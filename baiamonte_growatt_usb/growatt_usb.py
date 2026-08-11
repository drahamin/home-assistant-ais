"""Baiamonte Home Assistant app for read-only Growatt SPF USB telemetry."""

from __future__ import annotations

import glob
import hashlib
import json
import math
import mimetypes
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API_BASE = "http://supervisor/core/api/states"
OPTIONS_PATH = Path(os.environ.get("GROWATT_USB_OPTIONS", "/data/options.json"))
STATE_PATH = Path(os.environ.get("GROWATT_USB_STATE", "/data/energy-state.json"))
FIRMWARE_DIR = Path(os.environ.get("GROWATT_FIRMWARE_DIR", "/data/firmware"))
WEB_ROOT = Path(os.environ.get("GROWATT_USB_WEB", "/web"))
ENTITY_PREFIX = "baiamonte_growatt"
STARTED_AT = time.time()
RUNNING = True
WAKE = threading.Event()
STATUS_LOCK = threading.Lock()
DEVICE_IO_LOCK = threading.Lock()
EVENTS: deque[dict[str, object]] = deque(maxlen=80)

STATUS: dict[str, object] = {
    "service": "starting",
    "health": "searching",
    "connected": False,
    "device": None,
    "protocol": None,
    "last_success_at": None,
    "last_attempt_at": None,
    "last_error": None,
    "consecutive_failures": 0,
    "successful_polls": 0,
    "readings": {},
    "warnings": {},
    "mode": "Unknown",
    "energy_today_kwh": 0.0,
    "settings": {},
    "settings_updated_at": None,
    "firmware": {},
    "firmware_updated_at": None,
}

PROTOCOL_PROFILES = {
    "PI16": {"identity": "QPI", "live": "QPIGS", "mode": None, "warnings": None, "settings": "QPI"},
    "PI17": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "MD"},
    "PI17INFINI": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "MD"},
    "PI17M058": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "MD"},
    "PI18": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "PIRI"},
    "PI18LVX": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "PIRI"},
    "PI18SV": {"identity": "PI", "live": "GS", "mode": "MOD", "warnings": "FWS", "settings": "PIRI"},
    "PI30": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI30M044": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI30M045": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI30MAX": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI30MST": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI30REVO": {"identity": "QPI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
    "PI41": {"identity": "QDI", "live": "QPIGS", "mode": "QMOD", "warnings": "QPIWS", "settings": "QPIRI"},
}
AUTO_PROTOCOLS = ["PI30", "PI30MAX", "PI30M044", "PI30M045", "PI30REVO", "PI41"]

SETTING_SPECS: dict[str, dict[str, object]] = {
    "output_source_priority": {"label": "Output source priority", "kind": "select", "values": {"utility_first": "POP00", "solar_first": "POP01", "sbu_first": "POP02"}, "risk": "Changes which source powers estate loads."},
    "charger_source_priority": {"label": "Charger source priority", "kind": "select", "values": {"utility_first": "PCP00", "solar_first": "PCP01", "solar_and_utility": "PCP02", "solar_only": "PCP03"}, "risk": "Changes which sources may charge the battery."},
    "input_voltage_range": {"label": "AC input range", "kind": "select", "values": {"appliance": "PGR00", "ups": "PGR01"}, "risk": "UPS mode accepts a narrower utility voltage range."},
    "battery_type": {"label": "Battery type", "kind": "select", "values": {"agm": "PBT00", "flooded": "PBT01", "user": "PBT02"}, "risk": "Critical battery charging profile. Verify against the Felicity/Growatt commissioning plan."},
    "output_frequency": {"label": "Output frequency", "kind": "select", "values": {"50": "F50", "60": "F60"}, "risk": "Must match the estate electrical system."},
    "battery_recharge_voltage": {"label": "Battery recharge voltage", "kind": "number", "prefix": "PBCV", "minimum": 44.0, "maximum": 58.0, "step": 0.1, "decimals": 1, "risk": "Controls when utility charging resumes."},
    "battery_redischarge_voltage": {"label": "Battery return-to-discharge voltage", "kind": "number", "prefix": "PBDV", "minimum": 0.0, "maximum": 58.0, "step": 0.1, "decimals": 1, "risk": "Controls when the inverter returns from utility to battery."},
    "battery_bulk_charge_voltage": {"label": "Battery bulk/CV voltage", "kind": "number", "prefix": "PCVV", "minimum": 48.0, "maximum": 58.4, "step": 0.1, "decimals": 1, "risk": "Critical lithium charge voltage. Use only the approved battery value."},
    "battery_float_charge_voltage": {"label": "Battery float voltage", "kind": "number", "prefix": "PBFT", "minimum": 48.0, "maximum": 58.4, "step": 0.1, "decimals": 1, "risk": "Critical lithium float voltage. Use only the approved battery value."},
    "battery_cutoff_voltage": {"label": "Battery cutoff voltage", "kind": "number", "prefix": "PSDV", "minimum": 40.0, "maximum": 48.0, "step": 0.1, "decimals": 1, "risk": "Critical low-voltage protection threshold."},
    "max_charging_current": {"label": "Maximum total charging current", "kind": "integer", "prefix": "MCHGC", "minimum": 10, "maximum": 120, "step": 10, "width": 3, "risk": "Must not exceed the battery bank or cabling limit."},
    "max_utility_charging_current": {"label": "Maximum utility charging current", "kind": "integer", "prefix": "MUCHGC", "minimum": 2, "maximum": 120, "step": 1, "width": 3, "risk": "Changes grid/generator charging demand."},
    "solar_power_balance": {"label": "Solar power balance", "kind": "select", "values": {"charge_current_only": "PSPB0", "charge_and_load_power": "PSPB1"}, "risk": "Changes how available PV power is allocated."},
    "pv_parallel_condition": {"label": "Parallel PV OK condition", "kind": "select", "values": {"any_unit": "PPVOKC0", "all_units": "PPVOKC1"}, "risk": "Relevant only to parallel inverter systems."},
    "buzzer": {"label": "Buzzer", "kind": "toggle", "enable": "PEa", "disable": "PDa", "risk": "Changes audible alarms."},
    "power_saving": {"label": "Power saving", "kind": "toggle", "enable": "PEj", "disable": "PDj", "risk": "May turn output off at very low load."},
    "overload_restart": {"label": "Restart after overload", "kind": "toggle", "enable": "PEu", "disable": "PDu", "risk": "Allows automatic recovery after overload."},
    "overtemperature_restart": {"label": "Restart after overtemperature", "kind": "toggle", "enable": "PEv", "disable": "PDv", "risk": "Allows automatic recovery after cooling."},
    "lcd_backlight": {"label": "LCD backlight", "kind": "toggle", "enable": "PEx", "disable": "PDx", "risk": "Display preference only."},
    "source_interrupt_alarm": {"label": "Source interruption alarm", "kind": "toggle", "enable": "PEy", "disable": "PDy", "risk": "Changes audible source-loss notification."},
    "record_faults": {"label": "Record fault codes", "kind": "toggle", "enable": "PEz", "disable": "PDz", "risk": "Controls inverter fault history recording."},
}

SENSORS = {
    "ac_input_voltage": ("Grid Voltage", "V", "voltage", "measurement", "mdi:transmission-tower"),
    "ac_input_frequency": ("Grid Frequency", "Hz", "frequency", "measurement", "mdi:sine-wave"),
    "ac_output_voltage": ("Output Voltage", "V", "voltage", "measurement", "mdi:power-plug"),
    "ac_output_frequency": ("Output Frequency", "Hz", "frequency", "measurement", "mdi:sine-wave"),
    "ac_output_apparent_power": ("Output Apparent Power", "VA", "apparent_power", "measurement", "mdi:flash"),
    "ac_output_active_power": ("Output Power", "W", "power", "measurement", "mdi:flash"),
    "ac_output_load": ("Load", "%", None, "measurement", "mdi:gauge"),
    "bus_voltage": ("Bus Voltage", "V", "voltage", "measurement", "mdi:current-dc"),
    "battery_voltage": ("Battery Voltage", "V", "voltage", "measurement", "mdi:car-battery"),
    "battery_charging_current": ("Battery Charging Current", "A", "current", "measurement", "mdi:battery-plus"),
    "battery_discharge_current": ("Battery Discharge Current", "A", "current", "measurement", "mdi:battery-minus"),
    "battery_capacity": ("Battery Capacity", "%", "battery", "measurement", "mdi:battery"),
    "inverter_heat_sink_temperature": ("Inverter Temperature", "°C", "temperature", "measurement", "mdi:thermometer"),
    "pv_input_current_for_battery": ("PV Input Current", "A", "current", "measurement", "mdi:solar-panel"),
    "pv_input_voltage": ("PV Input Voltage", "V", "voltage", "measurement", "mdi:solar-panel"),
    "pv_input_power": ("PV Input Power", "W", "power", "measurement", "mdi:solar-power"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str, level: str = "info") -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
    EVENTS.appendleft({"at": now_iso(), "level": level, "message": message})


def load_options() -> dict[str, object]:
    if not OPTIONS_PATH.exists():
        return {}
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Could not read app options: {exc}", "error")
        return {}


def normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_")


def candidate_devices(configured: str = "auto") -> list[str]:
    if configured and configured != "auto":
        return [configured]
    stable = sorted(glob.glob("/dev/serial/by-id/*"))
    growatt_stable = [p for p in stable if any(word in p.lower() for word in ("growatt", "usb-serial", "ch340", "ch341"))]
    other_stable = [p for p in stable if p not in growatt_stable and "can" not in p.lower()]
    serial = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    hid = sorted(glob.glob("/dev/hidraw*"))
    return list(dict.fromkeys(growatt_stable + serial + other_stable + hid))


def protocols(configured: str = "auto") -> list[str]:
    return [configured] if configured != "auto" else AUTO_PROTOCOLS


def transport_for(device: str, configured: str = "auto") -> str:
    if configured != "auto":
        return configured
    return "usb" if "hidraw" in device.lower() else "serial"


def parse_mpp_json(output: str) -> dict[str, dict[str, object]]:
    parsed = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            parsed = json.loads(line)
            break
    if not isinstance(parsed, dict):
        raise ValueError("the inverter tool returned no JSON data")
    result: dict[str, dict[str, object]] = {}
    for raw_key, raw_value in parsed.items():
        key = normalize_key(raw_key)
        if key.startswith("_") or key in {"raw_response", "error"}:
            continue
        if isinstance(raw_value, dict) and "value" in raw_value:
            result[key] = {"value": raw_value.get("value"), "unit": raw_value.get("unit") or None}
        else:
            result[key] = {"value": raw_value, "unit": None}
    if not result:
        raise ValueError("the inverter returned an empty or unsupported response")
    return result


def run_query(device: str, protocol: str, baud: int, command: str, timeout: int = 12, transport: str = "auto") -> dict[str, dict[str, object]]:
    args = [
        "mpp-solar", "-p", device, "-P", protocol, "-b", str(baud),
        "--porttype", transport_for(device, transport), "-c", command, "-o", "json_units",
    ]
    with DEVICE_IO_LOCK:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else f"exit code {completed.returncode}"
        raise RuntimeError(detail)
    data = parse_mpp_json(combined)
    if any(key in data for key in ("error", "_failed_status_commands")):
        raise RuntimeError("the inverter did not accept the inquiry")
    return data


def discover(options: dict[str, object]) -> tuple[str, str, dict[str, dict[str, object]]]:
    candidates = candidate_devices(str(options.get("device", "auto")))
    if not candidates:
        raise FileNotFoundError("no serial or HID USB device is visible")
    baud = int(options.get("baud_rate", 2400))
    failures = []
    for device in candidates:
        if not Path(device).exists():
            failures.append(f"{device}: path does not exist")
            continue
        for protocol in protocols(str(options.get("protocol", "auto"))):
            try:
                profile = PROTOCOL_PROFILES.get(protocol, PROTOCOL_PROFILES["PI30"])
                identity = run_query(
                    device, protocol, baud, str(profile["identity"]), timeout=9,
                    transport=str(options.get("transport", "auto")),
                )
                return device, protocol, identity
            except Exception as exc:
                failures.append(f"{device} / {protocol}: {exc}")
    summary = failures[-1] if failures else "no candidates responded"
    raise ConnectionError(f"USB devices were found, but no Growatt response was received ({summary})")


def publish_state(entity_id: str, value: object, attributes: dict[str, object]) -> None:
    if not TOKEN:
        return
    request = urllib.request.Request(
        f"{API_BASE}/{entity_id}",
        data=json.dumps({"state": value, "attributes": attributes}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f"Home Assistant state update failed: {exc}", "warning")


def publish_sensor(key: str, value: object, unit: str | None = None) -> None:
    definition = SENSORS.get(key)
    if definition:
        name, default_unit, device_class, state_class, icon = definition
        attributes: dict[str, object] = {
            "friendly_name": f"Growatt {name}", "icon": icon,
            "attribution": "Local read-only Growatt USB telemetry",
        }
        unit = unit or default_unit
        if unit:
            attributes["unit_of_measurement"] = unit
        if device_class:
            attributes["device_class"] = device_class
        if state_class:
            attributes["state_class"] = state_class
    else:
        attributes = {"friendly_name": f"Growatt {key.replace('_', ' ').title()}", "attribution": "Local read-only Growatt USB telemetry"}
        if unit:
            attributes["unit_of_measurement"] = unit
    publish_state(f"sensor.{ENTITY_PREFIX}_{key}", value, attributes)


def publish_all(readings: dict[str, dict[str, object]], status: dict[str, object], enabled: bool) -> None:
    if not enabled:
        return
    for key in SENSORS:
        if key in readings:
            publish_sensor(key, readings[key].get("value"), readings[key].get("unit"))
    publish_sensor("mode", status.get("mode", "Unknown"))
    publish_state(
        f"binary_sensor.{ENTITY_PREFIX}_connected",
        "on" if status.get("connected") else "off",
        {"friendly_name": "Growatt USB Connected", "device_class": "connectivity", "icon": "mdi:usb-port", "device": status.get("device"), "protocol": status.get("protocol")},
    )


def setting_catalog(options: dict[str, object]) -> dict[str, object]:
    writable = not bool(options.get("read_only", True)) and bool(options.get("allow_setting_changes", False))
    return {
        "writable": writable,
        "locked_reason": None if writable else "Disable read-only mode and enable setting changes in the app Configuration page.",
        "confirmation": "APPLY",
        "items": SETTING_SPECS,
    }


def build_setting_command(key: str, value: object) -> str:
    if key not in SETTING_SPECS:
        raise ValueError("This setting is not in the approved Growatt command list")
    spec = SETTING_SPECS[key]
    kind = str(spec["kind"])
    if kind == "select":
        values = spec["values"]
        if not isinstance(values, dict) or str(value) not in values:
            raise ValueError("Select one of the supported values")
        return str(values[str(value)])
    if kind == "toggle":
        if isinstance(value, bool):
            normalized = value
        else:
            toggle_value = str(value).lower()
            if toggle_value not in {"true", "1", "on", "yes", "enabled", "false", "0", "off", "no", "disabled"}:
                raise ValueError("Select Enabled or Disabled")
            normalized = toggle_value in {"true", "1", "on", "yes", "enabled"}
        return str(spec["enable"] if normalized else spec["disable"])
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Value must be a finite number")
    minimum = float(spec["minimum"])
    maximum = float(spec["maximum"])
    step = float(spec["step"])
    if number < minimum or number > maximum:
        raise ValueError(f"Value must be between {minimum:g} and {maximum:g}")
    if abs((number - minimum) / step - round((number - minimum) / step)) > 1e-6:
        raise ValueError(f"Value must use increments of {step:g}")
    if kind == "integer":
        formatted = f"{int(number):0{int(spec.get('width', 0))}d}"
    else:
        formatted = f"{number:.{int(spec.get('decimals', 1))}f}"
    return f"{spec['prefix']}{formatted}"


def apply_setting(key: str, value: object, confirmation: str) -> dict[str, object]:
    options = load_options()
    catalog = setting_catalog(options)
    if not catalog["writable"]:
        raise PermissionError(str(catalog["locked_reason"]))
    if confirmation != catalog["confirmation"]:
        raise PermissionError("Enter APPLY to confirm this inverter change")
    with STATUS_LOCK:
        device = STATUS.get("device")
        protocol = STATUS.get("protocol")
    if not device or not protocol:
        raise ConnectionError("The Growatt USB connection must be online before changing a setting")
    if str(protocol) not in {"PI30", "PI30M044", "PI30M045", "PI30MAX", "PI30MST", "PI30REVO", "PI41"}:
        raise ValueError(f"Setting changes are not enabled for protocol {protocol}")
    command = build_setting_command(key, value)
    result = run_query(
        str(device), str(protocol), int(options.get("baud_rate", 2400)), command, timeout=12,
        transport=str(options.get("transport", "auto")),
    )
    acknowledgement = next((item.get("value") for name, item in result.items() if name in {"ack", "command_execution"}), None)
    if acknowledgement is not None and str(acknowledgement).lower() not in {"successful", "success", "ack", "true", "1"}:
        raise RuntimeError(f"The inverter rejected the command: {acknowledgement}")
    log(f"Commissioning change applied: {key} = {value}", "warning")
    profile = PROTOCOL_PROFILES.get(str(protocol), PROTOCOL_PROFILES["PI30"])
    settings_command = profile.get("settings")
    verified: dict[str, dict[str, object]] = {}
    verification_error = None
    if settings_command:
        try:
            verified = run_query(
                str(device), str(protocol), int(options.get("baud_rate", 2400)), str(settings_command), timeout=12,
                transport=str(options.get("transport", "auto")),
            )
            with STATUS_LOCK:
                STATUS.update({"settings": verified, "settings_updated_at": now_iso()})
        except Exception as exc:
            verification_error = str(exc)
            log(f"Setting command was accepted, but read-back failed: {exc}", "error")
    return {"ok": True, "setting": key, "value": value, "command": command, "verified": bool(verified), "verification_error": verification_error, "read_back": verified}


def read_settings_now() -> dict[str, dict[str, object]]:
    options = load_options()
    with STATUS_LOCK:
        device = STATUS.get("device")
        protocol = STATUS.get("protocol")
    if not device or not protocol:
        raise ConnectionError("The Growatt USB connection must be online before reading settings")
    profile = PROTOCOL_PROFILES.get(str(protocol))
    if not profile or not profile.get("settings"):
        raise ValueError(f"A settings inquiry is not defined for protocol {protocol}")
    settings = run_query(
        str(device), str(protocol), int(options.get("baud_rate", 2400)), str(profile["settings"]), timeout=12,
        transport=str(options.get("transport", "auto")),
    )
    with STATUS_LOCK:
        STATUS.update({"settings": settings, "settings_updated_at": now_iso()})
    log("Growatt settings profile refreshed")
    return settings


def read_firmware_now() -> dict[str, dict[str, object]]:
    options = load_options()
    with STATUS_LOCK:
        device = STATUS.get("device")
        protocol = STATUS.get("protocol")
    if not device or not protocol:
        raise ConnectionError("The Growatt USB connection must be online before reading firmware versions")
    commands = ["QVFW", "QVFW2"] if str(protocol).startswith(("PI30", "PI41")) else ["VFW"]
    firmware: dict[str, dict[str, object]] = {}
    errors = []
    for command in commands:
        try:
            firmware.update(run_query(
                str(device), str(protocol), int(options.get("baud_rate", 2400)), command, timeout=10,
                transport=str(options.get("transport", "auto")),
            ))
        except Exception as exc:
            errors.append(f"{command}: {exc}")
    if not firmware:
        raise RuntimeError("Firmware version inquiries failed: " + " | ".join(errors))
    with STATUS_LOCK:
        STATUS.update({"firmware": firmware, "firmware_updated_at": now_iso()})
    log("Growatt firmware versions refreshed")
    return firmware


def firmware_backup() -> dict[str, object]:
    with STATUS_LOCK:
        return {
            "created_at": now_iso(),
            "app": "Baiamonte Growatt USB",
            "inverter": {
                "device": STATUS.get("device"), "protocol": STATUS.get("protocol"),
                "identity": STATUS.get("identity", {}), "firmware": STATUS.get("firmware", {}),
                "settings": STATUS.get("settings", {}),
            },
            "notice": "Reference backup only. Restore values individually through approved commissioning controls.",
        }


def stage_firmware_package(filename: str, content: bytes, expected_sha256: str, model_confirmation: str) -> dict[str, object]:
    options = load_options()
    if not bool(options.get("firmware_tools_enabled", True)):
        raise PermissionError("Enable firmware maintenance tools in the app Configuration page")
    if model_confirmation.strip().upper() != "SPF 5000 ES":
        raise ValueError("Enter SPF 5000 ES to confirm the target inverter model")
    safe_name = Path(filename).name
    extension = Path(safe_name).suffix.lower()
    if extension not in {".bin", ".hex", ".fw", ".zip"}:
        raise ValueError("Use an official .bin, .hex, .fw, or .zip firmware package")
    maximum = max(1, int(options.get("firmware_max_package_mb", 32))) * 1024 * 1024
    if len(content) < 256:
        raise ValueError("The firmware package is unexpectedly small")
    if len(content) > maximum:
        raise ValueError(f"The firmware package exceeds the configured {maximum // (1024 * 1024)} MB limit")
    digest = hashlib.sha256(content).hexdigest()
    expected = expected_sha256.strip().lower()
    if not expected or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("Enter the 64-character SHA-256 supplied or independently verified for this package")
    if digest != expected:
        raise ValueError("SHA-256 mismatch: the selected file does not match the expected package")
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    target = FIRMWARE_DIR / f"spf5000es-{digest[:16]}{extension}"
    temporary = FIRMWARE_DIR / f".{digest}.staging"
    temporary.write_bytes(content)
    temporary.replace(target)
    metadata = {
        "staged_at": now_iso(), "original_filename": safe_name, "stored_filename": target.name,
        "size_bytes": len(content), "sha256": digest, "model": "SPF 5000 ES",
        "flash_supported": False,
        "status": "Validated and staged; awaiting an official Growatt flashing driver/procedure.",
    }
    (FIRMWARE_DIR / "staged-package.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    log(f"Official firmware package staged and checksum verified: {safe_name} ({digest[:12]}…)", "warning")
    return metadata


def is_official_growatt_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "growatt.com" or host.endswith(".growatt.com")) and not parsed.username and not parsed.password


class GrowattRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not is_official_growatt_url(newurl):
            raise ValueError("Growatt download redirected outside the official growatt.com domain")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_official_firmware(url: str, expected_sha256: str, model_confirmation: str) -> dict[str, object]:
    if not is_official_growatt_url(url):
        raise ValueError("Use an HTTPS firmware URL hosted on the official growatt.com domain")
    options = load_options()
    if not bool(options.get("firmware_tools_enabled", True)):
        raise PermissionError("Enable firmware maintenance tools in the app Configuration page")
    maximum = max(1, int(options.get("firmware_max_package_mb", 32))) * 1024 * 1024
    request = urllib.request.Request(url, headers={"User-Agent": "Baiamonte-Growatt-USB/1.2"})
    opener = urllib.request.build_opener(GrowattRedirectHandler())
    with opener.open(request, timeout=25) as response:
        final_url = response.geturl()
        if not is_official_growatt_url(final_url):
            raise ValueError("Growatt download resolved outside the official growatt.com domain")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > maximum:
            raise ValueError(f"The Growatt package exceeds the configured {maximum // (1024 * 1024)} MB limit")
        content = response.read(maximum + 1)
        disposition = response.headers.get("Content-Disposition", "")
    if len(content) > maximum:
        raise ValueError(f"The Growatt package exceeds the configured {maximum // (1024 * 1024)} MB limit")
    filename = Path(urlparse(final_url).path).name
    if "filename=" in disposition.lower():
        filename = disposition.split("filename=", 1)[1].strip().strip('"\'').split(";", 1)[0]
    result = stage_firmware_package(filename, content, expected_sha256, model_confirmation)
    result["official_source_url"] = final_url
    (FIRMWARE_DIR / "staged-package.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(f"Firmware package fetched from official Growatt source: {urlparse(final_url).hostname}", "warning")
    return result


def staged_firmware_status() -> dict[str, object] | None:
    path = FIRMWARE_DIR / "staged-package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    publish_state(
        f"sensor.{ENTITY_PREFIX}_energy_today",
        round(float(status.get("energy_today_kwh", 0.0)), 3),
        {"friendly_name": "Growatt PV Energy Today", "unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total", "icon": "mdi:solar-power"},
    )


def load_energy() -> dict[str, object]:
    today = datetime.now().astimezone().date().isoformat()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("date") != today:
        data = {"date": today, "wh": 0.0, "last_power": 0.0, "last_at": None}
    return data


def update_energy(energy: dict[str, object], readings: dict[str, dict[str, object]], poll_interval: int) -> None:
    today = datetime.now().astimezone().date().isoformat()
    if energy.get("date") != today:
        energy.clear()
        energy.update({"date": today, "wh": 0.0, "last_power": 0.0, "last_at": None})
    now = time.time()
    power = float(readings.get("pv_input_power", {}).get("value") or 0.0)
    last_at = energy.get("last_at")
    if isinstance(last_at, (int, float)):
        elapsed = min(now - float(last_at), poll_interval * 2.5)
        energy["wh"] = float(energy.get("wh", 0.0)) + ((float(energy.get("last_power", 0.0)) + power) / 2.0) * elapsed / 3600.0
    energy.update({"last_power": power, "last_at": now})
    STATUS["energy_today_kwh"] = float(energy.get("wh", 0.0)) / 1000.0
    try:
        STATE_PATH.write_text(json.dumps(energy), encoding="utf-8")
    except OSError as exc:
        log(f"Could not save daily energy estimate: {exc}", "warning")


def classify_health(status: dict[str, object], options: dict[str, object]) -> tuple[str, str, list[str]]:
    devices = candidate_devices(str(options.get("device", "auto")))
    if not devices:
        return "usb_missing", "No compatible USB path is visible to the app.", [
            "Connect the USB-B cable between the Growatt and Home Assistant host.",
            "Try a known data-capable cable; charge-only cables do not create a device.",
            "Reconnect the cable, then press Rescan USB or restart the app.",
        ]
    if not status.get("connected"):
        return "no_response", "A USB device is visible, but the inverter is not answering.", [
            "Stop the old manual Growatt service or integration so only this app owns the USB port.",
            "Leave device and protocol on Auto; the app tests the common SPF PI30-family variants.",
            "Confirm the Growatt is powered, then test another USB port and cable.",
            "If several USB devices exist, select the stable /dev/serial/by-id path in Configuration.",
        ]
    age = time.time() - float(status.get("last_success_epoch") or 0)
    if age > int(options.get("stale_after_seconds", 45)):
        return "stale", "The inverter answered earlier, but live readings have stopped.", [
            "Check whether another app reclaimed the same USB device.",
            "Reconnect the cable and press Rescan USB.",
            "Review the event log for timeouts, disconnects, or protocol errors.",
        ]
    warnings = status.get("warnings") or {}
    def warning_is_active(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        value = item.get("value")
        return value is True or value == 1 or str(value).lower() in {"1", "true", "on", "yes", "active", "warning", "fault"}

    if warnings and any(warning_is_active(item) for item in warnings.values()):
        return "warning", "USB telemetry is live and the inverter is reporting a warning flag.", [
            "Open the inverter warning details below and compare them with the Growatt display.",
            "Resolve active inverter warnings before applying commissioning changes.",
        ]
    safety_status = "Guarded commissioning changes are enabled." if setting_catalog(options)["writable"] else "The read-only safety lock is active."
    return "healthy", "Local Growatt USB telemetry is live and updating normally.", [
        "The USB device is connected and responding.",
        "Live inverter values are updating in Home Assistant.",
        safety_status,
    ]


def dashboard_status() -> dict[str, object]:
    options = load_options()
    with STATUS_LOCK:
        result = dict(STATUS)
        result["readings"] = dict(STATUS.get("readings", {}))
        result["warnings"] = dict(STATUS.get("warnings", {}))
    health, diagnosis, steps = classify_health(result, options)
    result.update({
        "health": health, "diagnosis": diagnosis, "troubleshooting": steps,
        "read_only": bool(options.get("read_only", True)), "uptime_seconds": int(time.time() - STARTED_AT),
        "detected_devices": candidate_devices(str(options.get("device", "auto"))),
        "configured_device": options.get("device", "auto"),
        "configured_transport": options.get("transport", "auto"),
        "configured_protocol": options.get("protocol", "auto"),
        "baud_rate": int(options.get("baud_rate", 2400)),
        "poll_interval_seconds": int(options.get("poll_interval_seconds", 10)),
        "events": list(EVENTS), "server_time": now_iso(),
        "setting_catalog": setting_catalog(options),
        "firmware_tools": {
            "enabled": bool(options.get("firmware_tools_enabled", True)),
            "direct_flash_supported": False,
            "staged_package": staged_firmware_status(),
            "max_package_mb": max(1, int(options.get("firmware_max_package_mb", 32))),
            "reason": "No official SPF 5000 ES USB flashing protocol or recovery driver is bundled.",
            "official_source_page": "https://en.growatt.com/support/download",
            "public_package_found": False,
        },
    })
    result.pop("last_success_epoch", None)
    return result


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload: object, status: int = 200) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path.endswith("/api/status"):
            self.send_json(dashboard_status())
            return
        if path.endswith("/api/firmware/backup"):
            payload = json.dumps(firmware_backup(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="baiamonte-growatt-firmware-backup.json"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        leaf = path.rsplit("/", 1)[-1]
        relative = leaf if "." in leaf else "index.html"
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():
            self.send_error(404)
            return
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path.endswith("/api/rescan"):
            with STATUS_LOCK:
                STATUS.update({"connected": False, "device": None, "protocol": None, "last_error": "Manual USB rescan requested"})
            WAKE.set()
            self.send_json({"ok": True, "message": "USB rescan requested"}, 202)
            return
        if path.endswith("/api/poll"):
            WAKE.set()
            self.send_json({"ok": True, "message": "Immediate reading requested"}, 202)
            return
        if path.endswith("/api/settings/read"):
            try:
                self.send_json({"ok": True, "settings": read_settings_now()})
            except (ValueError, ConnectionError, RuntimeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path.endswith("/api/firmware/read"):
            try:
                self.send_json({"ok": True, "firmware": read_firmware_now()})
            except (ValueError, ConnectionError, RuntimeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path.endswith("/api/firmware/stage"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                maximum = max(1, int(load_options().get("firmware_max_package_mb", 32))) * 1024 * 1024
                if length <= 0 or length > maximum:
                    raise ValueError("Invalid firmware package size")
                result = stage_firmware_package(
                    self.headers.get("X-Firmware-Filename", ""), self.rfile.read(length),
                    self.headers.get("X-Expected-SHA256", ""), self.headers.get("X-Model-Confirmation", ""),
                )
                self.send_json({"ok": True, "package": result}, 201)
            except PermissionError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 403)
            except (ValueError, OSError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path.endswith("/api/firmware/fetch"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16384:
                    raise ValueError("Invalid official firmware request size")
                body = json.loads(self.rfile.read(length))
                result = fetch_official_firmware(
                    str(body.get("url", "")), str(body.get("sha256", "")), str(body.get("model_confirmation", "")),
                )
                self.send_json({"ok": True, "package": result}, 201)
            except PermissionError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 403)
            except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if path.endswith("/api/settings"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                result = apply_setting(str(body.get("key", "")), body.get("value"), str(body.get("confirmation", "")))
                self.send_json(result)
            except PermissionError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 403)
            except (ValueError, ConnectionError, RuntimeError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        self.send_error(405)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def poll_loop() -> None:
    options = load_options()
    interval = max(5, int(options.get("poll_interval_seconds", 10)))
    publish_enabled = bool(options.get("publish_to_home_assistant", True))
    energy = load_energy()
    device = None
    protocol = None
    last_identity: dict[str, dict[str, object]] = {}
    last_slow_poll = 0.0
    last_settings_poll = 0.0
    last_firmware_poll = 0.0
    with STATUS_LOCK:
        STATUS.update({"service": "running", "energy_today_kwh": float(energy.get("wh", 0.0)) / 1000.0})
    if setting_catalog(options)["writable"]:
        log("Baiamonte Growatt USB started with guarded commissioning changes enabled", "warning")
    else:
        log("Baiamonte Growatt USB started in read-only monitoring mode")

    while RUNNING:
        with STATUS_LOCK:
            STATUS["last_attempt_at"] = now_iso()
        try:
            if not device or not Path(device).exists():
                device, protocol, last_identity = discover(options)
                log(f"Connected to Growatt on {device} using {protocol}")
            profile = PROTOCOL_PROFILES.get(protocol, PROTOCOL_PROFILES["PI30"])
            readings = run_query(
                device, protocol, int(options.get("baud_rate", 2400)), str(profile["live"]),
                transport=str(options.get("transport", "auto")),
            )
            mode_data: dict[str, dict[str, object]] = {}
            if profile.get("mode"):
                mode_data = run_query(
                    device, protocol, int(options.get("baud_rate", 2400)), str(profile["mode"]), timeout=8,
                    transport=str(options.get("transport", "auto")),
                )
            mode = str(mode_data.get("device_mode", {}).get("value") or mode_data.get("mode", {}).get("value") or "Unknown")
            warnings: dict[str, dict[str, object]] = {}
            if profile.get("warnings") and time.time() - last_slow_poll >= 60:
                try:
                    warnings = run_query(
                        device, protocol, int(options.get("baud_rate", 2400)), str(profile["warnings"]), timeout=8,
                        transport=str(options.get("transport", "auto")),
                    )
                    last_slow_poll = time.time()
                except Exception as exc:
                    log(f"Optional warning inquiry was not accepted: {exc}", "warning")
            current_settings: dict[str, dict[str, object]] = {}
            if profile.get("settings") and time.time() - last_settings_poll >= 300:
                try:
                    current_settings = run_query(
                        device, protocol, int(options.get("baud_rate", 2400)), str(profile["settings"]), timeout=12,
                        transport=str(options.get("transport", "auto")),
                    )
                    last_settings_poll = time.time()
                except Exception as exc:
                    log(f"Optional settings inquiry was not accepted: {exc}", "warning")
            current_firmware: dict[str, dict[str, object]] = {}
            if time.time() - last_firmware_poll >= 300:
                try:
                    current_firmware = read_firmware_now()
                    last_firmware_poll = time.time()
                except Exception as exc:
                    log(f"Optional firmware version inquiry was not accepted: {exc}", "warning")
            update_energy(energy, readings, interval)
            with STATUS_LOCK:
                existing_warnings = STATUS.get("warnings", {})
                STATUS.update({
                    "connected": True, "device": device, "protocol": protocol,
                    "identity": last_identity, "mode": mode, "readings": readings,
                    "warnings": warnings or existing_warnings, "last_success_at": now_iso(),
                    "last_success_epoch": time.time(), "last_error": None,
                    "consecutive_failures": 0,
                    "successful_polls": int(STATUS.get("successful_polls", 0)) + 1,
                })
                if current_settings:
                    STATUS.update({"settings": current_settings, "settings_updated_at": now_iso()})
                if current_firmware:
                    STATUS.update({"firmware": current_firmware, "firmware_updated_at": now_iso()})
                snapshot = dict(STATUS)
            publish_all(readings, snapshot, publish_enabled)
        except Exception as exc:
            with STATUS_LOCK:
                failures = int(STATUS.get("consecutive_failures", 0)) + 1
                STATUS.update({"connected": False, "last_error": str(exc), "consecutive_failures": failures})
                snapshot = dict(STATUS)
            log(f"Growatt USB poll failed: {exc}", "warning")
            publish_all({}, snapshot, publish_enabled)
            device = None
            protocol = None
        WAKE.wait(interval)
        WAKE.clear()


def stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False
    WAKE.set()


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = ThreadingHTTPServer(("0.0.0.0", 8097), Handler)
    threading.Thread(target=server.serve_forever, name="growatt-dashboard", daemon=True).start()
    worker = threading.Thread(target=poll_loop, name="growatt-usb", daemon=True)
    worker.start()
    log("Baiamonte Growatt dashboard ready on Home Assistant ingress")
    while RUNNING:
        time.sleep(1)
    server.shutdown()
    worker.join(timeout=5)
    log("Baiamonte Growatt USB stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
