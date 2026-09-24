"""Baiamonte Power Guard Home Assistant app.

The app observes battery and load telemetry, learns a conservative load baseline,
and can shed only explicitly allow-listed noncritical switches. It never writes to
the inverter/BMS and never operates the estate main, camera, or refrigerator feeds.
"""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from .engine import Decision, LearningState, MODE_RANK, Policy, Snapshot, decide, protected_overlap
except ImportError:  # The app image copies both modules to the filesystem root.
    from engine import Decision, LearningState, MODE_RANK, Policy, Snapshot, decide, protected_overlap


TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API_BASE = "http://supervisor/core/api"
WEB_ROOT = Path("/web")
STATE_PATH = Path(os.environ.get("POWER_GUARD_STATE", "/data/power_guard_state.json"))
OPTIONS_PATH = Path(os.environ.get("POWER_GUARD_OPTIONS", "/data/options.json"))
UI_OPTIONS_PATH = Path(os.environ.get("POWER_GUARD_UI_OPTIONS", "/data/power_guard_ui_options.json"))
RUNNING = True
LOCK = threading.Lock()
HISTORY: list[dict[str, object]] = []
AVAILABLE_SWITCHES: list[dict[str, str]] = []
STATUS: dict[str, object] = {
    "service": "starting",
    "mode": "telemetry_lost",
    "control_mode": "observe",
    "last_error": None,
    "managed_off": [],
}

EDITABLE_OPTIONS = (
    "control_mode",
    "protected_entities", "shed_first", "shed_tier_1", "shed_tier_2", "shed_tier_3",
    "battery_capacity_kwh", "reserve_soc", "economy_soc", "conserve_soc",
    "protect_soc", "emergency_soc", "restore_soc", "economy_runtime_hours",
    "conserve_runtime_hours", "protect_runtime_hours", "emergency_runtime_hours",
    "forecast_margin_percent", "minimum_planning_load_w", "restore_charge_power_w",
    "restore_solar_surplus_w", "evaluation_interval_seconds", "confirm_seconds",
    "restore_stable_seconds", "history_hours", "learning_alpha",
    "battery_soc_entity", "battery_power_entity", "battery_online_entity",
    "remaining_energy_entity", "nominal_energy_entity", "estate_load_entity",
    "solar_power_entity", "internet_health_entity", "solar_forecast_now_entity",
    "solar_remaining_today_entity", "solar_tomorrow_entity", "weather_entity",
    "solar_forecast_credit_percent", "maximum_solar_credit_kwh",
    "overnight_buffer_hours", "poor_weather_load_penalty_percent",
)
STRING_OPTIONS = {
    "control_mode", "protected_entities", "shed_first", "shed_tier_1", "shed_tier_2",
    "shed_tier_3", "battery_soc_entity", "battery_power_entity", "battery_online_entity",
    "remaining_energy_entity", "nominal_energy_entity", "estate_load_entity",
    "solar_power_entity", "internet_health_entity",
    "solar_forecast_now_entity", "solar_remaining_today_entity", "solar_tomorrow_entity",
    "weather_entity",
}
INTEGER_OPTIONS = {
    "evaluation_interval_seconds", "confirm_seconds", "restore_stable_seconds", "history_hours",
}
DEFAULT_UI_OPTIONS: dict[str, object] = {
    "control_mode": "observe",
    "battery_soc_entity": "sensor.baiamonte_can_bank_soc",
    "battery_power_entity": "sensor.baiamonte_can_bank_power",
    "battery_online_entity": "binary_sensor.baiamonte_can_bank_all_batteries_online",
    "remaining_energy_entity": "sensor.baiamonte_can_bank_remaining_energy",
    "nominal_energy_entity": "sensor.baiamonte_can_bank_nominal_energy",
    "estate_load_entity": "sensor.baiamonte_estate_load",
    "solar_power_entity": "sensor.total_dc_input_power",
    "internet_health_entity": "binary_sensor.starlink_connectivity",
    "solar_forecast_now_entity": "sensor.solcast_pv_forecast_power_now",
    "solar_remaining_today_entity": "sensor.solcast_pv_forecast_forecast_remaining_today",
    "solar_tomorrow_entity": "sensor.solcast_pv_forecast_forecast_tomorrow",
    "weather_entity": "weather.forecast_home",
    "protected_entities": "switch.wifi_din_rail_40a_main,switch.wifi_din_rail_10a_cameras_switch,switch.smart_power_outlet_3",
    "shed_first": "switch.wifi_din_rail_10a_nokia_lte_switch",
    "shed_tier_1": "switch.smart_power_outlet_6,switch.smart_power_outlet_4,switch.smart_power_outlet_5",
    "shed_tier_2": "switch.smart_power_outlet_2,switch.smart_power_outlet_1",
    "shed_tier_3": "switch.wifi_din_rail_10a_lights_switch",
    "battery_capacity_kwh": 10.24,
    "reserve_soc": 30, "economy_soc": 70, "conserve_soc": 60,
    "protect_soc": 45, "emergency_soc": 35, "restore_soc": 80,
    "economy_runtime_hours": 12, "conserve_runtime_hours": 10,
    "protect_runtime_hours": 6, "emergency_runtime_hours": 2.5,
    "forecast_margin_percent": 15, "minimum_planning_load_w": 500,
    "restore_charge_power_w": 100, "restore_solar_surplus_w": 200,
    "solar_forecast_credit_percent": 35, "maximum_solar_credit_kwh": 2,
    "overnight_buffer_hours": 1, "poor_weather_load_penalty_percent": 10,
    "evaluation_interval_seconds": 60, "confirm_seconds": 120,
    "restore_stable_seconds": 1800, "history_hours": 24, "learning_alpha": 0.06,
}


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def csv_entities(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def switch_options(states: dict[str, dict], options: dict) -> list[dict[str, str]]:
    configured = set()
    for key in ("protected_entities", "shed_first", "shed_tier_1", "shed_tier_2", "shed_tier_3"):
        configured.update(csv_entities(options.get(key)))
    entity_ids = configured.union(entity for entity in states if entity.startswith("switch."))
    result = []
    for entity_id in entity_ids:
        state = states.get(entity_id, {})
        attributes = state.get("attributes", {}) if isinstance(state, dict) else {}
        friendly_name = str(attributes.get("friendly_name") or entity_id.removeprefix("switch.").replace("_", " ").title())
        value = str(state.get("state", "not found"))
        result.append({"entity_id": entity_id, "name": friendly_name, "state": value})
    return sorted(result, key=lambda item: (item["name"].casefold(), item["entity_id"]))


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def effective_options() -> dict:
    base = dict(DEFAULT_UI_OPTIONS)
    base.update(load_json(OPTIONS_PATH, {}))
    overrides = load_json(UI_OPTIONS_PATH, {})
    if isinstance(overrides, dict):
        base.update({key: value for key, value in overrides.items() if key in EDITABLE_OPTIONS})
    return base


def validate_ui_options(payload: dict, current: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be a JSON object")
    candidate = dict(current)
    clean: dict[str, object] = {}
    for key in EDITABLE_OPTIONS:
        if key not in payload:
            continue
        raw = payload[key]
        if key in STRING_OPTIONS:
            value = str(raw).strip()
        elif key in INTEGER_OPTIONS:
            value = int(float(raw))
        else:
            value = float(raw)
        candidate[key] = value
        clean[key] = value

    if candidate.get("control_mode") not in {"observe", "automatic"}:
        raise ValueError("Control mode must be observe or automatic")
    if (
        candidate.get("control_mode") == "automatic"
        and current.get("control_mode") != "automatic"
        and payload.get("automatic_confirmation") != "ENABLE AUTOMATIC"
    ):
        raise ValueError("Type ENABLE AUTOMATIC to enable physical load switching")

    for key in ("reserve_soc", "economy_soc", "conserve_soc", "protect_soc", "emergency_soc", "restore_soc"):
        if not 0 <= float(candidate.get(key, 0)) <= 100:
            raise ValueError(f"{key} must be between 0 and 100")
    if not 15 <= int(candidate.get("evaluation_interval_seconds", 60)) <= 3600:
        raise ValueError("Evaluation interval must be between 15 and 3600 seconds")
    if not 1 <= int(candidate.get("history_hours", 24)) <= 168:
        raise ValueError("History must be between 1 and 168 hours")
    if not 0.01 <= float(candidate.get("learning_alpha", 0.06)) <= 1:
        raise ValueError("Learning alpha must be between 0.01 and 1")
    if not 0 <= float(candidate.get("forecast_margin_percent", 15)) <= 100:
        raise ValueError("Forecast margin must be between 0 and 100 percent")
    if not 0 <= float(candidate.get("solar_forecast_credit_percent", 35)) <= 100:
        raise ValueError("Solar forecast confidence must be between 0 and 100 percent")
    if not 0 <= float(candidate.get("poor_weather_load_penalty_percent", 10)) <= 100:
        raise ValueError("Poor-weather penalty must be between 0 and 100 percent")
    if float(candidate.get("maximum_solar_credit_kwh", 2)) < 0:
        raise ValueError("Maximum solar credit cannot be negative")
    if not 0 <= float(candidate.get("overnight_buffer_hours", 1)) <= 12:
        raise ValueError("Overnight buffer must be between 0 and 12 hours")
    if float(candidate.get("battery_capacity_kwh", 10.24)) <= 0:
        raise ValueError("Battery capacity must be greater than zero")
    configure(candidate)
    return clean


def save_ui_options(overrides: dict) -> None:
    existing = load_json(UI_OPTIONS_PATH, {})
    existing.update(overrides)
    temporary = UI_OPTIONS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(UI_OPTIONS_PATH)


def save_state(learning: LearningState, managed_off: set[str], current_mode: str) -> None:
    payload = {
        "learning": learning.to_dict(),
        "managed_off": sorted(managed_off),
        "current_mode": current_mode,
        "history": HISTORY,
        "saved_at": time.time(),
    }
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(STATE_PATH)


class HomeAssistant:
    def __init__(self, token: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def request(self, path: str, data: dict | None = None, method: str | None = None):
        encoded = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            API_BASE + path,
            data=encoded,
            method=method or ("POST" if data is not None else "GET"),
            headers=self.headers,
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read() or b"null")

    def states(self) -> dict[str, dict]:
        return {item["entity_id"]: item for item in self.request("/states")}

    def switch(self, entity_ids: list[str], turn_on: bool) -> None:
        if not entity_ids:
            return
        service = "turn_on" if turn_on else "turn_off"
        self.request(f"/services/switch/{service}", {"entity_id": entity_ids})

    def publish(self, entity_id: str, state: object, attributes: dict) -> None:
        self.request(f"/states/{entity_id}", {"state": state, "attributes": attributes})


def numeric(states: dict[str, dict], entity_id: str) -> float | None:
    try:
        value = float(states.get(entity_id, {}).get("state"))
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def boolean(states: dict[str, dict], entity_id: str) -> bool | None:
    value = states.get(entity_id, {}).get("state")
    if value in ("on", "home", "connected"):
        return True
    if value in ("off", "not_home", "disconnected"):
        return False
    return None


def configure(options: dict) -> tuple[Policy, dict[int, list[str]], set[str]]:
    policy = Policy(
        reserve_soc=float(options.get("reserve_soc", 30)),
        economy_soc=float(options.get("economy_soc", 70)),
        conserve_soc=float(options.get("conserve_soc", 60)),
        protect_soc=float(options.get("protect_soc", 45)),
        emergency_soc=float(options.get("emergency_soc", 35)),
        restore_soc=float(options.get("restore_soc", 80)),
        economy_runtime_hours=float(options.get("economy_runtime_hours", 12)),
        conserve_runtime_hours=float(options.get("conserve_runtime_hours", 10)),
        protect_runtime_hours=float(options.get("protect_runtime_hours", 6)),
        emergency_runtime_hours=float(options.get("emergency_runtime_hours", 2.5)),
        battery_capacity_kwh=float(options.get("battery_capacity_kwh", 10.24)),
        forecast_margin_percent=float(options.get("forecast_margin_percent", 15)),
        minimum_planning_load_w=float(options.get("minimum_planning_load_w", 500)),
        restore_charge_power_w=float(options.get("restore_charge_power_w", 100)),
        restore_solar_surplus_w=float(options.get("restore_solar_surplus_w", 200)),
        solar_forecast_credit_percent=float(options.get("solar_forecast_credit_percent", 35)),
        maximum_solar_credit_kwh=float(options.get("maximum_solar_credit_kwh", 2)),
        overnight_buffer_hours=float(options.get("overnight_buffer_hours", 1)),
        poor_weather_load_penalty_percent=float(options.get("poor_weather_load_penalty_percent", 10)),
    )
    tiers = {
        0: csv_entities(options.get("shed_first")),
        1: csv_entities(options.get("shed_tier_1")),
        2: csv_entities(options.get("shed_tier_2")),
        3: csv_entities(options.get("shed_tier_3")),
    }
    protected = set(csv_entities(options.get("protected_entities")))
    protected.update({
        "switch.wifi_din_rail_40a_main",
        "switch.wifi_din_rail_10a_cameras_switch",
        "switch.smart_power_outlet_3",
    })
    invalid = [entity for entity in protected.union(*map(set, tiers.values())) if not entity.startswith("switch.")]
    if invalid:
        raise ValueError("Managed entities must be switch entities: " + ", ".join(sorted(invalid)))
    overlap = protected_overlap(protected, tiers.values())
    if overlap:
        raise ValueError("Protected entities also appear in shedding tiers: " + ", ".join(sorted(overlap)))
    duplicates = [entity for entity in set(sum(tiers.values(), [])) if sum(entity in tier for tier in tiers.values()) > 1]
    if duplicates:
        raise ValueError("Entities may appear in only one shedding tier: " + ", ".join(sorted(duplicates)))
    if not (policy.reserve_soc <= policy.emergency_soc <= policy.protect_soc <= policy.conserve_soc <= policy.economy_soc < policy.restore_soc):
        raise ValueError("SOC thresholds must increase from reserve through restore")
    if not (policy.emergency_runtime_hours <= policy.protect_runtime_hours <= policy.conserve_runtime_hours <= policy.economy_runtime_hours):
        raise ValueError("Runtime thresholds must increase from emergency through economy")
    return policy, tiers, protected


def make_snapshot(options: dict, states: dict[str, dict]) -> Snapshot:
    weather = states.get(str(options.get("weather_entity", "")), {})
    sun = states.get("sun.sun", {})
    return Snapshot(
        timestamp=time.time(),
        soc=numeric(states, str(options["battery_soc_entity"])),
        battery_power_w=numeric(states, str(options["battery_power_entity"])),
        estate_load_w=numeric(states, str(options["estate_load_entity"])),
        remaining_energy_kwh=numeric(states, str(options["remaining_energy_entity"])),
        solar_power_w=numeric(states, str(options["solar_power_entity"])),
        battery_online=boolean(states, str(options["battery_online_entity"])),
        internet_online=boolean(states, str(options["internet_health_entity"])),
        cameras_powered=boolean(states, "switch.wifi_din_rail_10a_cameras_switch"),
        nominal_energy_kwh=numeric(states, str(options["nominal_energy_entity"])),
        solar_forecast_now_w=numeric(states, str(options.get("solar_forecast_now_entity", ""))),
        solar_remaining_today_kwh=numeric(states, str(options.get("solar_remaining_today_entity", ""))),
        solar_tomorrow_kwh=numeric(states, str(options.get("solar_tomorrow_entity", ""))),
        weather_condition=str(weather.get("state")) if weather.get("state") not in (None, "unknown", "unavailable") else None,
        cloud_coverage_percent=_attribute_number(weather, "cloud_coverage"),
        sun_above_horizon=True if sun.get("state") == "above_horizon" else False if sun.get("state") == "below_horizon" else None,
        hours_to_sunrise=_hours_until(sun.get("attributes", {}).get("next_rising")),
        hours_to_sunset=_hours_until(sun.get("attributes", {}).get("next_setting")),
    )


def _attribute_number(state: dict, name: str) -> float | None:
    try:
        value = float(state.get("attributes", {}).get(name))
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def _hours_until(value: object) -> float | None:
    if not value:
        return None
    try:
        target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds() / 3600)
    except (TypeError, ValueError):
        return None


def desired_off(decision: Decision, tiers: dict[int, list[str]]) -> set[str]:
    return {entity for tier in decision.shed_tiers for entity in tiers[tier]}


def publish_status(ha: HomeAssistant, decision: Decision, learning: LearningState, managed_off: set[str], options: dict) -> None:
    common = {
        "friendly_name": "Baiamonte Power Guard",
        "icon": "mdi:shield-check",
        "reason": decision.reason,
        "control_mode": options.get("control_mode", "observe"),
        "telemetry_ok": decision.telemetry_ok,
        "expected_load_w": round(decision.expected_load_w, 1),
        "managed_off": sorted(managed_off),
        "cameras_protected": True,
        "lte_shed_first": "switch.wifi_din_rail_10a_nokia_lte_switch" in csv_entities(options.get("shed_first")),
        "shed_categories": options.get("shed_first", "") + " | " + options.get("shed_tier_1", "") + " | " + options.get("shed_tier_2", "") + " | " + options.get("shed_tier_3", ""),
        "renewable_outlook": decision.renewable_outlook,
        "solar_credit_kwh": round(decision.solar_credit_kwh, 2),
        "season": decision.season,
    }
    ha.publish("sensor.baiamonte_power_guard_status", decision.mode, common)
    ha.publish(
        "sensor.baiamonte_power_guard_estimated_runtime",
        "unknown" if decision.runtime_hours is None else round(decision.runtime_hours, 2),
        {"friendly_name": "Baiamonte Estimated Protected Runtime", "unit_of_measurement": "h", "icon": "mdi:timer-sand"},
    )
    ha.publish(
        "sensor.baiamonte_power_guard_learned_load",
        round(learning.learned_load_w, 1),
        {"friendly_name": "Baiamonte Learned Estate Load", "unit_of_measurement": "W", "device_class": "power", "state_class": "measurement", "samples": learning.samples},
    )


def run() -> None:
    global HISTORY, AVAILABLE_SWITCHES
    options = effective_options()
    policy, tiers, protected = configure(options)
    stored = load_json(STATE_PATH, {})
    learning = LearningState.from_dict(stored.get("learning", {}))
    HISTORY = [row for row in stored.get("history", []) if isinstance(row, dict)][-10_080:]
    managed_off = set(stored.get("managed_off", [])) - protected
    current_mode = str(stored.get("current_mode", "normal"))
    candidate_mode = current_mode
    candidate_since = time.time()
    recovery_since: float | None = None
    last_save = 0.0
    ha = HomeAssistant(TOKEN)
    interval = 60
    control_mode = str(options.get("control_mode", "observe"))
    with LOCK:
        STATUS.update(
            service="running",
            control_mode=control_mode,
            protected_entities=sorted(protected),
            shed_categories={"first": tiers[0], "stage_2": tiers[1], "stage_3": tiers[2], "emergency": tiers[3]},
        )

    while RUNNING:
        try:
            options = effective_options()
            policy, tiers, protected = configure(options)
            interval = max(15, int(options.get("evaluation_interval_seconds", 60)))
            confirm_seconds = max(0, int(options.get("confirm_seconds", 120)))
            restore_seconds = max(300, int(options.get("restore_stable_seconds", 1800)))
            control_mode = str(options.get("control_mode", "observe"))
            managed_off.difference_update(protected)
            states = ha.states()
            available_switches = switch_options(states, options)
            snapshot = make_snapshot(options, states)
            learning.update(snapshot, alpha=float(options.get("learning_alpha", 0.06)))
            decision = decide(snapshot, learning, policy)
            now = time.time()
            if decision.mode != candidate_mode:
                candidate_mode, candidate_since = decision.mode, now

            stable_for = now - candidate_since
            escalation = decision.mode in MODE_RANK and MODE_RANK.get(decision.mode, 0) > MODE_RANK.get(current_mode, 0)
            accepted = decision.mode == current_mode or stable_for >= (confirm_seconds if escalation else restore_seconds)
            if accepted and decision.mode != "telemetry_lost":
                current_mode = decision.mode

            target_off = desired_off(decision, tiers) if accepted else desired_off(
                Decision(current_mode, "pending", None, 0, {"normal": (), "economy": (0,), "conserve": (0, 1), "protect": (0, 1, 2), "emergency": (0, 1, 2, 3)}.get(current_mode, ()), False, True), tiers
            )
            available = {entity for entity, state in states.items() if state.get("state") not in ("unavailable", "unknown")}
            to_shed = sorted(entity for entity in target_off if entity in available and states[entity].get("state") == "on" and entity not in protected)
            to_restore = sorted(entity for entity in managed_off - target_off if entity in available)

            if decision.can_restore:
                recovery_since = recovery_since or now
            else:
                recovery_since = None
            recovery_ready = recovery_since is not None and now - recovery_since >= restore_seconds

            if control_mode == "automatic" and decision.telemetry_ok:
                journal_changed = False
                if to_shed:
                    ha.switch(to_shed, False)
                    managed_off.update(to_shed)
                    journal_changed = True
                    log("Shed noncritical loads: " + ", ".join(to_shed))
                if decision.can_restore and accepted and recovery_ready and to_restore:
                    ha.switch(to_restore, True)
                    managed_off.difference_update(to_restore)
                    journal_changed = True
                    log("Restored managed loads: " + ", ".join(to_restore))
                if journal_changed:
                    # Persist immediately so an outage after switching cannot lose
                    # the record of which loads this app is allowed to restore.
                    save_state(learning, managed_off, current_mode)
                    last_save = now

            publish_status(ha, decision, learning, managed_off, options)
            with LOCK:
                AVAILABLE_SWITCHES = available_switches
                HISTORY.append({
                    "timestamp": now,
                    "soc": snapshot.soc,
                    "battery_power_w": snapshot.battery_power_w,
                    "estate_load_w": snapshot.estate_load_w,
                    "solar_power_w": snapshot.solar_power_w,
                    "solar_forecast_now_w": snapshot.solar_forecast_now_w,
                    "runtime_hours": decision.runtime_hours,
                    "mode": decision.mode,
                })
                cutoff = now - int(options.get("history_hours", 24)) * 3600
                HISTORY = [row for row in HISTORY if float(row.get("timestamp", 0)) >= cutoff]
                STATUS.update(
                    service="running",
                    mode=decision.mode,
                    accepted_mode=current_mode,
                    reason=decision.reason,
                    runtime_hours=decision.runtime_hours,
                    expected_load_w=decision.expected_load_w,
                    battery_only_runtime_hours=decision.battery_only_runtime_hours,
                    solar_credit_kwh=decision.solar_credit_kwh,
                    renewable_outlook=decision.renewable_outlook,
                    season=decision.season,
                    soc=snapshot.soc,
                    battery_power_w=snapshot.battery_power_w,
                    estate_load_w=snapshot.estate_load_w,
                    solar_power_w=snapshot.solar_power_w,
                    solar_forecast_now_w=snapshot.solar_forecast_now_w,
                    solar_remaining_today_kwh=snapshot.solar_remaining_today_kwh,
                    solar_tomorrow_kwh=snapshot.solar_tomorrow_kwh,
                    weather_condition=snapshot.weather_condition,
                    hours_to_sunrise=snapshot.hours_to_sunrise,
                    hours_to_sunset=snapshot.hours_to_sunset,
                    internet_online=snapshot.internet_online,
                    cameras_powered=snapshot.cameras_powered,
                    telemetry_ok=decision.telemetry_ok,
                    recommended_off=sorted(target_off),
                    managed_off=sorted(managed_off),
                    learned_load_w=learning.learned_load_w,
                    learned_discharge_w=learning.learned_discharge_w,
                    samples=learning.samples,
                    recovery_stable_seconds=0 if recovery_since is None else int(now - recovery_since),
                    protected_entities=sorted(protected),
                    shed_categories={"first": tiers[0], "stage_2": tiers[1], "stage_3": tiers[2], "emergency": tiers[3]},
                    config_source="web" if UI_OPTIONS_PATH.exists() else "app options",
                    last_evaluation=time.time(),
                    last_error=None,
                )
            if now - last_save >= 300:
                save_state(learning, managed_off, current_mode)
                last_save = now
        except Exception as exc:
            log(f"Evaluation failed safely (no switching): {exc}")
            with LOCK:
                STATUS.update(service="degraded", mode="telemetry_lost", last_error=str(exc))
        time.sleep(interval)

    save_state(learning, managed_off, current_mode)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.rstrip("/").endswith("/api/status"):
            with LOCK:
                value = dict(STATUS)
            self.send_json(value)
            return
        if path.rstrip("/").endswith("/api/history"):
            with LOCK:
                value = list(HISTORY)
            self.send_json({"history": value})
            return
        if path.rstrip("/").endswith("/api/config"):
            options = effective_options()
            self.send_json({
                "options": {key: options.get(key) for key in EDITABLE_OPTIONS},
                "source": "web" if UI_OPTIONS_PATH.exists() else "app options",
                "hard_protected": [
                    "switch.wifi_din_rail_40a_main",
                    "switch.wifi_din_rail_10a_cameras_switch",
                    "switch.smart_power_outlet_3",
                ],
            })
            return
        if path.rstrip("/").endswith("/api/switches"):
            with LOCK:
                switches = list(AVAILABLE_SWITCHES)
            if not switches:
                switches = switch_options({}, effective_options())
            self.send_json({"switches": switches})
            return
        name = path.rstrip("/").rsplit("/", 1)[-1]
        target = WEB_ROOT / (name if "." in name else "index.html")
        if not target.is_file() or WEB_ROOT not in target.resolve().parents:
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
        path = urlparse(self.path).path
        if not path.rstrip("/").endswith("/api/config"):
            self.send_json({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65_536:
                raise ValueError("Configuration request is empty or too large")
            payload = json.loads(self.rfile.read(length))
            current = effective_options()
            clean = validate_ui_options(payload, current)
            save_ui_options(clean)
            with LOCK:
                STATUS["config_source"] = "web"
                STATUS["config_saved_at"] = time.time()
            self.send_json({
                "ok": True,
                "message": "Configuration saved; it will apply on the next evaluation",
                "options": {key: effective_options().get(key) for key in EDITABLE_OPTIONS},
            })
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = ThreadingHTTPServer(("0.0.0.0", 8096), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("Power Guard dashboard ready on port 8096")
    try:
        run()
    finally:
        server.shutdown()
