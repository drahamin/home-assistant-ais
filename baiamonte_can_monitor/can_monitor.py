"""Home Assistant add-on for receive-only Growatt/Felicity CAN monitoring."""

from __future__ import annotations

import glob
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import can

from can_decoder import Reading, decode_frame


ENTITY_PREFIX = "baiamonte_can"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API_BASE = "http://supervisor/core/api/states"
RUNNING = True


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def load_options() -> dict:
    path = Path(os.environ.get("CAN_MONITOR_OPTIONS", "/data/options.json"))
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def friendly_name(key: str) -> str:
    return "CAN " + key.replace("_", " ").title()


def publish(key: str, reading: Reading, binary: bool = False) -> None:
    if not TOKEN:
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
    payload = json.dumps({"state": reading.value, "attributes": attributes}).encode()
    request = urllib.request.Request(
        f"{API_BASE}/{domain}.{ENTITY_PREFIX}_{key}",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f"Home Assistant state update failed: {exc}")


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
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    options = load_options()
    stale_after = max(5, int(options.get("stale_after_seconds", 30)))
    interval = max(1, int(options.get("publish_interval_seconds", 2)))
    log("Starting Baiamonte CAN Monitor; transmit code is disabled")
    publish_connection(False, "searching", 0, None)

    receiver = None
    adapter_name = "not connected"
    frame_count = 0
    last_frame_at = 0.0
    last_publish_at = 0.0
    last_status_at = 0.0
    last_id = None
    pending: dict[str, Reading] = {}

    while RUNNING:
        if receiver is None:
            try:
                receiver, adapter_name = open_adapter(options)
                log(f"Connected with {adapter_name}")
            except Exception as exc:
                log(f"CAN adapter not ready: {exc}; retrying in 10 seconds")
                publish_connection(False, "adapter not ready", frame_count, last_id)
                time.sleep(10)
                continue

        try:
            message = receiver.recv(timeout=1.0)
        except Exception as exc:
            log(f"CAN receive error: {exc}; reopening adapter")
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
            pending.update(decode_frame(message.arbitration_id, bytes(message.data)))
            if frame_count <= 20 or frame_count % 500 == 0:
                log(f"RX 0x{message.arbitration_id:03X} {bytes(message.data).hex(' ')}")

        connected = bool(last_frame_at and now - last_frame_at <= stale_after)
        if pending and now - last_publish_at >= interval:
            for key, reading in pending.items():
                publish(key, reading, key.endswith("_active") or key.endswith("_enabled") or key.startswith("force_charge"))
            pending.clear()
            last_publish_at = now

        if now - last_status_at >= interval:
            publish_connection(connected, adapter_name, frame_count, last_id)
            last_status_at = now

    if receiver is not None:
        receiver.shutdown()
    publish_connection(False, "stopped", frame_count, last_id)
    log("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
