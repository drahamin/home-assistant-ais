"""Read-only Felicity LPBA battery polling over Modbus RTU/RS-485."""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial

from can_decoder import Reading


def modbus_crc(payload: bytes) -> int:
    crc = 0xFFFF
    for value in payload:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def read_request(address: int, register: int, count: int) -> bytes:
    payload = bytes((address, 0x03)) + register.to_bytes(2, "big") + count.to_bytes(2, "big")
    return payload + modbus_crc(payload).to_bytes(2, "little")


def parse_read_response(buffer: bytes, address: int, expected_bytes: int) -> tuple[bytes, bytes] | None:
    """Find one CRC-valid Modbus read response in a possibly noisy buffer."""
    frame_length = expected_bytes + 5
    header = bytes((address, 0x03, expected_bytes))
    start = 0
    while True:
        offset = buffer.find(header, start)
        if offset < 0:
            return None
        frame = buffer[offset : offset + frame_length]
        if len(frame) < frame_length:
            return None
        received_crc = int.from_bytes(frame[-2:], "little")
        if received_crc == modbus_crc(frame[:-2]):
            return frame, frame[3:-2]
        start = offset + 1


def _measurement(value: float, unit: str, device_class: str | None = None) -> Reading:
    return Reading(value, unit, device_class, "measurement")


def decode_battery_information(data: bytes) -> dict[str, Reading]:
    if len(data) != 20:
        return {}
    voltage = int.from_bytes(data[8:10], "big") * 0.01
    current = int.from_bytes(data[10:12], "big", signed=True) * 0.1
    soc = int.from_bytes(data[18:20], "big")
    if not (20 <= voltage <= 70 and -500 <= current <= 500 and 0 <= soc <= 100):
        return {}
    status = "charging" if current > 0.05 else "discharging" if current < -0.05 else "idle"
    return {
        "battery_voltage": _measurement(round(voltage, 2), "V", "voltage"),
        "battery_current": _measurement(round(current, 1), "A", "current"),
        "battery_power": _measurement(round(voltage * current, 1), "W", "power"),
        "battery_soc": Reading(soc, "%", "battery", "measurement"),
        "battery_status": Reading(status),
    }


def decode_cell_information(data: bytes) -> dict[str, Reading]:
    if len(data) != 40:
        return {}
    readings: dict[str, Reading] = {}
    for index in range(16):
        voltage = int.from_bytes(data[index * 2 : index * 2 + 2], "big") * 0.001
        if 1.5 <= voltage <= 5.0:
            readings[f"cell_{index + 1}_voltage"] = _measurement(round(voltage, 3), "V", "voltage")
    for index in range(4):
        temperature = int.from_bytes(data[32 + index * 2 : 34 + index * 2], "big", signed=True)
        if -50 <= temperature <= 150:
            readings[f"temperature_{index + 1}"] = _measurement(temperature, "°C", "temperature")
    return readings


@dataclass(frozen=True)
class Rs485Message:
    identifier: str
    data: bytes
    decoded: dict[str, Reading]
    address: int
    is_error_frame: bool = False
    is_remote_frame: bool = False


class FelicityRs485Receiver:
    """Cycle through the three known read-only Felicity register blocks."""

    monitor_transport = "felicity_rs485"
    COMMANDS = (
        (0xF80B, 1, "BMS version"),
        (0x1302, 10, "pack information"),
        (0x132A, 20, "cell information"),
    )

    def __init__(self, path: str, baudrate: int, addresses: list[int]) -> None:
        if not addresses:
            raise ValueError("at least one Felicity battery address is required")
        self.path = path
        self.baudrate = baudrate
        self.addresses = addresses
        self._serial = serial.Serial(
            path,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=0.5,
        )
        self._polls = [
            (address, register, count, label)
            for address in addresses
            for register, count, label in self.COMMANDS
        ]
        self._poll_index = 0
        self.last_error: str | None = None

    def _namespaced(self, address: int, readings: dict[str, Reading]) -> dict[str, Reading]:
        if address == self.addresses[0]:
            return readings
        return {f"battery_{address}_{key}": value for key, value in readings.items()}

    def recv(self, timeout: float = 1.0) -> Rs485Message | None:
        address, register, count, label = self._polls[self._poll_index]
        self._poll_index = (self._poll_index + 1) % len(self._polls)
        expected_bytes = count * 2
        request = read_request(address, register, count)
        self._serial.reset_input_buffer()
        self._serial.write(request)
        self._serial.flush()

        deadline = time.monotonic() + max(0.15, timeout)
        buffer = bytearray()
        parsed = None
        while time.monotonic() < deadline:
            chunk = self._serial.read(max(1, min(256, self._serial.in_waiting or 1)))
            if chunk:
                buffer.extend(chunk)
                if len(buffer) > 4096:
                    del buffer[:-4096]
                parsed = parse_read_response(bytes(buffer), address, expected_bytes)
                if parsed:
                    break

        if not parsed:
            self.last_error = (
                f"no CRC-valid reply from battery {address} for {label}; "
                f"received {len(buffer)} byte(s)"
            )
            return None

        frame, data = parsed
        if register == 0xF80B:
            readings = {"bms_version": Reading(int.from_bytes(data, "big"))}
        elif register == 0x1302:
            readings = decode_battery_information(data)
        else:
            readings = decode_cell_information(data)
        if not readings:
            self.last_error = f"battery {address} returned implausible {label} values"
            return None
        self.last_error = None
        return Rs485Message(
            identifier=f"B{address}:0x{register:04X}",
            data=frame,
            decoded=self._namespaced(address, readings),
            address=address,
        )

    def shutdown(self) -> None:
        self._serial.close()
