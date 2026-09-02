"""Local-first Home Assistant route and operations view for Tuya-family devices."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    import websocket
except ImportError:  # Allows routing tests to run without container dependencies.
    websocket = None


TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_HTTP = os.environ.get("TUYA_BRIDGE_CORE_HTTP", "http://supervisor/core/api")
CORE_WS = os.environ.get("TUYA_BRIDGE_CORE_WS", "ws://supervisor/core/websocket")
OPTIONS_PATH = Path(os.environ.get("TUYA_BRIDGE_OPTIONS", "/data/options.json"))
WEB_ROOT = Path(os.environ.get("TUYA_BRIDGE_WEB", "/web"))
STARTED_AT = time.time()
RUNNING = True
WAKE = threading.Event()
LOCK = threading.RLock()

UNAVAILABLE_STATES = {"", "unknown", "unavailable"}
SAFE_SERVICES: dict[str, set[str]] = {
    "switch": {"turn_on", "turn_off", "toggle"},
    "light": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "set_preset_mode"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position"},
    "climate": {"turn_on", "turn_off", "set_temperature", "set_hvac_mode", "set_preset_mode"},
    "humidifier": {"turn_on", "turn_off", "set_humidity", "set_mode"},
    "valve": {"open_valve", "close_valve", "stop_valve", "set_valve_position"},
    "vacuum": {"start", "stop", "pause", "return_to_base"},
    "siren": {"turn_on", "turn_off"},
    "select": {"select_option"},
    "number": {"set_value"},
}
SAFE_DATA_KEYS: dict[str, set[str]] = {
    "turn_on": {"brightness", "brightness_pct", "color_temp_kelvin", "rgb_color", "transition"},
    "set_percentage": {"percentage"},
    "set_preset_mode": {"preset_mode"},
    "set_cover_position": {"position"},
    "set_temperature": {"temperature", "target_temp_high", "target_temp_low", "hvac_mode"},
    "set_hvac_mode": {"hvac_mode"},
    "set_humidity": {"humidity"},
    "set_mode": {"mode"},
    "set_valve_position": {"position"},
    "select_option": {"option"},
    "set_value": {"value"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_options() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "prefer_local": True,
        "cloud_fallback": True,
        "controls_enabled": True,
        "auto_pair": True,
        "poll_interval_seconds": 10,
        "discover_local_platforms": "tuya_local,localtuya",
        "cloud_platforms": "tuya",
        "managed_entities": [],
        "device_pairs": [],
    }
    try:
        supplied = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(supplied, dict):
            defaults.update(supplied)
    except (OSError, json.JSONDecodeError):
        pass
    defaults["poll_interval_seconds"] = max(5, min(300, int(defaults["poll_interval_seconds"])))
    for key in ("managed_entities", "device_pairs"):
        if not isinstance(defaults.get(key), list):
            defaults[key] = []
    return defaults


def platform_set(value: Any) -> set[str]:
    return {part.strip().lower() for part in str(value).split(",") if part.strip()}


def is_available(state: dict[str, Any] | None) -> bool:
    return bool(state and str(state.get("state", "")).lower() not in UNAVAILABLE_STATES)


def friendly_name(state: dict[str, Any]) -> str:
    return str(state.get("attributes", {}).get("friendly_name") or state.get("entity_id", ""))


def match_key(state: dict[str, Any]) -> tuple[str, str]:
    entity_id = str(state.get("entity_id", ""))
    domain = entity_id.partition(".")[0]
    name = friendly_name(state).casefold()
    name = re.sub(r"\b(tuya|smart\s*life|matter|zigbee|zha|local|cloud|lan|wi-?fi)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    return domain, name


def safe_state(value: Any) -> str:
    text = str(value if value is not None else "unavailable")
    return text[:160]


@dataclass
class Route:
    name: str
    domain: str
    local_entity: str | None = None
    cloud_entity: str | None = None
    local_platform: str | None = None
    cloud_platform: str | None = None
    local_available: bool = False
    cloud_available: bool = False
    active_path: str = "offline"
    state: str = "unavailable"
    controls: list[str] | None = None


class HomeAssistantClient:
    def __init__(self, token: str = TOKEN, http_base: str = CORE_HTTP, ws_url: str = CORE_WS):
        self.token = token
        self.http_base = http_base.rstrip("/")
        self.ws_url = ws_url

    def request(self, path: str, method: str = "GET", payload: Any = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.http_base}/{path.lstrip('/')}",
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def states(self) -> list[dict[str, Any]]:
        result = self.request("states")
        return result if isinstance(result, list) else []

    def registry(self) -> list[dict[str, Any]]:
        if websocket is None:
            raise RuntimeError("websocket-client is not installed")
        ws = websocket.create_connection(self.ws_url, timeout=8)
        try:
            greeting = json.loads(ws.recv())
            if greeting.get("type") != "auth_required":
                raise RuntimeError("unexpected Home Assistant WebSocket greeting")
            ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(ws.recv())
            if auth.get("type") != "auth_ok":
                raise PermissionError("Home Assistant rejected the app token")
            ws.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
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
            "service": "starting",
            "health": "starting",
            "last_refresh": None,
            "last_error": None,
            "routes": [],
            "events": [],
            "registry_available": False,
        }

    def event(self, message: str, level: str = "info") -> None:
        clean = str(message).replace("\n", " ")[:500]
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {clean}", flush=True)
        with LOCK:
            events = self.status.setdefault("events", [])
            events.insert(0, {"at": now_iso(), "level": level, "message": clean})
            del events[50:]

    @staticmethod
    def _make_route(
        name: str,
        local_id: str | None,
        cloud_id: str | None,
        states: dict[str, dict[str, Any]],
        platforms: dict[str, str],
        prefer_local: bool,
    ) -> Route:
        local_state = states.get(local_id or "")
        cloud_state = states.get(cloud_id or "")
        local_ok, cloud_ok = is_available(local_state), is_available(cloud_state)
        if local_ok and (prefer_local or not cloud_ok):
            active, chosen = "local", local_state
        elif cloud_ok:
            active, chosen = "cloud", cloud_state
        else:
            active, chosen = "offline", local_state or cloud_state or {}
        chosen_id = local_id or cloud_id or ""
        domain = chosen_id.partition(".")[0]
        controls = sorted(SAFE_SERVICES.get(domain, set()))
        return Route(
            name=name[:120],
            domain=domain,
            local_entity=local_id,
            cloud_entity=cloud_id,
            local_platform=platforms.get(local_id or "") or None,
            cloud_platform=platforms.get(cloud_id or "") or None,
            local_available=local_ok,
            cloud_available=cloud_ok,
            active_path=active,
            state=safe_state(chosen.get("state", "unavailable")),
            controls=controls,
        )

    def _manual_routes(
        self,
        states: dict[str, dict[str, Any]],
        platforms: dict[str, str],
    ) -> list[Route]:
        routes = []
        for item in self.options.get("device_pairs", []):
            parts = [part.strip() for part in str(item).split("|", 2)]
            if len(parts) != 3:
                self.event(f"Ignored invalid manual pair: {item}", "warning")
                continue
            name, local_id, cloud_id = parts
            if local_id and local_id not in states:
                self.event(f"Manual local entity is not present: {local_id}", "warning")
            if cloud_id and cloud_id not in states:
                self.event(f"Manual cloud entity is not present: {cloud_id}", "warning")
            if local_id and cloud_id and local_id.partition(".")[0] != cloud_id.partition(".")[0]:
                self.event(f"Ignored cross-domain manual pair: {item}", "warning")
                continue
            routes.append(self._make_route(
                name or friendly_name(states.get(local_id or cloud_id, {})),
                local_id or None,
                cloud_id or None,
                states,
                platforms,
                bool(self.options.get("prefer_local", True)),
            ))
        return routes

    def build_routes(self, all_states: list[dict[str, Any]], registry: list[dict[str, Any]]) -> list[Route]:
        states = {str(item.get("entity_id")): item for item in all_states if item.get("entity_id")}
        platforms = {
            str(item.get("entity_id")): str(item.get("platform", "")).lower()
            for item in registry if item.get("entity_id")
        }
        routes = self._manual_routes(states, platforms)
        used = {entity for route in routes for entity in (route.local_entity, route.cloud_entity) if entity}
        prefer_local = bool(self.options.get("prefer_local", True))

        for entity_id in self.options.get("managed_entities", []):
            entity_id = str(entity_id).strip()
            if not entity_id or entity_id in used:
                continue
            state = states.get(entity_id)
            if not state:
                self.event(f"Managed entity is not present: {entity_id}", "warning")
                continue
            if platforms.get(entity_id, "") in platform_set(self.options.get("cloud_platforms")):
                self.event(f"Additional local entity belongs to a cloud platform: {entity_id}", "warning")
                continue
            routes.append(self._make_route(
                friendly_name(state), entity_id, None, states, platforms, prefer_local,
            ))
            used.add(entity_id)

        if not self.options.get("auto_pair", True):
            return sorted(routes, key=lambda route: route.name.casefold())

        local_platforms = platform_set(self.options.get("discover_local_platforms"))
        cloud_platforms = platform_set(self.options.get("cloud_platforms"))
        locals_by_key: dict[tuple[str, str], list[str]] = {}
        clouds_by_key: dict[tuple[str, str], list[str]] = {}
        for entity_id, state in states.items():
            if entity_id in used:
                continue
            platform = platforms.get(entity_id, "")
            if platform in local_platforms:
                locals_by_key.setdefault(match_key(state), []).append(entity_id)
            elif platform in cloud_platforms:
                clouds_by_key.setdefault(match_key(state), []).append(entity_id)

        for key in sorted(set(locals_by_key) | set(clouds_by_key)):
            local_ids = sorted(locals_by_key.get(key, []))
            cloud_ids = sorted(clouds_by_key.get(key, []))
            for index in range(max(len(local_ids), len(cloud_ids))):
                local_id = local_ids[index] if index < len(local_ids) else None
                cloud_id = cloud_ids[index] if index < len(cloud_ids) else None
                state = states.get(local_id or cloud_id or "", {})
                routes.append(self._make_route(
                    friendly_name(state), local_id, cloud_id, states, platforms, prefer_local,
                ))
        return sorted(routes, key=lambda route: route.name.casefold())

    def refresh(self) -> None:
        try:
            self.options = load_options()
            routes = self.build_routes(self.client.states(), self.client.registry())
            local_count = sum(route.local_available for route in routes)
            cloud_count = sum(route.cloud_available for route in routes)
            paired_count = sum(bool(route.local_entity and route.cloud_entity) for route in routes)
            offline_count = sum(route.active_path == "offline" for route in routes)
            if local_count:
                health = "local"
            elif cloud_count:
                health = "cloud"
            else:
                health = "offline"
            with LOCK:
                self.status.update({
                    "service": "running",
                    "health": health,
                    "last_refresh": now_iso(),
                    "last_error": None,
                    "registry_available": True,
                    "routes": [asdict(route) for route in routes],
                    "counts": {
                        "total": len(routes),
                        "local": local_count,
                        "cloud": cloud_count,
                        "paired": paired_count,
                        "offline": offline_count,
                    },
                    "prefer_local": bool(self.options.get("prefer_local", True)),
                    "cloud_fallback": bool(self.options.get("cloud_fallback", True)),
                    "controls_enabled": bool(self.options.get("controls_enabled", True)),
                    "uptime_seconds": int(time.time() - STARTED_AT),
                })
            self.publish_summary()
        except Exception as exc:
            self.event(f"Refresh failed: {exc}", "error")
            with LOCK:
                self.status.update({"service": "running", "health": "error", "last_error": safe_state(exc)})

    def publish_summary(self) -> None:
        counts = self.status.get("counts", {})
        attributes = {
            "friendly_name": "Baiamonte Tuya Route",
            "icon": "mdi:home-lightning-bolt-outline",
            "local_available": counts.get("local", 0),
            "cloud_available": counts.get("cloud", 0),
            "paired_devices": counts.get("paired", 0),
            "offline_devices": counts.get("offline", 0),
            "total_devices": counts.get("total", 0),
            "last_refresh": self.status.get("last_refresh"),
        }
        try:
            self.client.publish("sensor.baiamonte_tuya_route", self.status.get("health", "unknown"), attributes)
        except Exception as exc:
            self.event(f"Could not publish route sensor: {exc}", "warning")

    def control(self, target: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.options.get("controls_enabled", True):
            raise PermissionError("dashboard controls are disabled")
        with LOCK:
            routes = list(self.status.get("routes", []))
        route = next((item for item in routes if target in {
            item.get("name"), item.get("local_entity"), item.get("cloud_entity"),
        }), None)
        if not route:
            raise KeyError("target is not a managed Baiamonte Tuya route")
        domain = str(route.get("domain") or "")
        if service not in SAFE_SERVICES.get(domain, set()):
            raise ValueError(f"{service or 'empty service'} is not permitted for {domain or 'this target'}")
        if not isinstance(data, dict):
            raise ValueError("service data must be an object")
        unexpected = set(data) - SAFE_DATA_KEYS.get(service, set())
        if unexpected:
            raise ValueError(f"unsupported service data: {', '.join(sorted(unexpected))}")

        prefer_local = bool(self.options.get("prefer_local", True))
        candidates: list[tuple[str, str]] = []
        if prefer_local:
            if route.get("local_entity") and route.get("local_available"):
                candidates.append(("local", str(route["local_entity"])))
            if self.options.get("cloud_fallback", True) and route.get("cloud_entity") and route.get("cloud_available"):
                candidates.append(("cloud", str(route["cloud_entity"])))
        else:
            if route.get("cloud_entity") and route.get("cloud_available"):
                candidates.append(("cloud", str(route["cloud_entity"])))
            if route.get("local_entity") and route.get("local_available"):
                candidates.append(("local", str(route["local_entity"])))
        if not candidates:
            raise ConnectionError("no permitted route is available")

        failures = []
        for path, entity_id in candidates:
            try:
                self.client.call_service(domain, service, entity_id, data)
                self.event(f"{route['name']}: {service} sent through {path}")
                WAKE.set()
                return {"ok": True, "path": path, "entity_id": entity_id}
            except Exception as exc:
                failures.append(f"{path}: {safe_state(exc)}")
                self.event(f"{route['name']}: {path} command failed ({safe_state(exc)})", "warning")
        raise ConnectionError("; ".join(failures))

    def snapshot(self) -> dict[str, Any]:
        with LOCK:
            return json.loads(json.dumps(self.status))


BRIDGE = Bridge(HomeAssistantClient())


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "BaiamonteTuya/0.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'self'",
        )

    def json_response(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
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
            code = 200 if state.get("service") == "running" else 503
            self.json_response(code, {"service": state.get("service"), "health": state.get("health")})
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/refresh":
            if not self.valid_browser_request():
                self.json_response(403, {"error": "request rejected"})
                return
            WAKE.set()
            self.json_response(202, {"ok": True})
            return
        if path != "/api/control":
            self.json_response(404, {"error": "not found"})
            return
        if not self.valid_browser_request():
            self.json_response(403, {"error": "request rejected"})
            return
        try:
            declared = int(self.headers.get("Content-Length", "0"))
            if declared < 0 or declared > 16384:
                raise ValueError("request body is too large")
            payload = json.loads(self.rfile.read(declared) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            result = BRIDGE.control(
                str(payload.get("target", "")),
                str(payload.get("service", "")),
                payload.get("data") or {},
            )
            self.json_response(200, result)
        except PermissionError as exc:
            self.json_response(403, {"error": str(exc)})
        except (ValueError, KeyError, ConnectionError, json.JSONDecodeError) as exc:
            self.json_response(400, {"error": str(exc)})
        except Exception as exc:
            self.json_response(502, {"error": safe_state(exc)})

    def valid_browser_request(self) -> bool:
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        return content_type == "application/json" and self.headers.get("X-Baiamonte-Request") == "1"

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
        self.send_header("Cache-Control", "no-cache" if target.name == "index.html" else "public, max-age=3600")
        self.security_headers()
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
    worker = threading.Thread(target=monitor, daemon=True, name="tuya-route-monitor")
    worker.start()
    server = Server(("0.0.0.0", 8095), Handler)
    server.timeout = 1
    BRIDGE.event("Baiamonte Tuya listening on port 8095")
    while RUNNING:
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    main()
