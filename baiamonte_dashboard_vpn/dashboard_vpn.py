"""Always-on CloudConnexa connector and local setup UI for Baiamonte dashboards."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from base64 import b64decode
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_API = os.environ.get("DASHBOARD_VPN_CORE_API", "http://supervisor/core/api")
OPTIONS_PATH = Path(os.environ.get("DASHBOARD_VPN_OPTIONS", "/data/options.json"))
PROFILE_PATH = Path(os.environ.get("DASHBOARD_VPN_PROFILE", "/data/connector.ovpn"))
STATUS_PATH = Path(os.environ.get("DASHBOARD_VPN_STATUS", "/data/openvpn.status"))
WEB_ROOT = Path(os.environ.get("DASHBOARD_VPN_WEB", "/web"))
PORT = int(os.environ.get("DASHBOARD_VPN_PORT", "8098"))
PROFILE_BASE_URL = os.environ.get(
    "DASHBOARD_VPN_PROFILE_BASE_URL",
    "https://network-gateway.openvpn.com/network-gate/api/v1/profiles/",
)

LOCK = threading.RLock()
WAKE = threading.Event()
RUNNING = True
PROXY: subprocess.Popen[str] | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "dashboard_url": "http://dashboard.baiamonte:8123",
        "reconnect_delay_seconds": 10,
        "publish_to_home_assistant": True,
    }
    try:
        supplied = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(supplied, dict):
            options.update(supplied)
    except (OSError, json.JSONDecodeError):
        pass
    options["reconnect_delay_seconds"] = max(5, int(options["reconnect_delay_seconds"]))
    return options


def valid_profile(profile: str) -> tuple[bool, str]:
    normalized = profile.replace("\r\n", "\n").strip()
    if len(normalized) < 100:
        return False, "The connector profile is incomplete."
    directives = {
        line.split(None, 1)[0].lower()
        for line in normalized.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";", "<"))
    }
    if "client" not in directives:
        return False, "This is not an OpenVPN client profile."
    if "remote" not in directives:
        return False, "The profile has no CloudConnexa remote endpoint."
    if re.search(r"(?mi)^\s*(script-security|up|down|route-up|plugin)\b", normalized):
        return False, "Profiles containing executable scripts or plugins are not accepted."
    return True, ""


def store_profile(profile: str) -> None:
    ok, error = valid_profile(profile)
    if not ok:
        raise ValueError(error)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROFILE_PATH.with_suffix(".tmp")
    temporary.write_text(profile.replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(PROFILE_PATH)
    PROFILE_PATH.chmod(0o600)


def profile_from_setup_token(token: str, base_url: str = PROFILE_BASE_URL) -> str:
    """Download and decrypt a CloudConnexa Connector profile."""
    token = token.strip()
    if len(token) <= 40 or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", token):
        raise ValueError("The CloudConnexa setup token is invalid.")
    encoded_key, file_reference = token[:-40], token[-40:]
    try:
        password = b64decode(encoded_key, validate=True)
    except ValueError as exc:
        raise ValueError("The CloudConnexa setup token has an invalid key.") from exc
    if not password or not re.fullmatch(r"[A-Za-z0-9_-]{40}", file_reference):
        raise ValueError("The CloudConnexa setup token has an invalid profile reference.")

    request = urllib.request.Request(
        base_url.rstrip("/") + "/" + file_reference,
        headers={"User-Agent": "openvpn-connector-setup"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = b64decode(response.read())
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ValueError("The CloudConnexa Connector profile could not be downloaded.") from exc
    if len(payload) < 49:
        raise ValueError("CloudConnexa returned an incomplete Connector profile.")

    salt, ciphertext, tag = payload[:32], payload[32:-16], payload[-16:]
    key_material = PBKDF2HMAC(
        algorithm=SHA256(), length=44, salt=salt, iterations=25000,
    ).derive(password)
    try:
        decryptor = Cipher(
            algorithms.AES(key_material[:32]), modes.GCM(key_material[32:44], tag),
        ).decryptor()
        profile = (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("The CloudConnexa setup token could not decrypt the profile.") from exc
    ok, error = valid_profile(profile)
    if not ok:
        raise ValueError(error)
    return profile


class Connector:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.profile_mtime: float | None = None
        self.started_at: str | None = None
        self.connected_at: str | None = None
        self.last_error: str | None = None
        self.last_log = "Waiting for a CloudConnexa connector profile."
        self.events: list[dict[str, str]] = []
        self.options = load_options()

    def event(self, message: str, level: str = "info") -> None:
        safe = re.sub(r"(?i)(auth-token|token|password)\s+\S+", r"\1 [redacted]", message)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {safe}", flush=True)
        with LOCK:
            self.last_log = safe
            self.events.insert(0, {"at": now_iso(), "level": level, "message": safe})
            del self.events[30:]

    def command(self) -> list[str]:
        return [
            "openvpn", "--config", str(PROFILE_PATH),
            "--status", str(STATUS_PATH), "5", "--status-version", "2",
            "--auth-nocache", "--persist-key", "--persist-tun",
            "--pull-filter", "ignore", "redirect-gateway",
            "--verb", "3",
        ]

    def start(self) -> None:
        self.stop()
        self.profile_mtime = PROFILE_PATH.stat().st_mtime
        self.started_at = now_iso()
        self.connected_at = None
        self.last_error = None
        self.event("Starting the Baiamonte CloudConnexa connector.")
        self.process = subprocess.Popen(
            self.command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if "Initialization Sequence Completed" in line:
                self.connected_at = now_iso()
                self.last_error = None
                self.event("CloudConnexa tunnel connected.")
            elif "AUTH_FAILED" in line or "Options error" in line or "ERROR:" in line:
                self.last_error = line[-300:]
                self.event(self.last_error, "error")
            elif any(marker in line for marker in ("Restart pause", "SIGUSR1", "Connection reset")):
                self.event(line[-300:], "warning")

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def is_connected(self) -> bool:
        try:
            text = STATUS_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "CONNECTED,SUCCESS" in text or "Initialization Sequence Completed" in self.last_log

    def snapshot(self) -> dict[str, Any]:
        process_running = bool(self.process and self.process.poll() is None)
        profile_installed = PROFILE_PATH.is_file()
        connected = process_running and self.is_connected()
        state = "connected" if connected else ("connecting" if process_running else ("ready" if profile_installed else "setup"))
        return {
            "state": state,
            "healthy": connected,
            "profile_installed": profile_installed,
            "process_running": process_running,
            "connected": connected,
            "dashboard_url": str(self.options.get("dashboard_url", "")),
            "started_at": self.started_at,
            "connected_at": self.connected_at,
            "last_error": self.last_error,
            "last_log": self.last_log,
            "events": list(self.events),
        }

    def publish(self) -> None:
        if not TOKEN or not self.options.get("publish_to_home_assistant", True):
            return
        status = self.snapshot()
        attributes = {
            "friendly_name": "Baiamonte Dashboard VPN",
            "icon": "mdi:television-lock",
            "connected": status["connected"],
            "profile_installed": status["profile_installed"],
            "dashboard_url": status["dashboard_url"],
            "connected_at": status["connected_at"],
            "last_error": status["last_error"],
        }
        request = urllib.request.Request(
            f"{CORE_API}/states/sensor.baiamonte_dashboard_vpn",
            data=json.dumps({"state": status["state"], "attributes": attributes}).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5).read()
        except (OSError, urllib.error.URLError):
            pass

    def supervise(self) -> None:
        global RUNNING
        while RUNNING:
            self.options = load_options()
            if PROFILE_PATH.is_file():
                mtime = PROFILE_PATH.stat().st_mtime
                stopped = not self.process or self.process.poll() is not None
                if stopped or self.profile_mtime != mtime:
                    try:
                        self.start()
                    except (OSError, subprocess.SubprocessError) as exc:
                        self.last_error = str(exc)
                        self.event(f"Connector start failed: {exc}", "error")
            elif self.process:
                self.stop()
            self.publish()
            delay = int(self.options.get("reconnect_delay_seconds", 10))
            WAKE.wait(delay)
            WAKE.clear()
        self.stop()


CONNECTOR = Connector()


class Handler(BaseHTTPRequestHandler):
    server_version = "BaiamonteDashboardVPN/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            self._json(CONNECTOR.snapshot())
            return
        if path == "/api/health":
            status = CONNECTOR.snapshot()
            self._json({"status": "ok" if status["connected"] else "setup", "connected": status["connected"]})
            return
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self.send_error(404)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/setup-token":
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 16384)
                payload = json.loads(self.rfile.read(length))
                profile = profile_from_setup_token(str(payload.get("token", "")))
                store_profile(profile)
                CONNECTOR.event("The CloudConnexa setup token installed a new connector profile.")
                WAKE.set()
                self._json({"ok": True})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/profile":
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 262144)
                payload = json.loads(self.rfile.read(length))
                profile = str(payload.get("profile", ""))
                store_profile(profile)
                CONNECTOR.event("A new connector profile was stored securely.")
                WAKE.set()
                self._json({"ok": True})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if path == "/api/reconnect":
            CONNECTOR.stop()
            WAKE.set()
            self._json({"ok": True})
            return
        self.send_error(404)


def shutdown(*_: Any) -> None:
    global RUNNING
    RUNNING = False
    WAKE.set()


def ensure_proxy() -> None:
    global PROXY
    if PROXY and PROXY.poll() is None:
        return
    print("Starting the private Home Assistant dashboard proxy.", flush=True)
    PROXY = subprocess.Popen(["nginx", "-g", "daemon off;"])


def main() -> None:
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    ensure_proxy()
    worker = threading.Thread(target=CONNECTOR.supervise, daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.timeout = 1
    print(f"Baiamonte Dashboard VPN setup UI listening on {PORT}", flush=True)
    while RUNNING:
        server.handle_request()
        ensure_proxy()
    server.server_close()
    worker.join(timeout=7)
    if PROXY and PROXY.poll() is None:
        PROXY.terminate()
        try:
            PROXY.wait(timeout=5)
        except subprocess.TimeoutExpired:
            PROXY.kill()


if __name__ == "__main__":
    main()
