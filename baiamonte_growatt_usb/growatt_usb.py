"""Baiamonte Home Assistant app for local Growatt SPF telemetry."""

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
PUBLISHED_STATES: dict[str, tuple[object, str, float]] = {}
OPTIONS_CACHE: dict[str, object] = {"path": None, "mtime_ns": None, "value": {}}
DEVICE_CACHE: dict[str, object] = {"configured": None, "at": 0.0, "devices": []}
LAST_ENERGY_SAVE = 0.0
ENTITY_HEARTBEAT_SECONDS = 300
ENERGY_SAVE_INTERVAL_SECONDS = 60
SETTINGS_REFRESH_SECONDS = 900
FIRMWARE_REFRESH_SECONDS = 21600

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
    "address_scan": {
        "running": False, "progress": 0, "current_address": None,
        "found_addresses": [], "started_at": None, "finished_at": None, "error": None,
    },
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

MODBUS_PROTOCOL = "GROWATT_MODBUS_V014"
RAW_PI_PROTOCOL = "PI_RAW"
MODBUS_STATUS = {
    0: "Standby", 1: "PV and grid combined discharge", 2: "Discharge", 3: "Fault",
    4: "Firmware update", 5: "PV charge", 6: "AC charge", 7: "Combined charge",
    8: "Combined charge and bypass", 9: "PV charge and bypass",
    10: "AC charge and bypass", 11: "Bypass", 12: "PV charge and discharge",
}
MODBUS_WARNING_BITS = {
    0: "fan_lock_warning", 1: "over_charge", 2: "battery_voltage_low",
    3: "over_load", 4: "output_power_derating", 5: "solar_stopped_battery_low",
    6: "solar_stopped_pv_high", 7: "solar_stopped_over_load", 8: "grid_different",
    9: "grid_phase_error", 10: "output_phase_loss", 11: "over_temperature",
    12: "buck_current_over", 13: "battery_disconnected", 14: "bms_communication_error",
    15: "pv_power_insufficient",
}


def _reading(value: object, unit: str | None = None) -> dict[str, object]:
    return {"value": value, "unit": unit}


def _u32(registers: list[int], high: int) -> int:
    return (int(registers[high]) << 16) | int(registers[high + 1])


def _s32(registers: list[int], high: int) -> int:
    value = _u32(registers, high)
    return value - 0x100000000 if value & 0x80000000 else value


def decode_modbus_input(registers: list[int]) -> tuple[dict[str, dict[str, object]], str, dict[str, dict[str, object]]]:
    """Decode Growatt Off-Grid Modbus RTU v0.14 input registers 0-90."""
    if len(registers) < 84:
        raise ValueError(f"short Growatt Modbus response ({len(registers)} registers)")
    pv1_power = _u32(registers, 3) / 10
    pv2_power = _u32(registers, 5) / 10
    battery_watts = _s32(registers, 77) / 10
    battery_voltage = registers[17] / 100
    readings = {
        "pv_input_voltage": _reading(registers[1] / 10, "V"),
        "pv2_input_voltage": _reading(registers[2] / 10, "V"),
        "pv_input_power": _reading(round(pv1_power + pv2_power, 1), "W"),
        "pv1_input_power": _reading(pv1_power, "W"),
        "pv2_input_power": _reading(pv2_power, "W"),
        "pv_input_current_for_battery": _reading((registers[7] + registers[8]) / 10, "A"),
        "ac_output_active_power": _reading(_u32(registers, 9) / 10, "W"),
        "ac_output_apparent_power": _reading(_u32(registers, 11) / 10, "VA"),
        "battery_voltage": _reading(battery_voltage, "V"),
        "battery_capacity": _reading(registers[18], "%"),
        "bus_voltage": _reading(registers[19] / 10, "V"),
        "ac_input_voltage": _reading(registers[20] / 10, "V"),
        "ac_input_frequency": _reading(registers[21] / 100, "Hz"),
        "ac_output_voltage": _reading(registers[22] / 10, "V"),
        "ac_output_frequency": _reading(registers[23] / 100, "Hz"),
        "inverter_heat_sink_temperature": _reading(registers[25] / 10, "°C"),
        "dc_dc_temperature": _reading(registers[26] / 10, "°C"),
        "ac_output_load": _reading(registers[27] / 10, "%"),
        "battery_charging_current": _reading(registers[83] / 10, "A"),
        "battery_discharge_current": _reading(round(max(0.0, battery_watts) / battery_voltage, 1) if battery_voltage else 0, "A"),
        "battery_power": _reading(battery_watts, "W"),
        "pv_energy_today": _reading((_u32(registers, 48) + _u32(registers, 52)) / 10, "kWh"),
    }
    status_code = int(registers[0])
    mode = MODBUS_STATUS.get(status_code, f"Status {status_code}")
    warning_word = int(registers[41])
    warnings = {name: _reading(bool(warning_word & (1 << bit))) for bit, name in MODBUS_WARNING_BITS.items()}
    if int(registers[40]):
        warnings["inverter_fault_code"] = _reading(int(registers[40]))
    return readings, mode, warnings


def _modbus_read(client: object, method: str, address: int, count: int, slave_id: int):
    call = getattr(client, method)
    try:
        return call(address=address, count=count, slave=slave_id)
    except TypeError:
        try:
            return call(address=address, count=count, device_id=slave_id)
        except TypeError:
            return call(address=address, count=count, unit=slave_id)


def modbus_crc(payload: bytes) -> bytes:
    """Return the Modbus RTU CRC16 in wire-order (low byte first)."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def decode_raw_modbus_response(response: bytes, slave_id: int, count: int) -> list[int]:
    """Extract a valid function-04 reply, tolerating a locally echoed request."""
    expected_bytes = count * 2
    for offset in range(max(0, len(response) - 4)):
        if response[offset:offset + 3] != bytes((slave_id, 0x04, expected_bytes)):
            continue
        frame_length = 3 + expected_bytes + 2
        frame = response[offset:offset + frame_length]
        if len(frame) != frame_length or modbus_crc(frame[:-2]) != frame[-2:]:
            continue
        data = frame[3:-2]
        return [int.from_bytes(data[index:index + 2], "big") for index in range(0, len(data), 2)]
    preview = response[:32].hex(" ") or "empty"
    raise ConnectionError(f"no valid direct Modbus reply in {len(response)} bytes [{preview}]")


def run_raw_modbus_read(device: str, baud: int = 9600, slave_id: int = 1) -> tuple[dict[str, dict[str, object]], str, dict[str, dict[str, object]]]:
    """Read the Growatt input map without pymodbus serial-port handling."""
    import serial

    count = 91
    request_body = bytes((slave_id, 0x04, 0x00, 0x00, 0x00, count))
    request = request_body + modbus_crc(request_body)
    with DEVICE_IO_LOCK, serial.Serial(device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0.25) as port:
        port.reset_input_buffer()
        port.write(request)
        port.flush()
        response = bytearray()
        deadline = time.monotonic() + 3.0
        expected = 3 + count * 2 + 2
        while time.monotonic() < deadline and len(response) < 1024:
            chunk = port.read(256)
            if chunk:
                response.extend(chunk)
                # Allow an eight-byte local request echo before the reply.
                if len(response) >= expected or len(response) >= expected + len(request):
                    try:
                        registers = decode_raw_modbus_response(bytes(response), slave_id, count)
                        return decode_modbus_input(registers)
                    except ConnectionError:
                        pass
    registers = decode_raw_modbus_response(bytes(response), slave_id, count)
    return decode_modbus_input(registers)


def _valid_modbus_probe(response: bytes, slave_id: int) -> bool:
    """Return true for a CRC-valid normal or exception response from one slave."""
    for offset in range(len(response)):
        if response[offset] != slave_id or offset + 5 > len(response):
            continue
        function = response[offset + 1]
        lengths = []
        if function == 0x04 and offset + 3 <= len(response):
            lengths.append(3 + response[offset + 2] + 2)
        elif function == 0x84:
            lengths.append(5)
        for length in lengths:
            frame = response[offset:offset + length]
            if len(frame) == length and modbus_crc(frame[:-2]) == frame[-2:]:
                return True
    return False


def scan_modbus_addresses(device: str, baud: int = 9600, first: int = 1, last: int = 247) -> list[int]:
    """Probe every documented Modbus address without reading or writing settings."""
    import serial

    found: list[int] = []
    with DEVICE_IO_LOCK, serial.Serial(device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0.05) as port:
        for slave_id in range(first, last + 1):
            started = time.monotonic()
            request_body = bytes((slave_id, 0x04, 0x00, 0x00, 0x00, 0x01))
            request = request_body + modbus_crc(request_body)
            port.reset_input_buffer()
            port.write(request)
            port.flush()
            response = bytearray()
            deadline = started + 0.35
            while time.monotonic() < deadline and len(response) < 128:
                chunk = port.read(64)
                if chunk:
                    response.extend(chunk)
                    if _valid_modbus_probe(bytes(response), slave_id):
                        found.append(slave_id)
                        break
            with STATUS_LOCK:
                STATUS["address_scan"] = {
                    **dict(STATUS.get("address_scan", {})),
                    "running": True,
                    "progress": int(slave_id * 100 / last),
                    "current_address": slave_id,
                    "found_addresses": list(found),
                }
            # Growatt documents an 850 ms minimum command period.
            remaining = 0.85 - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    return found


def start_address_scan() -> None:
    """Run the long read-only address sweep in the background."""
    with STATUS_LOCK:
        current = dict(STATUS.get("address_scan", {}))
        if current.get("running"):
            raise RuntimeError("An RS485 address scan is already running")
        STATUS["address_scan"] = {
            "running": True, "progress": 0, "current_address": 1,
            "found_addresses": [], "started_at": now_iso(), "finished_at": None, "error": None,
        }

    def worker() -> None:
        options = load_options()
        devices = [item for item in candidate_devices(str(options.get("device", "auto"))) if "hidraw" not in item.lower()]
        try:
            if not devices:
                raise FileNotFoundError("No serial RS485 adapter is visible")
            baud = int(options.get("modbus_baud_rate", 9600))
            log(f"Read-only Modbus address scan started on {devices[0]} at {baud} baud")
            found = scan_modbus_addresses(devices[0], baud)
            with STATUS_LOCK:
                STATUS["address_scan"] = {
                    **dict(STATUS.get("address_scan", {})), "running": False, "progress": 100,
                    "current_address": 247, "found_addresses": found, "finished_at": now_iso(), "error": None,
                }
            log(f"Modbus address scan finished; responding addresses: {found or 'none'}", "info" if found else "warning")
        except Exception as exc:
            with STATUS_LOCK:
                STATUS["address_scan"] = {
                    **dict(STATUS.get("address_scan", {})), "running": False,
                    "finished_at": now_iso(), "error": str(exc),
                }
            log(f"Modbus address scan failed: {exc}", "error")
        finally:
            WAKE.set()

    threading.Thread(target=worker, name="growatt-address-scan", daemon=True).start()


def run_modbus_read(device: str, baud: int = 9600, slave_id: int = 1) -> tuple[dict[str, dict[str, object]], str, dict[str, dict[str, object]]]:
    from pymodbus.client import ModbusSerialClient

    client = ModbusSerialClient(port=device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=2)
    library_error = None
    try:
        with DEVICE_IO_LOCK:
            if not client.connect():
                raise ConnectionError("the RS485 adapter could not be opened")
            try:
                response = _modbus_read(client, "read_input_registers", 0, 91, slave_id)
                if response is None or (hasattr(response, "isError") and response.isError()) or not hasattr(response, "registers"):
                    # Some SPF revisions reject a long request even though the same
                    # register range is available in smaller blocks.
                    first = _modbus_read(client, "read_input_registers", 0, 45, slave_id)
                    second = _modbus_read(client, "read_input_registers", 45, 46, slave_id)
                    if all(item is not None and not (hasattr(item, "isError") and item.isError()) and hasattr(item, "registers") for item in (first, second)):
                        response = type("RegisterReply", (), {"registers": list(first.registers) + list(second.registers)})()
            finally:
                client.close()
        if response is None or (hasattr(response, "isError") and response.isError()) or not hasattr(response, "registers"):
            raise ConnectionError(f"no valid Modbus reply from inverter address {slave_id}")
        return decode_modbus_input(list(response.registers))
    except Exception as exc:
        library_error = exc

    try:
        return run_raw_modbus_read(device, baud, slave_id)
    except Exception as raw_exc:
        raise ConnectionError(f"{library_error}; direct serial fallback: {raw_exc}") from raw_exc


def pi_crc(payload: bytes) -> bytes:
    """Return the escaped CRC-16/XMODEM used by Voltronic/Growatt PI links."""
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    result = bytearray(crc.to_bytes(2, "big"))
    for index, byte in enumerate(result):
        if byte in (0x28, 0x0D, 0x0A):
            result[index] = (byte + 1) & 0xFF
    return bytes(result)


def decode_raw_qpigs(response: bytes) -> tuple[dict[str, dict[str, object]], str, dict[str, dict[str, object]]]:
    # USB-to-RS485 service cables can locally echo the request or prefix the
    # inverter reply with line noise.  Select the first complete PI response
    # rather than requiring the receive buffer itself to begin with "(".
    frame = response.rstrip(b"\r\n")
    marker = frame.find(b"(")
    if marker > 0:
        frame = frame[marker:]
    terminator = frame.find(b"\r")
    if terminator >= 0:
        frame = frame[:terminator]
    if len(frame) < 4 or not frame.startswith(b"("):
        preview = response[:32].hex(" ") or "empty"
        raise ValueError(f"no PI frame in {len(response)} received bytes [{preview}]")
    payload = frame[:-2]
    if pi_crc(payload) != frame[-2:]:
        raise ValueError("raw PI response CRC mismatch")
    fields = payload[1:].decode("ascii", errors="strict").split()
    if len(fields) < 16:
        raise ValueError(f"short QPIGS payload ({len(fields)} fields)")
    number = lambda index: float(fields[index])
    readings = {
        "ac_input_voltage": _reading(number(0), "V"),
        "ac_input_frequency": _reading(number(1), "Hz"),
        "ac_output_voltage": _reading(number(2), "V"),
        "ac_output_frequency": _reading(number(3), "Hz"),
        "ac_output_apparent_power": _reading(number(4), "VA"),
        "ac_output_active_power": _reading(number(5), "W"),
        "ac_output_load": _reading(number(6), "%"),
        "bus_voltage": _reading(number(7), "V"),
        "battery_voltage": _reading(number(8), "V"),
        "battery_charging_current": _reading(number(9), "A"),
        "battery_capacity": _reading(number(10), "%"),
        "inverter_heat_sink_temperature": _reading(number(11), "°C"),
        "pv_input_current_for_battery": _reading(number(12), "A"),
        "pv_input_voltage": _reading(number(13), "V"),
        "battery_discharge_current": _reading(number(15), "A"),
    }
    if len(fields) > 19:
        readings["pv_input_power"] = _reading(number(19), "W")
    return readings, "Live", {}


def run_raw_pi_read(device: str, baud: int = 2400) -> tuple[dict[str, dict[str, object]], str, dict[str, dict[str, object]]]:
    import serial

    command = b"QPIGS"
    request = command + pi_crc(command) + b"\r"
    with DEVICE_IO_LOCK, serial.Serial(device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0.35) as port:
        port.reset_input_buffer()
        port.write(request)
        port.flush()
        # Do not stop on a locally echoed request.  Accumulate several serial
        # fragments until a complete parenthesized inverter reply is present.
        response = bytearray()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(response) < 1024:
            chunk = port.read(256)
            if chunk:
                response.extend(chunk)
                marker = response.find(b"(")
                if marker >= 0 and response.find(b"\r", marker) >= 0:
                    break
    return decode_raw_qpigs(bytes(response))


def decode_modbus_holding(registers: list[int]) -> dict[str, dict[str, object]]:
    if len(registers) < 114:
        raise ValueError(f"short Growatt settings response ({len(registers)} registers)")
    output_sources = {0: "Battery first", 1: "PV first", 2: "Utility first", 3: "PV and utility first"}
    charge_sources = {0: "PV first", 1: "PV and utility", 2: "PV only"}
    battery_types = {0: "AGM", 1: "Flooded", 2: "User", 3: "Lithium", 4: "User 2"}
    return {
        "output_source_priority": _reading(output_sources.get(registers[1], registers[1])),
        "charger_source_priority": _reading(charge_sources.get(registers[2], registers[2])),
        "ac_input_mode": _reading({0: "Appliance", 1: "UPS", 2: "Generator"}.get(registers[8], registers[8])),
        "output_voltage": _reading({0: 208, 1: 230, 2: 240, 3: 220, 4: 100, 5: 110, 6: 120}.get(registers[18], registers[18]), "V"),
        "output_frequency": _reading(50 if registers[19] == 0 else 60, "Hz"),
        "max_charging_current": _reading(registers[34], "A"),
        "battery_bulk_charge_voltage": _reading(registers[35] / 10, "V"),
        "battery_float_charge_voltage": _reading(registers[36] / 10, "V"),
        "battery_low_to_utility": _reading(registers[37] / 10, "V"),
        "max_utility_charging_current": _reading(registers[38], "A"),
        "battery_type": _reading(battery_types.get(registers[39], registers[39])),
        "battery_cutoff_voltage": _reading(registers[82] / 10, "V"),
        "battery_return_voltage": _reading(registers[95] / 10, "V"),
        "modbus_version": _reading(registers[73] / 100),
        "communication_address": _reading(registers[30]),
    }


def run_modbus_settings(device: str, baud: int = 9600, slave_id: int = 1) -> dict[str, dict[str, object]]:
    from pymodbus.client import ModbusSerialClient

    client = ModbusSerialClient(port=device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=2)
    with DEVICE_IO_LOCK:
        if not client.connect():
            raise ConnectionError("the RS485 adapter could not be opened")
        try:
            response = _modbus_read(client, "read_holding_registers", 0, 114, slave_id)
        finally:
            client.close()
    if response is None or (hasattr(response, "isError") and response.isError()) or not hasattr(response, "registers"):
        raise ConnectionError(f"no valid settings reply from inverter address {slave_id}")
    return decode_modbus_holding(list(response.registers))


def _register_ascii(registers: list[int]) -> str:
    payload = b"".join(int(value).to_bytes(2, "big") for value in registers)
    return payload.replace(b"\x00", b"").replace(b"\xff", b"").decode("ascii", errors="replace").strip()


def run_modbus_firmware(device: str, baud: int = 9600, slave_id: int = 1) -> dict[str, dict[str, object]]:
    from pymodbus.client import ModbusSerialClient

    client = ModbusSerialClient(port=device, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=2)
    with DEVICE_IO_LOCK:
        if not client.connect():
            raise ConnectionError("the RS485 adapter could not be opened")
        try:
            version_response = _modbus_read(client, "read_holding_registers", 9, 6, slave_id)
            serial_response = _modbus_read(client, "read_holding_registers", 23, 5, slave_id)
        finally:
            client.close()
    for response in (version_response, serial_response):
        if response is None or (hasattr(response, "isError") and response.isError()) or not hasattr(response, "registers"):
            raise ConnectionError(f"no valid identity reply from inverter address {slave_id}")
    versions = list(version_response.registers)
    return {
        "firmware_version": _reading(_register_ascii(versions[:3])),
        "control_firmware_version": _reading(_register_ascii(versions[3:6])),
        "serial_number": _reading(_register_ascii(list(serial_response.registers))),
    }

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
    try:
        stat = OPTIONS_PATH.stat()
    except OSError:
        OPTIONS_CACHE.update({"path": str(OPTIONS_PATH), "mtime_ns": None, "value": {}})
        return {}
    if OPTIONS_CACHE.get("path") == str(OPTIONS_PATH) and OPTIONS_CACHE.get("mtime_ns") == stat.st_mtime_ns:
        return dict(OPTIONS_CACHE.get("value", {}))
    try:
        value = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("app options must be a JSON object")
        OPTIONS_CACHE.update({"path": str(OPTIONS_PATH), "mtime_ns": stat.st_mtime_ns, "value": value})
        return dict(value)
    except (OSError, ValueError) as exc:
        OPTIONS_CACHE.update({"path": str(OPTIONS_PATH), "mtime_ns": stat.st_mtime_ns, "value": {}})
        log(f"Could not read app options: {exc}", "error")
        return {}


def normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_")


def candidate_devices(configured: str = "auto") -> list[str]:
    if configured and configured != "auto":
        return [configured]
    stable = sorted(glob.glob("/dev/serial/by-id/*"))
    excluded_words = ("canable", "openlight", "u-blox", "ublox", "gnss", "gps")
    excluded_stable = [p for p in stable if any(word in p.lower() for word in excluded_words)]
    excluded_targets = {os.path.realpath(p) for p in excluded_stable}
    growatt_words = ("growatt", "usb-serial", "ch340", "ch341", "cp2102", "cp210x", "ftdi", "exar")
    growatt_stable = [
        p for p in stable
        if p not in excluded_stable and any(word in p.lower() for word in growatt_words)
    ]
    other_stable = [p for p in stable if p not in growatt_stable and p not in excluded_stable]
    serial = [
        p for p in sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
        if os.path.realpath(p) not in excluded_targets
    ]
    hid = sorted(glob.glob("/dev/hidraw*"))
    # A stable by-id link and /dev/ttyUSB0 commonly name the same adapter.
    # Probe each physical target only once so one cable is not reported twice.
    result = []
    seen_targets = set()
    for path in growatt_stable + serial + other_stable + hid:
        target = os.path.realpath(path)
        if target in seen_targets:
            continue
        seen_targets.add(target)
        result.append(path)
    return result


def cached_candidate_devices(configured: str = "auto", max_age: float = 5.0) -> list[str]:
    """Avoid repeated /dev globbing for every dashboard request."""
    now = time.monotonic()
    if DEVICE_CACHE.get("configured") == configured and now - float(DEVICE_CACHE.get("at", 0.0)) < max_age:
        return list(DEVICE_CACHE.get("devices", []))
    devices = candidate_devices(configured)
    DEVICE_CACHE.update({"configured": configured, "at": now, "devices": list(devices)})
    return devices


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
        if isinstance(raw_value, dict) and "value" in raw_value:
            value = raw_value.get("value")
            unit = raw_value.get("unit") or None
        elif isinstance(raw_value, (list, tuple)):
            value = raw_value[0] if raw_value else None
            unit = raw_value[1] if len(raw_value) > 1 and raw_value[1] else None
        else:
            value = raw_value
            unit = None

        diagnostic = str(value or "").strip().lower()
        if key in {"error", "_failed_status_commands"}:
            raise ValueError(f"the inverter rejected the inquiry ({value})")
        if key == "validity_check":
            if any(marker in diagnostic for marker in ("error", "empty", "invalid", "nak", "fail", "timeout")):
                raise ValueError(f"the inverter returned no valid response ({value})")
            continue
        if key.startswith("_") or key == "raw_response":
            continue
        result[key] = {"value": value, "unit": unit}
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
    modbus_baud = int(options.get("modbus_baud_rate", 9600))
    slave_id = int(options.get("modbus_slave_id", 1))
    selected_protocol = str(options.get("protocol", "auto"))
    selected_transport = str(options.get("transport", "auto"))
    failures = []
    for device in candidates:
        if not Path(device).exists():
            failures.append(f"{device}: path does not exist")
            continue
        if "hidraw" not in device.lower() and selected_protocol in {"auto", MODBUS_PROTOCOL} and selected_transport in {"auto", "serial", "modbus_rtu"}:
            modbus_bauds = list(dict.fromkeys([modbus_baud, 9600, 19200]))
            with STATUS_LOCK:
                scan_addresses = list(dict(STATUS.get("address_scan", {})).get("found_addresses", []))
            slave_ids = list(dict.fromkeys([*scan_addresses, slave_id, 1, 2]))
            for test_baud in modbus_bauds:
                for test_slave in slave_ids:
                    try:
                        readings, mode, _warnings = run_modbus_read(device, test_baud, test_slave)
                        identity = {
                            "connection": _reading("Growatt RS485 Modbus RTU v0.14"),
                            "inverter_address": _reading(test_slave),
                            "active_baud": _reading(test_baud),
                            "initial_mode": _reading(mode),
                            "registers_received": _reading(len(readings)),
                        }
                        return device, MODBUS_PROTOCOL, identity
                    except Exception as exc:
                        failures.append(f"{device} / Modbus {test_baud} baud address {test_slave}: {exc}")
        if selected_protocol == MODBUS_PROTOCOL or selected_transport == "modbus_rtu":
            continue
        if "hidraw" not in device.lower() and selected_protocol == "auto" and selected_transport in {"auto", "serial"}:
            for test_baud in dict.fromkeys([baud, 2400, 9600, 19200, 115200]):
                try:
                    readings, mode, _warnings = run_raw_pi_read(device, test_baud)
                    return device, RAW_PI_PROTOCOL, {
                        "connection": _reading("Growatt direct serial PI"),
                        "active_baud": _reading(test_baud),
                        "initial_mode": _reading(mode),
                        "fields_received": _reading(len(readings)),
                    }
                except Exception as exc:
                    failures.append(f"{device} / raw PI {test_baud} baud: {exc}")
        for protocol in protocols(str(options.get("protocol", "auto"))):
            profile = PROTOCOL_PROFILES.get(protocol, PROTOCOL_PROFILES["PI30"])
            try:
                identity = run_query(
                    device, protocol, baud, str(profile["identity"]), timeout=9,
                    transport=str(options.get("transport", "auto")),
                )
                return device, protocol, identity
            except Exception as exc:
                identity_error = exc
            try:
                # Some SPF firmware revisions do not implement QPI even though
                # their read-only QPIGS telemetry command works normally.
                run_query(
                    device, protocol, baud, str(profile["live"]), timeout=9,
                    transport=str(options.get("transport", "auto")),
                )
                return device, protocol, {
                    "protocol_probe": {"value": "live status", "unit": None},
                    "identity_note": {"value": f"Identity inquiry unavailable: {identity_error}", "unit": None},
                }
            except Exception as live_exc:
                failures.append(f"{device} / {protocol}: identity {identity_error}; live status {live_exc}")
    summary = " | ".join(failures[:8]) if failures else "no candidates responded"
    raise ConnectionError(f"USB/RS485 devices were found, but no Growatt response was received ({summary})")


def publish_state(
    entity_id: str,
    value: object,
    attributes: dict[str, object],
    min_interval_seconds: int = 30,
) -> bool:
    if not TOKEN:
        return False
    now = time.monotonic()
    signature = json.dumps(attributes, sort_keys=True, separators=(",", ":"), default=str)
    previous = PUBLISHED_STATES.get(entity_id)
    if previous:
        previous_value, previous_signature, previous_at = previous
        unchanged = previous_value == value and previous_signature == signature
        minimum = ENTITY_HEARTBEAT_SECONDS if unchanged else max(0, min_interval_seconds)
        if now - previous_at < minimum:
            return False
    request = urllib.request.Request(
        f"{API_BASE}/{entity_id}",
        data=json.dumps({"state": value, "attributes": attributes}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
        PUBLISHED_STATES[entity_id] = (value, signature, now)
        return True
    except (urllib.error.URLError, TimeoutError) as exc:
        log(f"Home Assistant state update failed: {exc}", "warning")
        return False


def publish_sensor(key: str, value: object, unit: str | None = None, min_interval_seconds: int = 30) -> None:
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
    publish_state(f"sensor.{ENTITY_PREFIX}_{key}", value, attributes, min_interval_seconds)


def publish_all(
    readings: dict[str, dict[str, object]],
    status: dict[str, object],
    enabled: bool,
    min_interval_seconds: int = 30,
) -> None:
    if not enabled:
        return
    for key in SENSORS:
        if key in readings:
            publish_sensor(key, readings[key].get("value"), readings[key].get("unit"), min_interval_seconds)
    publish_sensor("mode", status.get("mode", "Unknown"), min_interval_seconds=min_interval_seconds)
    publish_state(
        f"binary_sensor.{ENTITY_PREFIX}_connected",
        "on" if status.get("connected") else "off",
        {"friendly_name": "Growatt USB Connected", "device_class": "connectivity", "icon": "mdi:usb-port", "device": status.get("device"), "protocol": status.get("protocol")},
        0,
    )


def setting_catalog(options: dict[str, object]) -> dict[str, object]:
    writable = not bool(options.get("read_only", True)) and bool(options.get("allow_setting_changes", False))
    with STATUS_LOCK:
        active_protocol = STATUS.get("protocol")
    if active_protocol == MODBUS_PROTOCOL:
        writable = False
        locked_reason = "RS485 configuration read-back is supported. Register writes remain safety-locked until the exact inverter revision is verified."
    else:
        locked_reason = None if writable else "Disable read-only mode and enable setting changes in the app Configuration page."
    return {
        "writable": writable,
        "locked_reason": locked_reason,
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
    if str(protocol) == MODBUS_PROTOCOL:
        settings = run_modbus_settings(
            str(device), int(options.get("modbus_baud_rate", 9600)), int(options.get("modbus_slave_id", 1)),
        )
        with STATUS_LOCK:
            STATUS.update({"settings": settings, "settings_updated_at": now_iso()})
        log("Growatt RS485 settings profile refreshed")
        return settings
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
    if str(protocol) == MODBUS_PROTOCOL:
        firmware = run_modbus_firmware(
            str(device), int(options.get("modbus_baud_rate", 9600)), int(options.get("modbus_slave_id", 1)),
        )
        with STATUS_LOCK:
            STATUS.update({"firmware": firmware, "firmware_updated_at": now_iso()})
        log("Growatt RS485 firmware versions refreshed")
        return firmware
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


def save_energy(energy: dict[str, object]) -> bool:
    global LAST_ENERGY_SAVE
    try:
        STATE_PATH.write_text(json.dumps(energy), encoding="utf-8")
        LAST_ENERGY_SAVE = time.monotonic()
        return True
    except OSError as exc:
        log(f"Could not save daily energy estimate: {exc}", "warning")
        return False


def update_energy(energy: dict[str, object], readings: dict[str, dict[str, object]], poll_interval: int) -> None:
    global LAST_ENERGY_SAVE
    today = datetime.now().astimezone().date().isoformat()
    date_changed = energy.get("date") != today
    if date_changed:
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
    if date_changed or time.monotonic() - LAST_ENERGY_SAVE >= ENERGY_SAVE_INTERVAL_SECONDS:
        save_energy(energy)


def retry_delay(poll_interval: int, failures: int, maximum: int = 300) -> int:
    """Back off expensive protocol discovery while remaining manually wakeable."""
    exponent = min(max(0, failures - 1), 8)
    return min(maximum, max(poll_interval, poll_interval * (2 ** exponent)))


def classify_health(
    status: dict[str, object],
    options: dict[str, object],
    devices: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    if devices is None:
        devices = candidate_devices(str(options.get("device", "auto")))
    if not devices:
        return "usb_missing", "No compatible USB or RS485 adapter is visible to the app.", [
            "Reconnect the Growatt RJ45-to-USB RS485 cable at both ends.",
            "The USB end should appear as a CP2102, CH340, FTDI, Exar, or similar serial adapter.",
            "Reconnect the cable, then press Rescan connection or restart the app.",
        ]
    if not status.get("connected"):
        return "no_response", "A USB/RS485 device is visible, but the inverter is not answering.", [
            "Stop the old manual Growatt service or integration so only this app owns the USB port.",
            "For the Growatt RJ45 RS485 cable, use Growatt Modbus RTU v0.14, address 1, and 9600 baud.",
            "Confirm the RJ45 plug is in the inverter RS485 port, not the BMS/CAN port.",
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
    return "healthy", "Local Growatt telemetry is live and updating normally.", [
        "The Growatt connection is responding.",
        "Live inverter values are updating in Home Assistant.",
        safety_status,
    ]


def dashboard_status() -> dict[str, object]:
    options = load_options()
    with STATUS_LOCK:
        result = dict(STATUS)
        result["readings"] = dict(STATUS.get("readings", {}))
        result["warnings"] = dict(STATUS.get("warnings", {}))
    devices = cached_candidate_devices(str(options.get("device", "auto")))
    health, diagnosis, steps = classify_health(result, options, devices)
    result.update({
        "health": health, "diagnosis": diagnosis, "troubleshooting": steps,
        "read_only": bool(options.get("read_only", True)), "uptime_seconds": int(time.time() - STARTED_AT),
        "detected_devices": devices,
        "configured_device": options.get("device", "auto"),
        "configured_transport": options.get("transport", "auto"),
        "configured_protocol": options.get("protocol", "auto"),
        "baud_rate": int(result.get("active_baud") or (options.get("modbus_baud_rate", 9600) if result.get("protocol") == MODBUS_PROTOCOL else options.get("baud_rate", 2400))),
        "modbus_slave_id": int(result.get("active_slave_id") or options.get("modbus_slave_id", 1)),
        "poll_interval_seconds": int(options.get("poll_interval_seconds", 10)),
        "events": list(EVENTS)[:30], "server_time": now_iso(),
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
            DEVICE_CACHE.update({"configured": None, "at": 0.0, "devices": []})
            WAKE.set()
            self.send_json({"ok": True, "message": "USB rescan requested"}, 202)
            return
        if path.endswith("/api/address-scan"):
            try:
                start_address_scan()
                self.send_json({"ok": True, "message": "Read-only addresses 1-247 scan started"}, 202)
            except RuntimeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 409)
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
    publish_interval = max(5, int(options.get("home_assistant_publish_interval_seconds", 30)))
    energy = load_energy()
    device = None
    protocol = None
    active_baud = None
    active_slave_id = None
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
        wait_seconds = interval
        with STATUS_LOCK:
            STATUS["last_attempt_at"] = now_iso()
        try:
            if not device or not Path(device).exists():
                device, protocol, last_identity = discover(options)
                active_baud = int(last_identity.get("active_baud", {}).get("value", options.get("baud_rate", 2400)))
                active_slave_id = int(last_identity.get("inverter_address", {}).get("value", options.get("modbus_slave_id", 1)))
                connection_name = "Growatt RS485 Modbus RTU" if protocol == MODBUS_PROTOCOL else protocol
                log(f"Connected to Growatt on {device} using {connection_name}")
            profile = PROTOCOL_PROFILES.get(protocol, PROTOCOL_PROFILES["PI30"])
            warnings: dict[str, dict[str, object]] = {}
            if protocol == MODBUS_PROTOCOL:
                readings, mode, warnings = run_modbus_read(
                    device, int(active_baud or options.get("modbus_baud_rate", 9600)), int(active_slave_id or options.get("modbus_slave_id", 1)),
                )
                last_slow_poll = time.time()
            elif protocol == RAW_PI_PROTOCOL:
                readings, mode, warnings = run_raw_pi_read(device, int(active_baud or options.get("baud_rate", 2400)))
            else:
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
                if profile.get("warnings") and time.time() - last_slow_poll >= 60:
                    last_slow_poll = time.time()
                    try:
                        warnings = run_query(
                            device, protocol, int(options.get("baud_rate", 2400)), str(profile["warnings"]), timeout=8,
                            transport=str(options.get("transport", "auto")),
                        )
                    except Exception as exc:
                        log(f"Optional warning inquiry was not accepted: {exc}", "warning")
            current_settings: dict[str, dict[str, object]] = {}
            if (protocol == MODBUS_PROTOCOL or profile.get("settings")) and time.time() - last_settings_poll >= SETTINGS_REFRESH_SECONDS:
                last_settings_poll = time.time()
                try:
                    if protocol == MODBUS_PROTOCOL:
                        current_settings = run_modbus_settings(
                            device, int(options.get("modbus_baud_rate", 9600)), int(options.get("modbus_slave_id", 1)),
                        )
                    else:
                        current_settings = run_query(
                            device, protocol, int(options.get("baud_rate", 2400)), str(profile["settings"]), timeout=12,
                            transport=str(options.get("transport", "auto")),
                        )
                except Exception as exc:
                    log(f"Optional settings inquiry was not accepted: {exc}", "warning")
            current_firmware: dict[str, dict[str, object]] = {}
            if time.time() - last_firmware_poll >= FIRMWARE_REFRESH_SECONDS:
                last_firmware_poll = time.time()
                try:
                    if protocol == MODBUS_PROTOCOL:
                        current_firmware = run_modbus_firmware(
                            device, int(options.get("modbus_baud_rate", 9600)), int(options.get("modbus_slave_id", 1)),
                        )
                    else:
                        current_firmware = read_firmware_now()
                except Exception as exc:
                    log(f"Optional firmware version inquiry was not accepted: {exc}", "warning")
            update_energy(energy, readings, interval)
            with STATUS_LOCK:
                existing_warnings = STATUS.get("warnings", {})
                STATUS.update({
                    "connected": True, "device": device, "protocol": protocol,
                    "active_baud": active_baud, "active_slave_id": active_slave_id,
                    "identity": last_identity, "mode": mode, "readings": readings,
                    "warnings": warnings or existing_warnings, "last_success_at": now_iso(),
                    "last_success_epoch": time.time(), "last_error": None,
                    "retry_in_seconds": None,
                    "consecutive_failures": 0,
                    "successful_polls": int(STATUS.get("successful_polls", 0)) + 1,
                })
                if current_settings:
                    STATUS.update({"settings": current_settings, "settings_updated_at": now_iso()})
                if current_firmware:
                    STATUS.update({"firmware": current_firmware, "firmware_updated_at": now_iso()})
                snapshot = dict(STATUS)
            publish_all(readings, snapshot, publish_enabled, publish_interval)
        except Exception as exc:
            with STATUS_LOCK:
                failures = int(STATUS.get("consecutive_failures", 0)) + 1
                wait_seconds = retry_delay(interval, failures)
                STATUS.update({
                    "connected": False, "last_error": str(exc), "consecutive_failures": failures,
                    "retry_in_seconds": wait_seconds,
                })
                snapshot = dict(STATUS)
            log(f"Growatt USB poll failed: {exc}", "warning")
            publish_all({}, snapshot, publish_enabled, publish_interval)
            device = None
            protocol = None
            active_baud = None
            active_slave_id = None
        WAKE.wait(wait_seconds)
        WAKE.clear()
    save_energy(energy)


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
        time.sleep(5)
    server.shutdown()
    worker.join(timeout=5)
    log("Baiamonte Growatt USB stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
