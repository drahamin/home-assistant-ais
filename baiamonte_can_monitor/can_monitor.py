"""Home Assistant add-on for receive-only Growatt/Felicity CAN monitoring."""

from __future__ import annotations

import glob
import json
import mimetypes
import os
import signal
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import can

from can_decoder import Reading, decode_frame
from state_publisher import StatePublisher


ENTITY_PREFIX = "baiamonte_can"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API_BASE = "http://supervisor/core/api/states"
RUNNING = True
WEB_ROOT = Path("/web")
STARTED_AT = time.time()
STATUS_LOCK = threading.Lock()
STATUS: dict[str, object] = {
    "service": "starting",
    "adapter_connected": False,
    "bus_active": False,
    "adapter": "searching",
    "bitrate": 500000,
    "frames_received": 0,
    "last_id": None,
    "last_frame_at": None,
    "last_error": None,
    "readings": {},
}
RECENT_FRAMES: deque[dict[str, object]] = deque(maxlen=24)
FRAME_TIMES: deque[float] = deque(maxlen=4000)
ID_COUNTS: Counter[str] = Counter()
PUBLISHER: StatePublisher | None = None


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_status(**values: object) -> None:
    with STATUS_LOCK:
        STATUS.update(values)


def dashboard_status() -> dict[str, object]:
    now = time.time()
    with STATUS_LOCK:
        status = dict(STATUS)
        status["readings"] = dict(STATUS.get("readings", {}))
        recent_frames = list(RECENT_FRAMES)
        while FRAME_TIMES and now - FRAME_TIMES[0] > 60:
            FRAME_TIMES.popleft()
        last_five = sum(1 for timestamp in FRAME_TIMES if now - timestamp <= 5)
        traffic_ids = [{"id": can_id, "count": count} for can_id, count in ID_COUNTS.most_common()]
    frames = int(status.get("frames_received", 0) or 0)
    adapter_connected = bool(status.get("adapter_connected"))
    bus_active = bool(status.get("bus_active"))
    if not adapter_connected:
        health = "adapter_missing"
        diagnosis = "The CAN adapter is not available. Check USB, the selected adapter mode, and the serial device."
    elif frames == 0:
        health = "no_traffic"
        diagnosis = "The adapter is ready but no valid CAN frames have arrived. Check CAN-H/CAN-L, equipment power, 500 kbit/s, and termination."
    elif not bus_active:
        health = "stale"
        diagnosis = "CAN traffic was seen but has stopped. Check the inverter, battery master, cable, and connectors."
    else:
        health = "healthy"
        diagnosis = "Live receive-only CAN traffic is being decoded normally."
    status.update({
        "health": health,
        "diagnosis": diagnosis,
        "receive_only": True,
        "uptime_seconds": max(0, int(time.time() - STARTED_AT)),
        "recent_frames": recent_frames,
        "frames_per_second": round(last_five / 5, 1),
        "frames_last_minute": len(FRAME_TIMES),
        "traffic_ids": traffic_ids,
        "server_time": iso_now(),
    })
    return status


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        if request_path.rstrip("/").endswith("/api/status"):
            payload = json.dumps(dashboard_status(), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        relative = request_path.rstrip("/").rsplit("/", 1)[-1] if "." in request_path.rsplit("/", 1)[-1] else "index.html"
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():
            self.send_error(404)
            return
        payload = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        self.send_error(405, "This dashboard is receive-only")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_dashboard(port: int = 8098) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    threading.Thread(target=server.serve_forever, name="baiamonte-can-dashboard", daemon=True).start()
    log(f"Baiamonte CAN status dashboard ready on port {port}")
    return server


def load_options() -> dict:
    path = Path(os.environ.get("CAN_MONITOR_OPTIONS", "/data/options.json"))
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def friendly_name(key: str) -> str:
    return "CAN " + key.replace("_", " ").title()


def publish(key: str, reading: Reading, binary: bool = False) -> None:
    if PUBLISHER is None:
        return
    domain = "binary_sensor" if binary else "sensor"
    attributes: dict[str, object] = {
        "friendly_name": friendly_name(key),
        "icon": "mdi:car-battery" if not binary else "mdi:check-network-outline",
        "attribution": "Growatt BMS CAN v1.04, receive-only",
    }
    if reading.unit:
        attributes["unit_of_measurement"] = reading.unit
    if reading.device_class:
        attributes["device_class"] = reading.device_class
    if reading.state_class:
        attributes["state_class"] = reading.state_class
    PUBLISHER.queue(
        f"{domain}.{ENTITY_PREFIX}_{key}",
        {"state": reading.value, "attributes": attributes},
    )


def publish_connection(connected: bool, adapter: str, frames: int, last_id: int | None) -> None:
    details = f"{adapter}; {frames} frames"
    if last_id is not None:
        details += f"; last ID 0x{last_id:03X}"
    publish("connected", Reading("on" if connected else "off"), binary=True)
    publish("connection_details", Reading(details))
    publish("frames_received", Reading(frames, None, None, "total_increasing"))


def serial_candidates(configured: str) -> list[str]:
    if configured and configured != "auto":
        return [configured]
    stable = sorted(glob.glob("/dev/serial/by-id/*CAN*")) + sorted(glob.glob("/dev/serial/by-id/*can*"))
    generic = sorted(glob.glob("/dev/ttyACM*"))
    # Deliberately exclude ttyUSB devices in auto mode: ttyUSB0 is the user's
    # direct Growatt connection and must not be claimed by this monitor.
    return list(dict.fromkeys(stable + generic))


def open_gs_usb(bitrate: int):
    """Open candleLight in firmware-enforced listen-only mode."""
    import usb.core
    from gs_usb.constants import GS_CAN_MODE_HW_TIMESTAMP, GS_CAN_MODE_LISTEN_ONLY
    from gs_usb.gs_usb import GsUsb

    devices = list(
        usb.core.find(find_all=True, custom_match=GsUsb.is_gs_usb_device) or []
    )
    if not devices:
        raise RuntimeError("no gs_usb/candleLight CAN adapter found")
    device = GsUsb(devices[0])
    if not (device.device_capability.feature & GS_CAN_MODE_LISTEN_ONLY):
        raise RuntimeError("CAN adapter firmware does not advertise listen-only support")
    if not device.set_bitrate(bitrate):
        raise RuntimeError(f"adapter cannot configure {bitrate} bit/s")
    device.start(flags=GS_CAN_MODE_LISTEN_ONLY | GS_CAN_MODE_HW_TIMESTAMP)
    log(f"Opened raw USB CAN adapter at {bitrate} bit/s in hardware listen-only mode")
    return RawGsUsbReceiver(device), "gs_usb listen-only"


class RawGsUsbReceiver:
    def __init__(self, device) -> None:
        self.device = device

    def recv(self, timeout: float):
        from gs_usb.gs_usb_frame import GsUsbFrame

        frame = GsUsbFrame()
        if not self.device.read(frame, max(1, int(timeout * 1000))):
            return None
        return can.Message(
            timestamp=frame.timestamp,
            arbitration_id=frame.arbitration_id,
            is_extended_id=frame.is_extended_id,
            is_remote_frame=frame.is_remote_frame,
            is_error_frame=frame.is_error_frame,
            data=bytearray(frame.data)[: frame.can_dlc],
            is_rx=True,
        )

    def shutdown(self) -> None:
        self.device.stop()


def open_slcan(options: dict, bitrate: int):
    candidates = serial_candidates(str(options.get("serial_device", "auto")))
    if not candidates:
        raise RuntimeError("no serial CANable found (auto mode excludes the Growatt ttyUSB0)")
    failures = []
    for path in candidates:
        try:
            bus = can.Bus(
                interface="slcan",
                channel=path,
                tty_baudrate=int(options.get("serial_baudrate", 115200)),
                bitrate=bitrate,
                listen_only=True,
            )
            log(f"Opened {path} at {bitrate} bit/s using the slcan listen-only command")
            return bus, f"slcan listen-only ({path})"
        except Exception as exc:  # continue through explicitly bounded candidates
            failures.append(f"{path}: {exc}")
    raise RuntimeError("; ".join(failures))


def open_adapter(options: dict):
    mode = str(options.get("adapter", "auto"))
    bitrate = int(options.get("bitrate", 500000))
    errors = []
    if mode in {"auto", "gs_usb"}:
        try:
            return open_gs_usb(bitrate)
        except Exception as exc:
            errors.append(f"gs_usb: {exc}")
            if mode == "gs_usb":
                raise
    if mode in {"auto", "slcan"}:
        try:
            return open_slcan(options, bitrate)
        except Exception as exc:
            errors.append(f"slcan: {exc}")
    raise RuntimeError(" | ".join(errors))


def stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    global PUBLISHER
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    options = load_options()
    stale_after = max(5, int(options.get("stale_after_seconds", 30)))
    interval = max(1, int(options.get("publish_interval_seconds", 2)))
    bitrate = int(options.get("bitrate", 500000))
    dashboard = start_dashboard(int(options.get("dashboard_port", 8098)))
    PUBLISHER = StatePublisher(
        API_BASE,
        TOKEN,
        log,
        error_callback=lambda error: update_status(last_error=error),
    )
    PUBLISHER.start()
    update_status(service="running", bitrate=bitrate)
    log("Starting Baiamonte CAN Monitor; transmit code is disabled")
    publish_connection(False, "searching", 0, None)

    receiver = None
    adapter_name = "not connected"
    frame_count = 0
    last_frame_at = 0.0
    last_publish_at = 0.0
    last_status_at = 0.0
    status_interval = max(10, interval)
    last_id = None
    pending: dict[str, Reading] = {}

    while RUNNING:
        if receiver is None:
            try:
                receiver, adapter_name = open_adapter(options)
                log(f"Connected with {adapter_name}")
                update_status(adapter_connected=True, adapter=adapter_name, last_error=None)
            except Exception as exc:
                log(f"CAN adapter not ready: {exc}; retrying in 10 seconds")
                update_status(adapter_connected=False, bus_active=False, adapter="not ready", last_error=str(exc))
                publish_connection(False, "adapter not ready", frame_count, last_id)
                time.sleep(10)
                continue

        try:
            message = receiver.recv(timeout=1.0)
        except Exception as exc:
            log(f"CAN receive error: {exc}; reopening adapter")
            update_status(adapter_connected=False, bus_active=False, last_error=f"CAN receive error: {exc}")
            try:
                receiver.shutdown()
            except Exception:
                pass
            receiver = None
            continue

        now = time.monotonic()
        if message is not None and not message.is_error_frame and not message.is_remote_frame:
            frame_count += 1
            last_frame_at = now
            last_id = message.arbitration_id
            decoded = decode_frame(message.arbitration_id, bytes(message.data))
            pending.update(decoded)
            frame_record = {
                "id": f"0x{message.arbitration_id:03X}",
                "data": bytes(message.data).hex(" ").upper(),
                "at": iso_now(),
                "decoded": sorted(decoded),
            }
            with STATUS_LOCK:
                RECENT_FRAMES.appendleft(frame_record)
                FRAME_TIMES.append(time.time())
                ID_COUNTS[frame_record["id"]] += 1
                readings = dict(STATUS.get("readings", {}))
                for key, reading in decoded.items():
                    readings[key] = {"value": reading.value, "unit": reading.unit, "updated_at": frame_record["at"]}
                STATUS.update({
                    "bus_active": True,
                    "frames_received": frame_count,
                    "last_id": frame_record["id"],
                    "last_frame_at": frame_record["at"],
                    "readings": readings,
                })
            if frame_count <= 20 or frame_count % 500 == 0:
                log(f"RX 0x{message.arbitration_id:03X} {bytes(message.data).hex(' ')}")

        connected = bool(last_frame_at and now - last_frame_at <= stale_after)
        update_status(bus_active=connected, frames_received=frame_count)
        if pending and now - last_publish_at >= interval:
            for key, reading in pending.items():
                publish(key, reading, key.endswith("_active") or key.endswith("_enabled") or key.startswith("force_charge"))
            pending.clear()
            last_publish_at = now

        if now - last_status_at >= status_interval:
            publish_connection(connected, adapter_name, frame_count, last_id)
            last_status_at = now

    if receiver is not None:
        receiver.shutdown()
    publish_connection(False, "stopped", frame_count, last_id)
    update_status(service="stopped", adapter_connected=False, bus_active=False)
    if PUBLISHER is not None:
        PUBLISHER.stop()
    dashboard.shutdown()
    log("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
