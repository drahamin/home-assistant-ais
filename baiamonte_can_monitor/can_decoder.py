"""Decode Growatt BMS CAN v1.04 broadcasts (11-bit IDs, big-endian fields)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    value: object
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None


PROTECTION_BITS = {
    15: "Over-temperature discharge",
    14: "Over-temperature charge",
    13: "Under-temperature discharge",
    12: "Under-temperature charge",
    11: "System error",
    10: "Cell voltage difference fault",
    7: "Discharge over-current",
    6: "Charge over-current",
    5: "Short-circuit discharge",
    4: "Cell over-voltage",
    3: "Cell under-voltage",
    2: "Module over-voltage",
    1: "Module under-voltage",
    0: "Soft-start failure",
}

ALARM_BITS = {
    15: "Over-temperature discharge",
    14: "Over-temperature charge",
    13: "Under-temperature discharge",
    12: "Under-temperature charge",
    11: "Cell voltage difference warning",
    10: "Pack preparing to turn off",
    9: "Internal communication failure",
    7: "Discharge over-current",
    6: "Charge over-current",
    5: "Cell over-voltage",
    4: "Cell under-voltage",
    3: "Module over-voltage",
    2: "Module under-voltage",
}


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big", signed=False)


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big", signed=True)


def _flags(value: int, names: dict[int, str]) -> str:
    active = [name for bit, name in names.items() if value & (1 << bit)]
    return ", ".join(active) if active else "None"


def _measurement(value: float, unit: str, device_class: str | None = None) -> Reading:
    return Reading(value, unit, device_class, "measurement")


def decode_frame(arbitration_id: int, payload: bytes) -> dict[str, Reading]:
    """Return decoded readings for one frame. Unknown/short frames return nothing."""
    data = bytes(payload)
    if len(data) < 8:
        return {}

    if arbitration_id == 0x311:
        status_code = data[6] & 0x03
        status = {0: "idle", 1: "charging", 2: "standby", 3: "discharging"}[status_code]
        return {
            "charge_voltage_limit": _measurement(_u16(data, 0) / 10, "V", "voltage"),
            "charge_current_limit": _measurement(_u16(data, 2) / 10, "A", "current"),
            "discharge_current_limit": _measurement(_u16(data, 4) / 10, "A", "current"),
            "battery_status": Reading(status),
        }

    if arbitration_id == 0x312:
        protections = _u16(data, 0)
        alarms = _u16(data, 2)
        return {
            "protection_flags": Reading(_flags(protections, PROTECTION_BITS)),
            "alarm_flags": Reading(_flags(alarms, ALARM_BITS)),
            "protection_active": Reading("on" if protections else "off"),
            "alarm_active": Reading("on" if alarms else "off"),
        }

    if arbitration_id == 0x313:
        voltage = _s16(data, 0) / 100
        current = _s16(data, 2) / 10
        return {
            "battery_voltage": _measurement(voltage, "V", "voltage"),
            "battery_current": _measurement(current, "A", "current"),
            "battery_power": _measurement(round(voltage * current, 1), "W", "power"),
            "maximum_cell_temperature": _measurement(_s16(data, 4) / 10, "°C", "temperature"),
            "battery_soc": Reading(data[6], "%", "battery", "measurement"),
            "battery_soh": Reading(data[7], "%", None, "measurement"),
        }

    if arbitration_id == 0x314:
        return {
            "remaining_capacity": _measurement(_u16(data, 0) / 100, "Ah"),
            "full_charge_capacity": _measurement(_u16(data, 2) / 100, "Ah"),
            "cell_voltage_difference": _measurement(_u16(data, 4), "mV", "voltage"),
            "cycle_count": Reading(_u16(data, 6), None, None, "total_increasing"),
        }

    if 0x315 <= arbitration_id <= 0x318:
        first_cell = 1 + (arbitration_id - 0x315) * 4
        return {
            f"cell_{first_cell + index}_voltage": _measurement(
                _u16(data, index * 2) / 1000, "V", "voltage"
            )
            for index in range(4)
        }

    if arbitration_id == 0x319:
        request = data[0]
        battery_type_code = request & 0x03
        battery_type = {0: "LiFePO4", 1: "NCM", 2: "LTO", 3: "Reserved"}[battery_type_code]
        return {
            "charge_enabled": Reading("on" if request & 0x80 else "off"),
            "discharge_enabled": Reading("on" if request & 0x40 else "off"),
            "force_charge_request_1": Reading("on" if request & 0x20 else "off"),
            "force_charge_request_2": Reading("on" if request & 0x10 else "off"),
            "battery_chemistry": Reading(battery_type),
            "maximum_cell_voltage": _measurement(_u16(data, 1) / 1000, "V", "voltage"),
            "minimum_cell_voltage": _measurement(_u16(data, 3) / 1000, "V", "voltage"),
            "maximum_cell_number": Reading(data[5]),
            "minimum_cell_number": Reading(data[6]),
            "protected_pack_id": Reading(data[7]),
        }

    if arbitration_id == 0x320:
        manufacturer = data[0:2].decode("ascii", errors="replace").strip("\x00 ")
        return {
            "battery_manufacturer": Reading(manufacturer or "Unknown"),
            "battery_hardware_version": Reading(str(data[2])),
            "battery_software_version": Reading(str(data[3])),
        }

    return {}
