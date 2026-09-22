"""Pure decision and learning logic for Baiamonte Power Guard."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable


MODES = ("normal", "economy", "conserve", "protect", "emergency", "telemetry_lost")
MODE_RANK = {name: rank for rank, name in enumerate(MODES[:5])}


@dataclass(frozen=True)
class Policy:
    reserve_soc: float = 30.0
    economy_soc: float = 70.0
    conserve_soc: float = 60.0
    protect_soc: float = 45.0
    emergency_soc: float = 35.0
    restore_soc: float = 80.0
    economy_runtime_hours: float = 12.0
    conserve_runtime_hours: float = 10.0
    protect_runtime_hours: float = 6.0
    emergency_runtime_hours: float = 2.5
    battery_capacity_kwh: float = 10.24
    forecast_margin_percent: float = 15.0
    minimum_planning_load_w: float = 500.0
    restore_charge_power_w: float = 100.0
    restore_solar_surplus_w: float = 200.0
    solar_forecast_credit_percent: float = 35.0
    maximum_solar_credit_kwh: float = 2.0
    overnight_buffer_hours: float = 1.0
    poor_weather_load_penalty_percent: float = 10.0


@dataclass(frozen=True)
class Snapshot:
    timestamp: float
    soc: float | None
    battery_power_w: float | None
    estate_load_w: float | None
    remaining_energy_kwh: float | None = None
    solar_power_w: float | None = None
    battery_online: bool | None = None
    internet_online: bool | None = None
    cameras_powered: bool | None = None
    nominal_energy_kwh: float | None = None
    solar_forecast_now_w: float | None = None
    solar_remaining_today_kwh: float | None = None
    solar_tomorrow_kwh: float | None = None
    weather_condition: str | None = None
    cloud_coverage_percent: float | None = None
    sun_above_horizon: bool | None = None
    hours_to_sunrise: float | None = None
    hours_to_sunset: float | None = None


@dataclass
class LearningState:
    learned_load_w: float = 0.0
    learned_discharge_w: float = 0.0
    samples: int = 0
    hourly_load_w: list[float] = field(default_factory=lambda: [0.0] * 24)
    hourly_samples: list[int] = field(default_factory=lambda: [0] * 24)
    monthly_load_w: list[float] = field(default_factory=lambda: [0.0] * 12)
    monthly_samples: list[int] = field(default_factory=lambda: [0] * 12)

    def update(self, snapshot: Snapshot, alpha: float = 0.06) -> None:
        load = snapshot.estate_load_w
        if load is not None and 20 <= load <= 30_000:
            self.learned_load_w = _ewma(self.learned_load_w, load, alpha)
            hour = datetime.fromtimestamp(snapshot.timestamp).hour
            month = datetime.fromtimestamp(snapshot.timestamp).month - 1
            self.hourly_load_w[hour] = _ewma(self.hourly_load_w[hour], load, alpha)
            self.hourly_samples[hour] += 1
            self.monthly_load_w[month] = _ewma(self.monthly_load_w[month], load, alpha)
            self.monthly_samples[month] += 1
            self.samples += 1

        discharge = snapshot.battery_power_w
        if discharge is not None and 20 <= discharge <= 30_000:
            self.learned_discharge_w = _ewma(self.learned_discharge_w, discharge, alpha)

    def expected_load_w(self, snapshot: Snapshot) -> float:
        hour = datetime.fromtimestamp(snapshot.timestamp).hour
        month = datetime.fromtimestamp(snapshot.timestamp).month - 1
        candidates = [value for value in (
            snapshot.estate_load_w,
            self.hourly_load_w[hour],
            self.monthly_load_w[month],
            self.learned_discharge_w,
            self.learned_load_w,
        ) if value is not None and value > 20]
        # Never let a momentary low reading make the runtime forecast optimistic.
        return max(candidates) if candidates else 500.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "LearningState":
        state = cls()
        for key in ("learned_load_w", "learned_discharge_w", "samples"):
            if key in value:
                setattr(state, key, value[key])
        for key, length in (("hourly_load_w", 24), ("hourly_samples", 24), ("monthly_load_w", 12), ("monthly_samples", 12)):
            candidate = value.get(key)
            if isinstance(candidate, list) and len(candidate) == length:
                setattr(state, key, candidate)
        return state


@dataclass(frozen=True)
class Decision:
    mode: str
    reason: str
    runtime_hours: float | None
    expected_load_w: float
    shed_tiers: tuple[int, ...]
    can_restore: bool
    telemetry_ok: bool
    battery_only_runtime_hours: float | None = None
    solar_credit_kwh: float = 0.0
    renewable_outlook: str = "unavailable"
    season: str = "unknown"


def estimate_runtime_hours(
    snapshot: Snapshot, learning: LearningState, policy: Policy
) -> tuple[float | None, float]:
    raw_load_w = max(learning.expected_load_w(snapshot), policy.minimum_planning_load_w)
    load_w = raw_load_w * (1 + policy.forecast_margin_percent / 100)
    nominal_kwh = (
        snapshot.nominal_energy_kwh
        if snapshot.nominal_energy_kwh is not None and snapshot.nominal_energy_kwh > 0
        else policy.battery_capacity_kwh
    )
    if snapshot.remaining_energy_kwh is not None and snapshot.remaining_energy_kwh >= 0:
        stored_kwh = snapshot.remaining_energy_kwh
    elif snapshot.soc is not None:
        stored_kwh = nominal_kwh * snapshot.soc / 100
    else:
        return None, load_w
    reserve_kwh = nominal_kwh * policy.reserve_soc / 100
    usable_kwh = max(0.0, stored_kwh - reserve_kwh)
    return usable_kwh * 1000 / max(load_w, 1), load_w


def _weather_factor(snapshot: Snapshot) -> float:
    condition = (snapshot.weather_condition or "").lower().replace("_", "-")
    if condition in {"sunny", "clear", "clear-night"}:
        factor = 1.0
    elif condition in {"partlycloudy", "partly-cloudy"}:
        factor = 0.65
    elif condition in {"cloudy", "fog"}:
        factor = 0.35
    elif any(word in condition for word in ("rain", "pour", "lightning", "snow", "hail")):
        factor = 0.15
    else:
        # Missing weather is neutral for load planning and still discounts solar
        # forecast energy by half. It must not masquerade as known bad weather.
        factor = 0.5
    if snapshot.cloud_coverage_percent is not None:
        cloud = min(100.0, max(0.0, snapshot.cloud_coverage_percent))
        factor *= 1 - cloud / 100 * 0.7
    return min(1.0, max(0.05, factor))


def _season(timestamp: float) -> str:
    month = datetime.fromtimestamp(timestamp).month
    return ("winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5)
            else "summer" if month in (6, 7, 8) else "autumn")


def decide(snapshot: Snapshot, learning: LearningState, policy: Policy) -> Decision:
    battery_runtime, load_w = estimate_runtime_hours(snapshot, learning, policy)
    weather_factor = _weather_factor(snapshot)
    if weather_factor < 0.5:
        penalty = policy.poor_weather_load_penalty_percent * (1 - weather_factor) / 100
        load_w *= 1 + penalty
        if battery_runtime is not None:
            battery_runtime /= 1 + penalty

    solar_credit = 0.0
    if snapshot.sun_above_horizon is True and snapshot.solar_remaining_today_kwh is not None:
        solar_credit = min(
            policy.maximum_solar_credit_kwh,
            max(0.0, snapshot.solar_remaining_today_kwh)
            * policy.solar_forecast_credit_percent / 100
            * weather_factor,
        )
    runtime = None if battery_runtime is None else battery_runtime + solar_credit * 1000 / max(load_w, 1)
    tomorrow = snapshot.solar_tomorrow_kwh
    if snapshot.sun_above_horizon is True:
        outlook = "strong" if weather_factor >= 0.7 and solar_credit >= 0.5 else "limited" if solar_credit > 0 else "poor"
    elif tomorrow is not None:
        expected_daily_kwh = load_w * 24 / 1000
        outlook = "strong" if tomorrow >= expected_daily_kwh * 0.75 else "limited" if tomorrow >= expected_daily_kwh * 0.3 else "poor"
    else:
        outlook = "unavailable"
    telemetry_ok = (
        snapshot.soc is not None
        and 0 <= snapshot.soc <= 100
        and snapshot.battery_online is True
    )
    if not telemetry_ok:
        return Decision(
            "telemetry_lost",
            "Battery telemetry is unavailable; automatic switching is frozen",
            runtime,
            load_w,
            (),
            False,
            False,
            battery_runtime,
            solar_credit,
            outlook,
            _season(snapshot.timestamp),
        )

    soc = float(snapshot.soc)
    if soc <= policy.emergency_soc or (runtime is not None and runtime <= policy.emergency_runtime_hours):
        mode, tiers, reason = "emergency", (0, 1, 2, 3), "Critical battery reserve or runtime"
    elif soc <= policy.protect_soc or (runtime is not None and runtime <= policy.protect_runtime_hours):
        mode, tiers, reason = "protect", (0, 1, 2), "Battery reserve requires protected-load operation"
    elif soc <= policy.conserve_soc or (runtime is not None and runtime <= policy.conserve_runtime_hours):
        mode, tiers, reason = "conserve", (0, 1), "Battery forecast requires early load reduction"
    elif soc <= policy.economy_soc or (runtime is not None and runtime <= policy.economy_runtime_hours):
        mode, tiers, reason = "economy", (0,), "Battery forecast recommends shedding the first-priority category"
    else:
        mode, tiers, reason = "normal", (), "Battery reserve and forecast are healthy"

    # At night the bank must survive until solar production can resume. Forecast
    # energy is never counted before sunrise, and the configured buffer makes the
    # policy earlier in seasons with longer nights.
    if snapshot.sun_above_horizon is False and snapshot.hours_to_sunrise is not None and battery_runtime is not None:
        required = snapshot.hours_to_sunrise + policy.overnight_buffer_hours
        if battery_runtime <= snapshot.hours_to_sunrise and MODE_RANK[mode] < MODE_RANK["emergency"]:
            mode, tiers, reason = "emergency", (0, 1, 2, 3), "Battery may not last until sunrise"
        elif battery_runtime <= required and MODE_RANK[mode] < MODE_RANK["protect"]:
            mode, tiers, reason = "protect", (0, 1, 2), "Protecting overnight reserve until solar production resumes"

    charging = (
        snapshot.battery_power_w is not None
        and snapshot.battery_power_w < -policy.restore_charge_power_w
    )
    solar_surplus = (
        snapshot.solar_power_w is not None
        and snapshot.estate_load_w is not None
        and snapshot.solar_power_w > snapshot.estate_load_w + policy.restore_solar_surplus_w
    )
    can_restore = mode == "normal" and soc >= policy.restore_soc and (charging or solar_surplus)
    return Decision(mode, reason, runtime, load_w, tiers, can_restore, True, battery_runtime, solar_credit, outlook, _season(snapshot.timestamp))


def protected_overlap(protected: Iterable[str], tiers: Iterable[Iterable[str]]) -> set[str]:
    protected_set = {item.strip() for item in protected if item.strip()}
    return protected_set.intersection(
        item.strip() for tier in tiers for item in tier if item.strip()
    )


def _ewma(previous: float, current: float, alpha: float) -> float:
    return current if previous <= 0 else previous * (1 - alpha) + current * alpha
