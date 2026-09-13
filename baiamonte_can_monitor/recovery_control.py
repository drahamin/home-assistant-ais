"""Guarded battery-recovery decisions and optional generator-input control."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from can_decoder import Reading


@dataclass(frozen=True)
class RecoveryAssessment:
    state: str
    summary: str
    action: str
    charge_safe: bool
    full_rate_safe: bool
    recommended_charge_limit_a: float
    weakest_battery: int | None
    weakest_cell: int | None

    def readings(self) -> dict[str, Reading]:
        return {
            "recovery_state": Reading(self.state),
            "recovery_summary": Reading(self.summary),
            "recovery_action": Reading(self.action),
            "recovery_charge_safe": Reading("on" if self.charge_safe else "off"),
            "recovery_full_rate_safe": Reading("on" if self.full_rate_safe else "off"),
            "recovery_recommended_charge_limit": Reading(
                self.recommended_charge_limit_a, "A", "current", "measurement"
            ),
            "recovery_weakest_battery": Reading(self.weakest_battery or "unavailable"),
            "recovery_weakest_cell": Reading(self.weakest_cell or "unavailable"),
        }


def _number(readings: dict[str, Reading], key: str) -> float | None:
    value = readings.get(key)
    if value is None:
        return None
    try:
        return float(value.value)
    except (TypeError, ValueError):
        return None


def assess_recovery(
    readings: dict[str, Reading], addresses: list[int], online_addresses: set[int]
) -> RecoveryAssessment:
    """Choose a conservative operating state from independently reported packs."""
    if not addresses or set(addresses) != online_addresses:
        return RecoveryAssessment(
            "monitoring_unavailable",
            "Not every configured battery is reporting.",
            "Do not start automatic charging until every BMS is online.",
            False,
            False,
            0.0,
            None,
            None,
        )

    packs: list[dict[str, float | int | bool]] = []
    for address in addresses:
        prefix = f"battery_{address}_"
        minimum = _number(readings, prefix + "minimum_cell_voltage")
        maximum = _number(readings, prefix + "maximum_cell_voltage")
        spread = _number(readings, prefix + "cell_voltage_difference")
        temperature = _number(readings, prefix + "pack_temperature")
        soc = _number(readings, prefix + "battery_soc")
        charge_limit = _number(readings, prefix + "charge_current_limit")
        weakest_cell = _number(readings, prefix + "minimum_cell_number")
        allowed_reading = readings.get(prefix + "charge_allowed")
        required = (minimum, maximum, spread, temperature, soc, charge_limit)
        if any(value is None for value in required) or allowed_reading is None:
            return RecoveryAssessment(
                "learning_limits",
                "Waiting for cell data and BMS operating limits from every battery.",
                "Continue monitored charging; automatic control remains locked.",
                False,
                False,
                0.0,
                None,
                None,
            )
        packs.append({
            "address": address,
            "minimum": minimum,
            "maximum": maximum,
            "spread": spread,
            "temperature": temperature,
            "soc": soc,
            "charge_limit": charge_limit,
            "charge_allowed": allowed_reading.value == "on",
            "weakest_cell": int(weakest_cell) if weakest_cell is not None else 0,
        })

    weakest = min(packs, key=lambda pack: float(pack["minimum"]))
    maximum_cell = max(float(pack["maximum"]) for pack in packs)
    maximum_spread = max(float(pack["spread"]) for pack in packs)
    minimum_temp = min(float(pack["temperature"]) for pack in packs)
    maximum_temp = max(float(pack["temperature"]) for pack in packs)
    soc_difference = max(float(pack["soc"]) for pack in packs) - min(float(pack["soc"]) for pack in packs)
    bms_limit = sum(float(pack["charge_limit"]) for pack in packs)
    bms_allows_charge = all(bool(pack["charge_allowed"]) for pack in packs)

    charge_safe = bms_allows_charge and 0 <= minimum_temp and maximum_temp <= 50 and maximum_cell < 3.55
    if not charge_safe:
        if not bms_allows_charge:
            reason = "At least one BMS reports that charging is not allowed."
        elif maximum_cell >= 3.55:
            reason = f"A cell has reached {maximum_cell:.3f} V."
        else:
            reason = f"Battery temperature is outside the guarded charging range ({minimum_temp:.0f}–{maximum_temp:.0f} °C)."
        return RecoveryAssessment(
            "charge_blocked",
            reason,
            "Disconnect the controllable charging input and inspect the battery condition.",
            False,
            False,
            0.0,
            int(weakest["address"]),
            int(weakest["weakest_cell"]),
        )

    full_rate_safe = (
        maximum_spread <= 50
        and soc_difference <= 5
        and float(weakest["minimum"]) >= 3.0
        and max(float(pack["soc"]) for pack in packs) < 90
    )
    if not full_rate_safe:
        # Recovery charging is deliberately capped at 0.05 C per installed
        # 100 Ah pack and can never exceed a BMS-advertised current limit.
        recommended = min(bms_limit, 5.0 * len(packs))
        summary = (
            f"Recovery required: Battery {int(weakest['address'])} cell "
            f"{int(weakest['weakest_cell'])} is {float(weakest['minimum']):.3f} V; "
            f"maximum spread is {maximum_spread:.0f} mV."
        )
        action = f"Use supervised low-rate charging, no more than {recommended:.0f} A for the bank."
        state = "recovery"
    else:
        recommended = min(bms_limit, 50.0 * len(packs))
        summary = "Both batteries are balanced and inside the guarded charging envelope."
        action = f"Normal charging is permitted up to {recommended:.0f} A, subject to inverter and wiring limits."
        state = "normal"
    return RecoveryAssessment(
        state,
        summary,
        action,
        True,
        full_rate_safe,
        round(recommended, 1),
        int(weakest["address"]),
        int(weakest["weakest_cell"]),
    )


class HomeAssistantControl:
    """Small authenticated client for one explicitly configured HA switch."""

    def __init__(self, token: str, log: Callable[[str], None], timeout: float = 3.0) -> None:
        self.token = token
        self.log = log
        self.timeout = timeout
        self.base = "http://supervisor/core/api"

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        parsed = json.loads(body or b"{}")
        return parsed[0] if isinstance(parsed, list) and parsed else parsed

    def state(self, entity_id: str) -> dict:
        return self._request(f"/states/{entity_id}")

    def switch(self, entity_id: str, turn_on: bool) -> None:
        service = "turn_on" if turn_on else "turn_off"
        self._request(f"/services/switch/{service}", "POST", {"entity_id": entity_id})


class RecoveryController:
    """Low-frequency, anti-cycling supervisor for an existing generator input switch."""

    def __init__(self, options: dict, token: str, log: Callable[[str], None]) -> None:
        self.enabled = bool(options.get("recovery_control_enabled", False))
        self.switch_entity = str(options.get("generator_input_switch", "switch.generator_main_breaker_switch"))
        self.voltage_entity = str(options.get("generator_voltage_sensor", "sensor.generator_main_breaker_phase_a_voltage"))
        self.minimum_voltage = float(options.get("generator_available_voltage", 180))
        self.start_soc = float(options.get("recovery_start_soc", 20))
        self.stop_soc = float(options.get("recovery_stop_soc", 90))
        self.minimum_dwell = max(300, int(options.get("recovery_minimum_dwell_seconds", 900)))
        self.client = HomeAssistantControl(token, log) if token else None
        self.log = log
        self.last_action_at = 0.0
        self.last_action = "none"
        self.switch_state = "unknown"
        self.generator_voltage: float | None = None
        self.error: str | None = None

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "switch_entity": self.switch_entity,
            "switch_state": self.switch_state,
            "generator_voltage": self.generator_voltage,
            "last_action": self.last_action,
            "error": self.error,
            "can_regulate_current": False,
        }

    def emergency_stop(self) -> None:
        if not self.client:
            raise RuntimeError("Home Assistant control API is unavailable")
        self.client.switch(self.switch_entity, False)
        self.switch_state = "off"
        self.last_action = "emergency_stop"
        self.last_action_at = time.monotonic()
        self.log(f"Recovery controller opened {self.switch_entity} by operator request")

    def update(self, assessment: RecoveryAssessment, bank_soc: float | None) -> None:
        if not self.client:
            self.error = "Home Assistant API token unavailable"
            return
        try:
            switch = self.client.state(self.switch_entity)
            voltage = self.client.state(self.voltage_entity)
            self.switch_state = str(switch.get("state", "unknown"))
            self.generator_voltage = float(voltage.get("state"))
            self.error = None
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError, OSError) as exc:
            self.error = f"Actuator status unavailable: {exc}"
            return

        if not self.enabled:
            return
        now = time.monotonic()
        if now - self.last_action_at < self.minimum_dwell:
            return
        desired: bool | None = None
        reason = ""
        if not assessment.charge_safe:
            desired, reason = False, assessment.summary
        elif bank_soc is not None and bank_soc >= self.stop_soc and assessment.full_rate_safe:
            desired, reason = False, f"bank reached {bank_soc:.0f}% target"
        elif (
            bank_soc is not None
            and bank_soc <= self.start_soc
            and self.generator_voltage >= self.minimum_voltage
        ):
            desired, reason = True, f"bank is {bank_soc:.0f}% and generator input is available"
        if desired is None or self.switch_state == ("on" if desired else "off"):
            return
        try:
            self.client.switch(self.switch_entity, desired)
            self.switch_state = "on" if desired else "off"
            self.last_action = ("closed" if desired else "opened") + f": {reason}"
            self.last_action_at = now
            self.log(f"Recovery controller {self.last_action}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.error = f"Actuator command failed: {exc}"
