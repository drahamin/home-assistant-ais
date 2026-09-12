"""Derived readings for a parallel bank of equal Felicity LPBA48100 packs."""

from __future__ import annotations

from can_decoder import Reading


PACK_CAPACITY_AH = 100.0
PACK_NOMINAL_ENERGY_KWH = 5.12


def _measurement(value: float, unit: str, device_class: str | None = None) -> Reading:
    return Reading(value, unit, device_class, "measurement")


def _duration_hours(energy_kwh: float, power_w: float) -> Reading:
    """Return a bounded runtime estimate, or unavailable when power is inactive."""
    if power_w <= 5.0:
        return Reading("unavailable", "h", "duration", "measurement")
    hours = min(240.0, max(0.0, energy_kwh * 1000.0 / power_w))
    return Reading(round(hours, 1), "h", "duration", "measurement")


def derive_bank_readings(
    readings: dict[str, Reading],
    addresses: list[int],
    online_addresses: set[int],
) -> dict[str, Reading]:
    """Build per-pack health/capacity and equal-pack parallel-bank totals."""
    derived: dict[str, Reading] = {}
    packs: list[dict[str, float]] = []

    for address in addresses:
        prefix = f"battery_{address}_"
        online = address in online_addresses
        derived[f"battery_{address}_online"] = Reading("on" if online else "off")
        soc_reading = readings.get(prefix + "battery_soc")
        voltage_reading = readings.get(prefix + "battery_voltage")
        current_reading = readings.get(prefix + "battery_current")
        power_reading = readings.get(prefix + "battery_power")
        if soc_reading is not None:
            soc = float(soc_reading.value)
            derived[prefix + "remaining_capacity"] = _measurement(round(PACK_CAPACITY_AH * soc / 100, 1), "Ah")
            derived[prefix + "remaining_energy"] = _measurement(
                round(PACK_NOMINAL_ENERGY_KWH * soc / 100, 3), "kWh", "energy"
            )
        if power_reading is not None:
            pack_power = float(power_reading.value)
            derived[prefix + "charging_power"] = _measurement(round(max(pack_power, 0.0), 1), "W", "power")
            derived[prefix + "discharging_power"] = _measurement(round(max(-pack_power, 0.0), 1), "W", "power")
            if soc_reading is not None:
                remaining_kwh = PACK_NOMINAL_ENERGY_KWH * float(soc_reading.value) / 100.0
                derived[prefix + "time_to_empty"] = _duration_hours(remaining_kwh, max(-pack_power, 0.0))
                derived[prefix + "time_to_full"] = _duration_hours(
                    PACK_NOMINAL_ENERGY_KWH - remaining_kwh, max(pack_power, 0.0)
                )
        if online and all(item is not None for item in (soc_reading, voltage_reading, current_reading, power_reading)):
            packs.append({
                "soc": float(soc_reading.value),
                "voltage": float(voltage_reading.value),
                "current": float(current_reading.value),
                "power": float(power_reading.value),
            })

    configured = len(addresses)
    online = len(online_addresses)
    derived["bank_configured_batteries"] = Reading(configured)
    derived["bank_online_batteries"] = Reading(online)
    derived["bank_nominal_capacity"] = _measurement(configured * PACK_CAPACITY_AH, "Ah")
    derived["bank_nominal_energy"] = Reading(round(configured * PACK_NOMINAL_ENERGY_KWH, 2), "kWh")
    derived["bank_all_batteries_online"] = Reading("on" if configured > 0 and online == configured else "off")

    if not packs:
        derived["bank_health"] = Reading("offline")
        return derived

    voltage = round(sum(pack["voltage"] for pack in packs) / len(packs), 2)
    current = round(sum(pack["current"] for pack in packs), 1)
    power = round(sum(pack["power"] for pack in packs), 1)
    soc = round(sum(pack["soc"] for pack in packs) / len(packs), 1)
    soc_difference = round(max(pack["soc"] for pack in packs) - min(pack["soc"] for pack in packs), 1)
    cell_spreads = [
        float(readings[f"battery_{address}_cell_voltage_difference"].value)
        for address in online_addresses
        if f"battery_{address}_cell_voltage_difference" in readings
    ]
    maximum_cell_spread = max(cell_spreads, default=0.0)
    remaining_energy = sum(PACK_NOMINAL_ENERGY_KWH * pack["soc"] / 100 for pack in packs)
    status = "charging" if current > 0.05 else "discharging" if current < -0.05 else "idle"
    health = "healthy"
    if online != configured or soc_difference > 10 or maximum_cell_spread > 50:
        health = "attention"

    lowest_soc = min(pack["soc"] for pack in packs)
    if online != configured:
        recommendation = "Check the offline battery before relying on the bank."
    elif lowest_soc <= 10:
        recommendation = "Charge now; avoid heavy loads until every battery is above 20%."
    elif soc_difference > 10 or maximum_cell_spread > 50:
        recommendation = "Continue gentle charging to rebalance the batteries; avoid heavy loads."
    elif soc < 25:
        recommendation = "Reserve mode: minimize discretionary loads and prioritize charging."
    elif status == "charging":
        recommendation = "Charging normally; defer large loads until the bank reaches the desired reserve."
    else:
        recommendation = "Battery bank is balanced and available for normal loads."

    derived.update({
        "bank_voltage": _measurement(voltage, "V", "voltage"),
        "bank_current": _measurement(current, "A", "current"),
        "bank_power": _measurement(power, "W", "power"),
        "bank_charging_power": _measurement(round(max(power, 0.0), 1), "W", "power"),
        "bank_discharging_power": _measurement(round(max(-power, 0.0), 1), "W", "power"),
        "bank_soc": Reading(soc, "%", "battery", "measurement"),
        "bank_soc_difference": _measurement(soc_difference, "%"),
        "bank_remaining_capacity": _measurement(round(sum(pack["soc"] for pack in packs), 1), "Ah"),
        "bank_remaining_energy": _measurement(
            round(remaining_energy, 3), "kWh", "energy"
        ),
        "bank_maximum_cell_spread": _measurement(maximum_cell_spread, "mV", "voltage"),
        "bank_status": Reading(status),
        "bank_health": Reading(health),
        "bank_operating_recommendation": Reading(recommendation),
        # Backwards-compatible entities from version 0.4 now represent the whole bank.
        "battery_voltage": _measurement(voltage, "V", "voltage"),
        "battery_current": _measurement(current, "A", "current"),
        "battery_power": _measurement(power, "W", "power"),
        "battery_soc": Reading(soc, "%", "battery", "measurement"),
        "battery_status": Reading(status),
    })
    return derived
