"""Local-first Home Assistant router for paired Legrand/Netatmo entities."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    import websocket
except ImportError:  # Allows pure routing tests outside the app container.
    websocket = None

TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_HTTP = os.environ.get("NETATMO_BRIDGE_CORE_HTTP", "http://supervisor/core/api")
CORE_WS = os.environ.get("NETATMO_BRIDGE_CORE_WS", "ws://supervisor/core/websocket")
OPTIONS_PATH = Path(os.environ.get("NETATMO_BRIDGE_OPTIONS", "/data/options.json"))
WEB_ROOT = Path(os.environ.get("NETATMO_BRIDGE_WEB", "/web"))
STARTED_AT = time.time()
RUNNING = True
WAKE = threading.Event()
LOCK = threading.RLock()
CONTROL_DOMAINS = {"switch", "light", "cover", "fan", "lock", "climate"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_options() -> dict[str, Any]:
    defaults = {
        "prefer_local": True,
        "cloud_fallback": True,
        "auto_pair": True,
        "poll_interval_seconds": 10,
        "local_platforms": "homekit_controller,matter",
        "cloud_platforms": "netatmo",
        "device_pairs": [],
    }
    try:
        supplied = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(supplied, dict):
            defaults.update(supplied)
    except (OSError, json.JSONDecodeError):
        pass
    defaults["poll_interval_seconds"] = max(5, int(defaults["poll_interval_seconds"]))
    return defaults


def platform_set(value: Any) -> set[str]:
    return {part.strip().lower() for part in str(value).split(",") if part.strip()}


def available(state: dict[str, Any] | None) -> bool:
    return bool(state and str(state.get("state", "")).lower() not in {"", "unknown", "unavailable"})


def friendly_name(state: dict[str, Any]) -> str:
    return str(state.get("attributes", {}).get("friendly_name") or state.get("entity_id", ""))


def match_key(state: dict[str, Any]) -> tuple[str, str]:
    entity_id = str(state.get("entity_id", ""))
    domain = entity_id.partition(".")[0]
    name = friendly_name(state).casefold()
    name = re.sub(r"\b(netatmo|homekit|matter|local|cloud)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    return domain, name


@dataclass
class Route:
    name: str
    local_entity: str | None = None
    cloud_entity: str | None = None
    local_available: bool = False
    cloud_available: bool = False
    active_path: str = "offline"
    state: str = "unavailable"
    domain: str = ""


class HomeAssistantClient:
    def __init__(self, token: str = TOKEN, http_base: str = CORE_HTTP, ws_url: str = CORE_WS):
        self.token = token
        self.http_base = http_base.rstrip("/")
        self.ws_url = ws_url

    def request(self, path: str, method: str = "GET", payload: Any = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.http_base}/{path.lstrip('/')}", data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def states(self) -> list[dict[str, Any]]:
        result = self.request("states")
        return result if isinstance(result, list) else []

    def registry(self, kind: str = "entity") -> list[dict[str, Any]]:
        if websocket is None:
            raise RuntimeError("websocket-client is not installed")
        ws = websocket.create_connection(self.ws_url, timeout=8)
        try:
            hello = json.loads(ws.recv())
            if hello.get("type") != "auth_required":
                raise RuntimeError("unexpected Home Assistant WebSocket greeting")
            ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(ws.recv())
            if auth.get("type") != "auth_ok":
                raise PermissionError("Home Assistant rejected the app token")
            if kind not in {"entity", "device"}:
                raise ValueError("unsupported Home Assistant registry kind")
            ws.send(json.dumps({"id": 1, "type": f"config/{kind}_registry/list"}))
            response = json.loads(ws.recv())
            if not response.get("success"):
                raise RuntimeError("Home Assistant entity registry request failed")
            result = response.get("result", [])
            return result if isinstance(result, list) else []
        finally:
            ws.close()

    def call_service(self, domain: str, service: str, entity_id: str, data: dict[str, Any]) -> Any:
        payload = dict(data)
        payload["entity_id"] = entity_id
        return self.request(f"services/{domain}/{service}", "POST", payload)

    def publish(self, entity_id: str, state: Any, attributes: dict[str, Any]) -> None:
        self.request(f"states/{entity_id}", "POST", {"state": state, "attributes": attributes})


class Bridge:
    def __init__(self, client: HomeAssistantClient):
        self.client = client
        self.options = load_options()
        self.status: dict[str, Any] = {
            "service": "starting", "health": "starting", "last_refresh": None,
            "last_error": None, "routes": [], "events": [], "registry_available": False,
        }

    def event(self, message: str, level: str = "info") -> None:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
        with LOCK:
            events = self.status.setdefault("events", [])
            events.insert(0, {"at": now_iso(), "level": level, "message": message})
            del events[50:]

    def _manual_routes(self, states: dict[str, dict[str, Any]]) -> list[Route]:
        routes = []
        for item in self.options.get("device_pairs", []):
            parts = [part.strip() for part in str(item).split("|", 2)]
            if len(parts) != 3:
                self.event(f"Ignored invalid manual pair: {item}", "warning")
                continue
            name, local_id, cloud_id = parts
            routes.append(self._route(
                name or local_id or cloud_id, local_id or None, cloud_id or None, states,
                prefer_local=bool(self.options.get("prefer_local", True)),
            ))
        return routes

    @staticmethod
    def _route(
        name: str, local_id: str | None, cloud_id: str | None,
        states: dict[str, dict[str, Any]], prefer_local: bool = True,
    ) -> Route:
        local_state = states.get(local_id or "")
        cloud_state = states.get(cloud_id or "")
        local_ok, cloud_ok = available(local_state), available(cloud_state)
        if local_ok and (prefer_local or not cloud_ok):
            active, chosen = "local", local_state
        elif cloud_ok:
            active, chosen = "cloud", cloud_state
        else:
            active, chosen = "offline", local_state or cloud_state or {}
        chosen_id = local_id or cloud_id or ""
        return Route(
            name=name, local_entity=local_id, cloud_entity=cloud_id,
            local_available=local_ok, cloud_available=cloud_ok, active_path=active,
            state=str(chosen.get("state", "unavailable")), domain=chosen_id.partition(".")[0],
        )

    @staticmethod
    def _device_keys(
        entity_id: str,
        entities: dict[str, dict[str, Any]],
        devices: dict[str, dict[str, Any]],
    ) -> set[str]:
        device = devices.get(str(entities.get(entity_id, {}).get("device_id") or ""), {})
        keys = set()
        serial = str(device.get("serial_number") or "").strip().casefold()
        if serial:
            keys.add(serial)
        for identifier in device.get("identifiers") or []:
            if isinstance(identifier, (list, tuple)) and len(identifier) >= 2:
                value = str(identifier[1]).strip().casefold()
                if value:
                    keys.add(value)
        return keys

    def build_routes(
        self,
        all_states: list[dict[str, Any]],
        registry: list[dict[str, Any]],
        device_registry: list[dict[str, Any]] | None = None,
    ) -> list[Route]:
        states = {str(item.get("entity_id")): item for item in all_states if item.get("entity_id")}
        routes = self._manual_routes(states)
        used = {entity for route in routes for entity in (route.local_entity, route.cloud_entity) if entity}
        if not self.options.get("auto_pair", True):
            return routes

        local_platforms = platform_set(self.options.get("local_platforms"))
        cloud_platforms = platform_set(self.options.get("cloud_platforms"))
        entity_records = {str(item.get("entity_id")): item for item in registry}
        device_records = {str(item.get("id")): item for item in (device_registry or [])}
        platforms = {entity_id: str(item.get("platform", "")).lower() for entity_id, item in entity_records.items()}
        locals_by_key: dict[tuple[str, str], list[str]] = {}
        clouds_by_key: dict[tuple[str, str], list[str]] = {}
        for entity_id, state in states.items():
            if entity_id in used:
                continue
            if entity_id.partition(".")[0] not in CONTROL_DOMAINS:
                continue
            platform = platforms.get(entity_id, "")
            if platform in local_platforms:
                locals_by_key.setdefault(match_key(state), []).append(entity_id)
            elif platform in cloud_platforms:
                clouds_by_key.setdefault(match_key(state), []).append(entity_id)

        # HomeKit and Netatmo use different names and sometimes different entity
        # domains for the same Legrand accessory. Their device records retain the
        # same hardware serial, which is the safest automatic pairing key.
        local_ids = [entity for values in locals_by_key.values() for entity in values]
        cloud_ids = [entity for values in clouds_by_key.values() for entity in values]
        for local_id in local_ids:
            if local_id in used:
                continue
            local_keys = self._device_keys(local_id, entity_records, device_records)
            for cloud_id in cloud_ids:
                if cloud_id in used:
                    continue
                if local_keys & self._device_keys(cloud_id, entity_records, device_records):
                    routes.append(self._route(
                        friendly_name(states.get(cloud_id, {})), local_id, cloud_id, states,
                        prefer_local=bool(self.options.get("prefer_local", True)),
                    ))
                    used.update({local_id, cloud_id})
                    break

        keys = sorted(set(locals_by_key) | set(clouds_by_key))
        for key in keys:
            local_ids = [entity for entity in locals_by_key.get(key, []) if entity not in used]
            cloud_ids = [entity for entity in clouds_by_key.get(key, []) if entity not in used]
            count = max(len(local_ids), len(cloud_ids))
            for index in range(count):
                local_id = local_ids[index] if index < len(local_ids) else None
                cloud_id = cloud_ids[index] if index < len(cloud_ids) else None
                state = states.get(local_id or cloud_id or "", {})
                routes.append(self._route(
                    friendly_name(state), local_id, cloud_id, states,
                    prefer_local=bool(self.options.get("prefer_local", True)),
                ))
        return routes

    def refresh(self) -> None:
        try:
            self.options = load_options()
            all_states = self.client.states()
            registry = self.client.registry()
            device_registry = self.client.registry("device")
            routes = self.build_routes(all_states, registry, device_registry)
            local_count = sum(route.local_available for route in routes)
            cloud_count = sum(route.cloud_available for route in routes)
            dual_count = sum(bool(route.local_entity and route.cloud_entity) for route in routes)
            health = "local" if local_count else ("cloud" if cloud_count else "offline")
            with LOCK:
                self.status.update({
                    "service": "running", "health": health, "last_refresh": now_iso(),
                    "last_error": None, "registry_available": True,
                    "routes": [asdict(route) for route in routes],
                    "counts": {"total": len(routes), "local": local_count, "cloud": cloud_count, "paired": dual_count},
                    "prefer_local": bool(self.options.get("prefer_local", True)),
                    "cloud_fallback": bool(self.options.get("cloud_fallback", True)),
                    "uptime_seconds": int(time.time() - STARTED_AT),
                })
            self.publish_summary()
        except Exception as exc:
            message = f"Refresh failed: {exc}"
            self.event(message, "error")
            with LOCK:
                self.status.update({"service": "running", "health": "error", "last_error": str(exc)})

    def publish_summary(self) -> None:
        counts = self.status.get("counts", {})
        attributes = {
            "friendly_name": "Baiamonte Netatmo Route",
            "icon": "mdi:home-switch",
            "local_available": counts.get("local", 0),
            "cloud_available": counts.get("cloud", 0),
            "paired_devices": counts.get("paired", 0),
            "total_devices": counts.get("total", 0),
            "last_refresh": self.status.get("last_refresh"),
        }
        try:
            self.client.publish("sensor.baiamonte_netatmo_route", self.status.get("health", "unknown"), attributes)
        except Exception as exc:
            self.event(f"Could not publish bridge sensor: {exc}", "warning")

    def control(self, target: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
        with LOCK:
            routes = list(self.status.get("routes", []))
        route = next((item for item in routes if target in {item.get("name"), item.get("local_entity"), item.get("cloud_entity")}), None)
        if not route:
            raise KeyError("target is not a managed Netatmo route")
        if not re.fullmatch(r"[a-z_]+", service):
            raise ValueError("invalid Home Assistant service")

        prefer_local = bool(self.options.get("prefer_local", True))
        candidates = []
        if prefer_local:
            if route.get("local_entity") and route.get("local_available"):
                candidates.append(("local", route["local_entity"]))
            if self.options.get("cloud_fallback", True) and route.get("cloud_entity") and route.get("cloud_available"):
                candidates.append(("cloud", route["cloud_entity"]))
        else:
            if route.get("cloud_entity") and route.get("cloud_available"):
                candidates.append(("cloud", route["cloud_entity"]))
            if route.get("local_entity") and route.get("local_available"):
                candidates.append(("local", route["local_entity"]))
        if not candidates:
            raise ConnectionError("neither local nor cloud route is available")

        errors = []
        for path, entity_id in candidates:
            try:
                domain = str(entity_id).partition(".")[0]
                if not domain:
                    raise ValueError("managed route has an invalid entity id")
                self.client.call_service(domain, service, entity_id, data)
                self.event(f"{route['name']}: {service} sent through {path}")
                WAKE.set()
                return {"ok": True, "path": path, "entity_id": entity_id}
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                self.event(f"{route['name']}: {path} command failed ({exc})", "warning")
        raise ConnectionError("; ".join(errors))

    def snapshot(self) -> dict[str, Any]:
        with LOCK:
            return json.loads(json.dumps(self.status))


BRIDGE = Bridge(HomeAssistantClient())


class Handler(BaseHTTPRequestHandler):
    server_version = "BaiamonteNetatmo/0.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def json_response(self, status: int, payload: Any) -> None:
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
            self.json_response(200, BRIDGE.snapshot())
            return
        if path == "/api/health":
            state = BRIDGE.snapshot()
            self.json_response(200 if state.get("service") == "running" else 503, {"service": state.get("service"), "health": state.get("health")})
            return
        if path == "/api/refresh":
            WAKE.set()
            self.json_response(202, {"ok": True})
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/control":
            self.json_response(404, {"error": "not found"})
            return
        try:
            size = min(int(self.headers.get("Content-Length", "0")), 65536)
            payload = json.loads(self.rfile.read(size) or b"{}")
            result = BRIDGE.control(str(payload.get("target", "")), str(payload.get("service", "")), payload.get("data") or {})
            self.json_response(200, result)
        except (ValueError, KeyError, ConnectionError, json.JSONDecodeError) as exc:
            self.json_response(400, {"error": str(exc)})
        except Exception as exc:
            self.json_response(502, {"error": str(exc)})

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path).lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def monitor() -> None:
    while RUNNING:
        BRIDGE.refresh()
        WAKE.wait(int(BRIDGE.options.get("poll_interval_seconds", 10)))
        WAKE.clear()


def stop(_signum: int, _frame: Any) -> None:
    global RUNNING
    RUNNING = False
    WAKE.set()


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not TOKEN:
        BRIDGE.event("SUPERVISOR_TOKEN is missing; Home Assistant API access will fail", "error")
    worker = threading.Thread(target=monitor, daemon=True, name="route-monitor")
    worker.start()
    server = ThreadingHTTPServer(("0.0.0.0", 8096), Handler)
    server.timeout = 1
    BRIDGE.event("Baiamonte Netatmo listening on port 8096")
    while RUNNING:
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    main()
