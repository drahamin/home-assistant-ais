#!/usr/bin/env python3
"""Isolated Baiamonte CloudConnexa egress connector and Ingress status API."""

from __future__ import annotations

import ipaddress
import base64
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request


APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")
DATA_DIR = Path(os.environ.get("EGRESS_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("EGRESS_OPTIONS_FILE", DATA_DIR / "options.json"))
PROFILE_FILE = Path(os.environ.get("EGRESS_PROFILE_FILE", DATA_DIR / "client.ovpn"))
RUNTIME_PROFILE_FILE = Path(os.environ.get("EGRESS_RUNTIME_PROFILE_FILE", DATA_DIR / "runtime.ovpn"))
OPENVPN_LOG = DATA_DIR / "openvpn.log"
OPENVPN_STATUS = DATA_DIR / "openvpn-status.log"

GUARD_CHAIN = "RHM_BAI_GUARD"
FWD_CHAIN = "RHM_BAI_FWD"
NAT_CHAIN = "RHM_BAI_NAT"
MODES = {"probe_only", "https_probe", "tim_egress"}
DEFAULTS: dict[str, Any] = {
    "auto_start": False,
    "mode": "probe_only",
    "wan_interface": "auto",
    "allowed_source_cidrs": ["192.168.86.127/32"],
    "allowed_udp_ports": [500, 4500],
    "tim_destination_ips": [],
    "probe_destination_ips": [],
    "nat_enabled": True,
    "health_hostname": "epdg.epc.mnc001.mcc222.pub.3gppnetwork.org",
    "public_ip_url": "https://api.ipify.org",
    "public_country_url": "https://api.country.is/",
    "expected_country": "IT",
}

SAFE_FLAG_DIRECTIVES = {"client", "nobind", "persist-key", "persist-tun", "remote-random"}
SAFE_VALUE_DIRECTIVES = {
    "auth", "cipher", "connect-retry", "connect-retry-max", "connect-timeout",
    "data-ciphers", "data-ciphers-fallback", "dev", "explicit-exit-notify",
    "key-direction", "ping", "ping-exit", "ping-restart", "proto",
    "remote-cert-tls", "reneg-sec", "resolv-retry", "server-poll-timeout",
    "tls-version-max", "tls-version-min", "verb", "verify-x509-name",
}
REPEATABLE_DIRECTIVES = {"remote"}
INLINE_BLOCKS = {"ca", "cert", "key", "tls-auth", "tls-crypt"}


class ConfigurationError(ValueError):
    """An unsafe or invalid configuration was supplied."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _validated_https_url(value: Any, field: str) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?", text):
        raise ConfigurationError(f"{field} must be a plain HTTPS URL without query or credentials")
    expected_host = {
        "public_ip_url": "api.ipify.org",
        "public_country_url": "api.country.is",
    }[field]
    if urllib.parse.urlsplit(text).hostname != expected_host:
        raise ConfigurationError(f"{field} host must be {expected_host}")
    return text


def _validate_inline_block(tag: str, lines: list[str]) -> list[str]:
    """Validate and normalize inline key material before re-emitting it."""
    if not lines or any(not line or line != line.strip() or "<" in line or ">" in line for line in lines):
        raise ConfigurationError(f"inline block has invalid structure: {tag}")

    if tag in {"ca", "cert", "key"}:
        label = "CERTIFICATE" if tag in {"ca", "cert"} else None
        if tag == "key":
            allowed_key_labels = {"PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY"}
            opening = re.fullmatch(r"-----BEGIN ([A-Z0-9 ]+)-----", lines[0])
            if not opening or opening.group(1) not in allowed_key_labels:
                raise ConfigurationError("inline key must contain an unencrypted PEM private key")
            label = opening.group(1)
        if lines[0] != f"-----BEGIN {label}-----" or lines[-1] != f"-----END {label}-----":
            raise ConfigurationError(f"inline block has mismatched PEM boundaries: {tag}")
        body = lines[1:-1]
        if not body or any(len(line) > 76 or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", line) for line in body):
            raise ConfigurationError(f"inline block has invalid PEM data: {tag}")
        try:
            decoded = base64.b64decode("".join(body), validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ConfigurationError(f"inline block has invalid base64 data: {tag}") from exc
        if not decoded:
            raise ConfigurationError(f"inline block is empty: {tag}")
        return [lines[0], *body, lines[-1]]

    static_header = "-----BEGIN OpenVPN Static key V1-----"
    static_footer = "-----END OpenVPN Static key V1-----"
    if lines[0] != static_header or lines[-1] != static_footer:
        raise ConfigurationError(f"inline block has invalid static-key boundaries: {tag}")
    body = lines[1:-1]
    if len(body) != 16 or any(not re.fullmatch(r"[0-9a-fA-F]{32}", line) for line in body):
        raise ConfigurationError(f"inline block has invalid static-key data: {tag}")
    return [static_header, *[line.lower() for line in body], static_footer]


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    settings.update(raw)
    mode = str(settings.get("mode", "probe_only"))
    if mode not in MODES:
        raise ConfigurationError("mode must be probe_only, https_probe, or tim_egress")
    settings["mode"] = mode

    interface = str(settings.get("wan_interface", "auto"))
    if interface != "auto" and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ConfigurationError("wan_interface is invalid")
    settings["wan_interface"] = interface

    raw_sources = settings.get("allowed_source_cidrs")
    if not isinstance(raw_sources, list) or not raw_sources or len(raw_sources) > 16:
        raise ConfigurationError("allowed_source_cidrs must contain 1 to 16 narrow IPv4 networks")
    sources: list[str] = []
    for item in raw_sources:
        try:
            network = ipaddress.ip_network(str(item), strict=True)
        except ValueError as exc:
            raise ConfigurationError(f"invalid source CIDR: {item}") from exc
        if network.version != 4 or network.prefixlen != 32:
            raise ConfigurationError(f"source must be one reviewed IPv4 /32 address: {item}")
        if str(network) not in sources:
            sources.append(str(network))
    settings["allowed_source_cidrs"] = sources

    raw_ports = settings.get("allowed_udp_ports")
    if not isinstance(raw_ports, list) or not raw_ports or len(raw_ports) > 16:
        raise ConfigurationError("allowed_udp_ports must contain 1 to 16 ports")
    ports: list[int] = []
    for item in raw_ports:
        if isinstance(item, bool):
            raise ConfigurationError("UDP ports must be integers")
        try:
            port = int(item)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"invalid UDP port: {item}") from exc
        if port < 1 or port > 65535:
            raise ConfigurationError(f"UDP port is out of range: {port}")
        if port not in ports:
            ports.append(port)
    settings["allowed_udp_ports"] = sorted(ports)
    if settings["allowed_udp_ports"] != [500, 4500]:
        raise ConfigurationError("production UDP ports are fixed to 500 and 4500")

    def public_ipv4_list(field: str, limit: int = 16) -> list[str]:
        raw_addresses = settings.get(field, [])
        if not isinstance(raw_addresses, list) or len(raw_addresses) > limit:
            raise ConfigurationError(f"{field} must contain at most {limit} public IPv4 addresses")
        addresses: list[str] = []
        for item in raw_addresses:
            try:
                address = ipaddress.ip_address(str(item))
            except ValueError as exc:
                raise ConfigurationError(f"invalid {field} IP: {item}") from exc
            if address.version != 4 or not address.is_global:
                raise ConfigurationError(f"{field} must contain public IPv4 addresses: {item}")
            if str(address) not in addresses:
                addresses.append(str(address))
        return addresses

    tim_ips = public_ipv4_list("tim_destination_ips")
    if mode == "tim_egress" and not tim_ips:
        raise ConfigurationError("tim_egress requires at least one exact TIM destination IP")
    settings["tim_destination_ips"] = tim_ips

    probe_ips = public_ipv4_list("probe_destination_ips", limit=8)
    if mode == "https_probe" and not probe_ips:
        raise ConfigurationError("https_probe requires at least one exact public probe destination IP")
    settings["probe_destination_ips"] = probe_ips

    hostname = str(settings.get("health_hostname", "")).strip().lower()
    if len(hostname) > 253 or not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", hostname):
        raise ConfigurationError("health_hostname is invalid")
    settings["health_hostname"] = hostname
    settings["public_ip_url"] = _validated_https_url(settings.get("public_ip_url"), "public_ip_url")
    settings["public_country_url"] = _validated_https_url(settings.get("public_country_url"), "public_country_url")
    country = str(settings.get("expected_country", "IT")).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ConfigurationError("expected_country must be a two-letter code")
    settings["expected_country"] = country
    settings["auto_start"] = bool(settings.get("auto_start", False))
    settings["nat_enabled"] = bool(settings.get("nat_enabled", True))
    return settings


def validate_profile(content: bytes) -> str:
    if not content or len(content) > 1024 * 1024:
        raise ConfigurationError("profile must be between 1 byte and 1 MB")
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("profile must be UTF-8 text") from exc
    directives: list[tuple[str, list[str]]] = []
    blocks: dict[str, list[str]] = {}
    active_block: str | None = None
    block_lines: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if active_block:
            if raw_line == f"</{active_block}>":
                blocks[active_block] = _validate_inline_block(active_block, block_lines)
                active_block = None
                block_lines = []
            elif "<" in raw_line or ">" in raw_line:
                raise ConfigurationError("tag-like content inside inline OpenVPN block")
            else:
                block_lines.append(raw_line)
            continue

        opening = re.fullmatch(r"<([A-Za-z0-9_-]+)>", raw_line)
        if opening:
            tag = opening.group(1).lower()
            if tag not in INLINE_BLOCKS or tag in blocks:
                raise ConfigurationError(f"unsupported or duplicate inline block: {tag}")
            active_block = tag
            block_lines = []
            continue
        if "<" in raw_line or ">" in raw_line:
            raise ConfigurationError("malformed or unexpected inline OpenVPN tag")
        if not stripped or stripped.startswith(("#", ";")):
            continue
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise ConfigurationError("profile contains invalid quoting") from exc
        if not tokens:
            continue
        name = tokens[0].lower()
        if name.startswith("--"):
            raise ConfigurationError("command-line-style OpenVPN directives are not permitted")
        args = tokens[1:]
        if name in SAFE_FLAG_DIRECTIVES:
            if args:
                raise ConfigurationError(f"OpenVPN directive takes no arguments: {name}")
        elif name == "remote":
            if len(args) not in (2, 3) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", args[0]):
                raise ConfigurationError("remote must contain a hostname, port, and optional protocol")
            try:
                port = int(args[1])
            except ValueError as exc:
                raise ConfigurationError("remote port is invalid") from exc
            if port < 1 or port > 65535:
                raise ConfigurationError("remote port is out of range")
            if len(args) == 3 and args[2].lower() not in {"udp", "udp4", "tcp", "tcp4", "tcp-client", "tcp4-client"}:
                raise ConfigurationError("remote protocol is unsupported")
        elif name in SAFE_VALUE_DIRECTIVES:
            if not args:
                raise ConfigurationError(f"OpenVPN directive requires a value: {name}")
        else:
            raise ConfigurationError(f"OpenVPN directive is not allowlisted: {name}")
        if name in seen and name not in REPEATABLE_DIRECTIVES:
            raise ConfigurationError(f"duplicate OpenVPN directive is not permitted: {name}")
        seen.add(name)
        directives.append((name, args))

    if active_block:
        raise ConfigurationError(f"unterminated inline block: {active_block}")
    required_directives = {"client", "dev", "proto", "remote", "remote-cert-tls"}
    if not required_directives.issubset(seen) or not {"ca", "cert", "key"}.issubset(blocks):
        raise ConfigurationError("profile is not a complete certificate-based connector profile")
    values = {name: args for name, args in directives if name not in REPEATABLE_DIRECTIVES}
    if values["dev"] != ["tun"]:
        raise ConfigurationError("only dev tun profiles are supported")
    if values["proto"][0].lower() not in {"udp", "udp4", "tcp-client", "tcp4-client"}:
        raise ConfigurationError("profile protocol is unsupported")
    if values["remote-cert-tls"] != ["server"]:
        raise ConfigurationError("remote-cert-tls must be server")
    if "tls-auth" in blocks and "tls-crypt" in blocks:
        raise ConfigurationError("profile cannot contain both tls-auth and tls-crypt")

    canonical: list[str] = []
    for name, args in directives:
        canonical.append(shlex.join([name, *args]))
    for tag in ("ca", "cert", "key", "tls-auth", "tls-crypt"):
        if tag in blocks:
            canonical.extend([f"<{tag}>", *blocks[tag], f"</{tag}>"])
    return "\n".join(canonical) + "\n"


class GatewayManager:
    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = normalize_settings(read_json(OPTIONS_FILE))
        self._runner = runner
        self.process: subprocess.Popen[str] | None = None
        self._log_handle: Any = None
        self._lock = threading.RLock()
        self._stop_monitor = threading.Event()
        self.firewall_applied = False
        self.guard_installed = False
        self.session_ready = False
        self._forwarding_original: bool | None = None
        self._installed_routes: list[tuple[str, str]] = []
        self.route_snapshot: dict[str, list[str]] = {"before": [], "after": []}
        self._log_position = 0
        self.last_error = ""
        self.last_health: dict[str, Any] = {}
        self.started_at: float | None = None

    def run_command(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        if self._runner:
            return self._runner(args, check=check)
        return subprocess.run(args, check=check, text=True, capture_output=True, timeout=10)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def wan_interface(self) -> str:
        configured = self.settings["wan_interface"]
        if configured != "auto":
            return configured
        output = self.run_command(["ip", "-o", "route", "show", "default"]).stdout
        match = re.search(r"\bdev\s+(\S+)", output)
        if not match or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", match.group(1)):
            raise RuntimeError("could not determine WAN-facing interface")
        return match.group(1)

    def tunnel_interface(self) -> str | None:
        result = self.run_command(["ip", "-o", "link", "show"], check=False)
        for line in result.stdout.splitlines():
            match = re.match(r"\d+:\s+([^:@]+)", line)
            if match and re.fullmatch(r"(?:tun\d+|ovpn[A-Za-z0-9_.-]*)", match.group(1)):
                return match.group(1)
        return None

    def _iptables(self, table: str | None, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["iptables", "-w", "5"]
        if table:
            command.extend(["-t", table])
        command.extend(args)
        return self.run_command(command, check=check)

    def _remove_jump_references(self, table: str | None, parent: str, child: str) -> None:
        command = ["iptables", "-w", "5"]
        if table:
            command.extend(["-t", table])
        result = self.run_command(command + ["-S", parent], check=False)
        for line in result.stdout.splitlines():
            try:
                tokens = shlex.split(line)
            except ValueError:
                continue
            if len(tokens) >= 4 and tokens[:2] == ["-A", parent] and any(
                tokens[index:index + 2] == ["-j", child] for index in range(len(tokens) - 1)
            ):
                delete = list(tokens)
                delete[0] = "-D"
                self._iptables(table, delete, check=False)

    def _read_forwarding(self) -> bool:
        forwarding = Path("/proc/sys/net/ipv4/ip_forward")
        try:
            return forwarding.read_text(encoding="ascii").strip() == "1"
        except OSError as exc:
            raise RuntimeError("cannot read IPv4 forwarding state in app namespace") from exc

    def _set_forwarding(self, enabled: bool) -> None:
        # Docker/HAOS may expose this namespaced sysctl read-only even when it
        # already has the required value.  A no-op must therefore remain a
        # no-op; never request broader container privileges merely to rewrite
        # an identical value.
        if self._read_forwarding() is enabled:
            return
        forwarding = Path("/proc/sys/net/ipv4/ip_forward")
        expected = "1" if enabled else "0"
        try:
            forwarding.write_text(expected + "\n", encoding="ascii")
            if self._read_forwarding() is not enabled:
                raise RuntimeError("IPv4 forwarding state did not change as requested")
        except OSError as exc:
            raise RuntimeError(
                "IPv4 forwarding is disabled and the app namespace exposes its sysctl read-only"
            ) from exc

    def _remove_installed_routes(self) -> None:
        for source, tunnel in reversed(self._installed_routes):
            self.run_command(["ip", "-4", "route", "del", source, "dev", tunnel], check=False)
        self._installed_routes = []

    def _restore_forwarding(self) -> None:
        if self._forwarding_original is not None:
            self._set_forwarding(self._forwarding_original)

    def _remove_active_chains(self) -> None:
        self._remove_jump_references("nat", "POSTROUTING", NAT_CHAIN)
        for table, chain in ((None, FWD_CHAIN), ("nat", NAT_CHAIN)):
            self._iptables(table, ["-F", chain], check=False)
            self._iptables(table, ["-X", chain], check=False)
        self._remove_installed_routes()
        self.firewall_applied = False

    def establish_guard(self) -> None:
        """Install a deny-first tunnel guard before OpenVPN can create tun0."""
        if self.guard_installed:
            return
        self._forwarding_original = self._read_forwarding()
        self._remove_jump_references(None, "FORWARD", GUARD_CHAIN)
        self._remove_active_chains()
        self._iptables(None, ["-F", GUARD_CHAIN], check=False)
        self._iptables(None, ["-X", GUARD_CHAIN], check=False)
        self._iptables(None, ["-N", GUARD_CHAIN])
        self._iptables(None, ["-A", GUARD_CHAIN, "-i", "tun+", "-j", "DROP"])
        self._iptables(None, ["-A", GUARD_CHAIN, "-j", "RETURN"])
        self._iptables(None, ["-I", "FORWARD", "1", "-j", GUARD_CHAIN])
        if self._iptables(None, ["-C", "FORWARD", "-j", GUARD_CHAIN], check=False).returncode != 0:
            raise RuntimeError("tunnel guard jump could not be verified")
        self.guard_installed = True

    def deactivate_forwarding(self) -> None:
        """Return atomically to the guard DROP before removing active rules."""
        if self.guard_installed:
            replaced = self._iptables(
                None, ["-R", GUARD_CHAIN, "1", "-i", "tun+", "-j", "DROP"], check=False
            )
            if replaced.returncode != 0:
                # Never tear down the active chains if the protective replacement failed.
                self.firewall_applied = False
                raise RuntimeError("could not restore tunnel guard DROP")
        self.firewall_applied = False
        self._restore_forwarding()
        self._remove_active_chains()

    def cleanup_firewall(self) -> None:
        """Remove app-owned state only after the connector has stopped."""
        try:
            self.deactivate_forwarding()
        finally:
            self._remove_jump_references(None, "FORWARD", GUARD_CHAIN)
            self._iptables(None, ["-F", GUARD_CHAIN], check=False)
            self._iptables(None, ["-X", GUARD_CHAIN], check=False)
            self.guard_installed = False
            self.firewall_applied = False

    def _install_return_routes(self, tunnel: str) -> None:
        self.route_snapshot["before"] = self.run_command(
            ["ip", "-4", "route", "show", "table", "main"], check=False
        ).stdout.splitlines()
        self._installed_routes = []
        for source in self.settings["allowed_source_cidrs"]:
            result = self.run_command(["ip", "-4", "route", "add", source, "dev", tunnel], check=False)
            if result.returncode == 0:
                self._installed_routes.append((source, tunnel))
                continue
            existing = self.run_command(["ip", "-4", "route", "show", source], check=False).stdout
            if not any(source in line and re.search(rf"\bdev\s+{re.escape(tunnel)}\b", line) for line in existing.splitlines()):
                raise RuntimeError(f"reviewed return route could not be installed: {source}")
        self.route_snapshot["after"] = self.run_command(
            ["ip", "-4", "route", "show", "table", "main"], check=False
        ).stdout.splitlines()

    def apply_firewall(self) -> None:
        if self.settings["mode"] == "probe_only":
            self.deactivate_forwarding()
            return
        if not self.guard_installed:
            raise RuntimeError("tunnel guard is not installed")
        if not self.session_ready:
            raise RuntimeError("OpenVPN session is not authenticated and ready")
        tunnel = self.tunnel_interface()
        if not tunnel:
            raise RuntimeError("CloudConnexa tunnel interface is not available")
        wan = self.wan_interface()
        self.deactivate_forwarding()

        self._iptables(None, ["-N", FWD_CHAIN], check=False)
        self._iptables(None, ["-F", FWD_CHAIN])
        self._iptables("nat", ["-N", NAT_CHAIN], check=False)
        self._iptables("nat", ["-F", NAT_CHAIN])

        for source in self.settings["allowed_source_cidrs"]:
            self._iptables(None, [
                "-A", FWD_CHAIN, "-i", wan, "-o", tunnel, "-d", source,
                "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT",
            ])
            if self.settings["mode"] == "tim_egress":
                flows = [
                    ("udp", str(port), destination)
                    for destination in self.settings["tim_destination_ips"]
                    for port in self.settings["allowed_udp_ports"]
                ]
            else:
                flows = [("tcp", "443", destination) for destination in self.settings["probe_destination_ips"]]
            for protocol, port_text, destination in flows:
                destination_args = ["-d", destination] if destination else []
                self._iptables(None, [
                    "-A", FWD_CHAIN, "-i", tunnel, "-o", wan, "-s", source,
                    *destination_args, "-p", protocol, "--dport", port_text, "-m", "conntrack",
                    "--ctstate", "NEW,ESTABLISHED", "-j", "ACCEPT",
                ])
                if self.settings["nat_enabled"]:
                    self._iptables("nat", [
                        "-A", NAT_CHAIN, "-s", source, "-o", wan, *destination_args, "-p", protocol,
                        "--dport", port_text, "-j", "MASQUERADE",
                    ])
        self._iptables(None, ["-A", FWD_CHAIN, "-i", tunnel, "-j", "DROP"])
        self._iptables(None, ["-A", FWD_CHAIN, "-j", "RETURN"])
        self._iptables("nat", ["-A", NAT_CHAIN, "-j", "RETURN"])
        self._install_return_routes(tunnel)
        self._iptables("nat", ["-A", "POSTROUTING", "-j", NAT_CHAIN])
        self._iptables(None, ["-R", GUARD_CHAIN, "1", "-j", FWD_CHAIN])
        self._set_forwarding(True)
        checks = (
            self._iptables(None, ["-C", "FORWARD", "-j", GUARD_CHAIN], check=False),
            self._iptables(None, ["-C", GUARD_CHAIN, "-j", FWD_CHAIN], check=False),
            self._iptables("nat", ["-C", "POSTROUTING", "-j", NAT_CHAIN], check=False),
        )
        if any(result.returncode != 0 for result in checks) or not self._read_forwarding():
            self.deactivate_forwarding()
            raise RuntimeError("active forwarding state could not be verified")
        self.firewall_applied = True

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            if not PROFILE_FILE.exists():
                raise ConfigurationError("an approved connector profile has not been uploaded")
            canonical_profile = validate_profile(PROFILE_FILE.read_bytes())
            atomic_write(RUNTIME_PROFILE_FILE, canonical_profile)
            self.establish_guard()
            self.last_error = ""
            self.session_ready = False
            OPENVPN_LOG.parent.mkdir(parents=True, exist_ok=True)
            log_descriptor = os.open(OPENVPN_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self._log_handle = os.fdopen(log_descriptor, "a", encoding="utf-8", buffering=1)
            self._log_handle.seek(0, os.SEEK_END)
            self._log_position = self._log_handle.tell()
            command = [
                "openvpn", "--config", str(RUNTIME_PROFILE_FILE),
                "--route-nopull", "--pull-filter", "ignore", "dhcp-option",
                "--script-security", "1",
                "--status", str(OPENVPN_STATUS), "5", "--verb", "3",
            ]
            try:
                self.process = subprocess.Popen(
                    command, stdout=self._log_handle, stderr=subprocess.STDOUT,
                    text=True, start_new_session=True,
                )
            except Exception:
                self.cleanup_firewall()
                self._log_handle.close()
                self._log_handle = None
                raise
            self.started_at = time.time()
            self._stop_monitor.clear()
            threading.Thread(target=self._monitor, name="egress-monitor", daemon=True).start()

    def _read_session_events(self) -> None:
        try:
            with OPENVPN_LOG.open(encoding="utf-8", errors="replace") as handle:
                handle.seek(self._log_position)
                lines = handle.readlines()
                self._log_position = handle.tell()
        except OSError:
            return
        failure_markers = (
            "process restarting", "restart pause", "auth_failed", "tls error",
            "inactivity timeout", "exiting due to fatal error", "sigusr1",
        )
        for line in lines:
            lower = line.lower()
            if any(marker in lower for marker in failure_markers):
                self.session_ready = False
            if "initialization sequence completed" in lower:
                self.session_ready = True

    def _terminate_openvpn(self) -> None:
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _fail_closed(self, message: str) -> None:
        try:
            self.deactivate_forwarding()
        except Exception as guard_error:
            message = f"{message}; guard error: {guard_error}"
        self._terminate_openvpn()
        self.session_ready = False
        self.last_error = message

    def _monitor(self) -> None:
        deadline = time.time() + 45
        while not self._stop_monitor.wait(2):
            with self._lock:
                self._read_session_events()
                if not self.running:
                    try:
                        self.deactivate_forwarding()
                    except Exception as exc:
                        self.last_error = f"OpenVPN stopped; guard error: {exc}"
                    if self.process is not None:
                        self.last_error = self.last_error or f"OpenVPN exited with code {self.process.poll()}"
                    self.session_ready = False
                    return
                tunnel = self.tunnel_interface()
                ready = self.session_ready and bool(tunnel)
                if not ready and self.firewall_applied:
                    try:
                        self.deactivate_forwarding()
                    except Exception as exc:
                        self._fail_closed(f"session lost and forwarding could not be disabled: {exc}")
                        return
                if ready and self.settings["mode"] != "probe_only" and not self.firewall_applied:
                    try:
                        self.apply_firewall()
                    except Exception as exc:
                        self._fail_closed(f"forwarding setup failed closed: {exc}")
                        return
                if ready and self.settings["mode"] == "probe_only" and self.firewall_applied:
                    try:
                        self.deactivate_forwarding()
                    except Exception as exc:
                        self._fail_closed(f"probe-only guard failed: {exc}")
                        return
                if time.time() > deadline and not ready:
                    self._fail_closed("OpenVPN did not reach authenticated session readiness within 45 seconds")
                    return

    def stop(self) -> None:
        with self._lock:
            self._stop_monitor.set()
            self._terminate_openvpn()
            self.process = None
            self.session_ready = False
            self.cleanup_firewall()
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None

    def _fetch_text(self, url: str, maximum: int = 4096) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": f"Rahamin-Baiamonte-Egress/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=6) as response:
            if response.status != 200:
                raise RuntimeError(f"health endpoint returned HTTP {response.status}")
            return response.read(maximum + 1).decode("utf-8", "strict")[:maximum]

    def health(self) -> dict[str, Any]:
        tunnel = self.tunnel_interface()
        dns: dict[str, Any]
        try:
            addresses = sorted({
                item[4][0] for item in socket.getaddrinfo(
                    self.settings["health_hostname"], 4500, type=socket.SOCK_DGRAM
                )
            })
            dns = {"ok": bool(addresses), "addresses": addresses[:8]}
        except socket.gaierror as exc:
            dns = {"ok": False, "error": str(exc)}

        local_egress: dict[str, Any] = {"ok": False}
        try:
            public_ip = str(ipaddress.ip_address(self._fetch_text(self.settings["public_ip_url"]).strip()))
            country_payload = self._fetch_text(self.settings["public_country_url"])
            try:
                country_data = json.loads(country_payload)
                country = str(country_data.get("country", "")).upper()
            except (json.JSONDecodeError, AttributeError):
                country = country_payload.strip().upper()
            local_egress = {
                "ok": True,
                "public_ip": public_ip,
                "country": country or None,
                "expected_country": self.settings["expected_country"],
                "country_matches": country == self.settings["expected_country"],
                "meaning": "Baiamonte app local WAN only; not proof of forwarded traffic",
            }
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            local_egress = {"ok": False, "error": str(exc)}

        result = {
            "checked_at": int(time.time()),
            "tunnel": {
                "ok": self.running and self.session_ready and bool(tunnel),
                "process_running": self.running,
                "interface_present": bool(tunnel),
                "session_ready": self.session_ready,
                "interface": tunnel if self.running else None,
            },
            "dns": dns,
            "local_egress": local_egress,
            "forwarded_egress": {
                "status": "external_probe_required",
                "meaning": "Must be tested from an approved remote source and matched to rule counters",
            },
            "tim_registration": {
                "status": "handset_test_required",
                "meaning": "Tunnel, DNS, and Italian IP do not prove TIM registration",
            },
        }
        self.last_health = result
        return result

    def sanitized_log(self, lines: int = 100) -> list[str]:
        try:
            recent = deque(OPENVPN_LOG.open(encoding="utf-8", errors="replace"), maxlen=lines)
        except OSError:
            return []
        output = []
        for line in recent:
            clean = re.sub(r"(?i)(token|password|secret)=\S+", r"\1=[redacted]", line.rstrip())
            clean = re.sub(r"(?i)(setenv\s+username\s+).+", r"\1[redacted]", clean)
            output.append(clean)
        return output

    def firewall_counters(self) -> dict[str, list[str]]:
        """Return packet/byte rows for app-owned chains only."""
        counters: dict[str, list[str]] = {"guard": [], "forward": [], "nat": []}
        for label, table, chain in (
            ("guard", None, GUARD_CHAIN),
            ("forward", None, FWD_CHAIN),
            ("nat", "nat", NAT_CHAIN),
        ):
            command = ["iptables", "-w", "5"]
            if table:
                command.extend(["-t", table])
            result = self.run_command(command + ["-L", chain, "-n", "-v", "-x"], check=False)
            if result.returncode == 0:
                counters[label] = [
                    line.strip() for line in result.stdout.splitlines()
                    if line.strip() and not line.startswith("Chain ") and "pkts" not in line
                ]
        return counters

    def status(self) -> dict[str, Any]:
        tunnel = self.tunnel_interface()
        return {
            "app_version": APP_VERSION,
            "running": self.running,
            "profile_present": PROFILE_FILE.exists(),
            "mode": self.settings["mode"],
            "tunnel": {
                "connected": self.running and self.session_ready and bool(tunnel),
                "process_running": self.running,
                "interface_present": bool(tunnel),
                "session_ready": self.session_ready,
                "interface": tunnel if self.running else None,
            },
            "forwarding": {
                "applied": self.firewall_applied,
                "allowed_source_cidrs": self.settings["allowed_source_cidrs"],
                "allowed_udp_ports": self.settings["allowed_udp_ports"],
                "tim_destination_ips": self.settings["tim_destination_ips"],
                "probe_destination_ips": self.settings["probe_destination_ips"],
                "nat_enabled": self.settings["nat_enabled"],
                "guard_installed": self.guard_installed,
                "route_snapshot": self.route_snapshot,
                "counters": self.firewall_counters() if self.guard_installed else {
                    "guard": [], "forward": [], "nat": []
                },
            },
            "last_error": self.last_error,
            "last_health": self.last_health,
            "truth": {
                "tunnel": "OpenVPN process and tunnel-interface state",
                "dns": "Name resolution only",
                "local_egress": "This container's direct Starlink path only",
                "forwarded_egress": "Requires a remote exact-host test plus counters",
                "tim_registration": "Requires the physical TIM handset",
            },
            "recent_log": self.sanitized_log(),
        }


app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
manager = GatewayManager()


@app.before_request
def ingress_only():
    allowed = {"172.30.32.2", "127.0.0.1", "::1"}
    if os.environ.get("EGRESS_ALLOW_ANY_UI") != "1" and request.remote_addr not in allowed:
        return jsonify({"error": "Home Assistant Ingress access required"}), 403
    return None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    return jsonify(manager.status())


@app.post("/api/health")
def api_health():
    return jsonify(manager.health())


@app.post("/api/profile")
def api_profile():
    if manager.running:
        return jsonify({"error": "stop the connector before replacing its profile"}), 409
    uploaded = request.files.get("profile")
    if not uploaded:
        return jsonify({"error": "profile file is required"}), 400
    try:
        profile = validate_profile(uploaded.read(1024 * 1024 + 1))
        atomic_write(PROFILE_FILE, profile)
    except ConfigurationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"stored": True, "restart_required": manager.running})


@app.post("/api/start")
def api_start():
    try:
        manager.start()
        return jsonify(manager.status())
    except (ConfigurationError, RuntimeError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/stop")
def api_stop():
    manager.stop()
    return jsonify(manager.status())


def shutdown(_signum: int, _frame: Any) -> None:
    manager.stop()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if manager.settings["auto_start"]:
        try:
            manager.start()
        except (ConfigurationError, RuntimeError, OSError) as exc:
            manager.last_error = str(exc)
    app.run(host="0.0.0.0", port=8100, threaded=True)
