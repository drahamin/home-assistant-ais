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


@dataclass
class LearningState:
    learned_load_w: float = 0.0
    learned_discharge_w: float = 0.0
    samples: int = 0
    hourly_load_w: list[float] = field(default_factory=lambda: [0.0] * 24)
    hourly_samples: list[int] = field(default_factory=lambda: [0] * 24)

    def update(self, snapshot: Snapshot, alpha: float = 0.06) -> None:
        load = snapshot.estate_load_w
        if load is not None and 20 <= load <= 30_000:
            self.learned_load_w = _ewma(self.learned_load_w, load, alpha)
            hour = datetime.fromtimestamp(snapshot.timestamp).hour
            self.hourly_load_w[hour] = _ewma(self.hourly_load_w[hour], load, alpha)
            self.hourly_samples[hour] += 1
            self.samples += 1

        discharge = snapshot.battery_power_w
        if discharge is not None and 20 <= discharge <= 30_000:
            self.learned_discharge_w = _ewma(self.learned_discharge_w, discharge, alpha)

    def expected_load_w(self, snapshot: Snapshot) -> float:
        hour = datetime.fromtimestamp(snapshot.timestamp).hour
        candidates = [value for value in (
            snapshot.estate_load_w,
            self.hourly_load_w[hour],
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
        for key in ("hourly_load_w", "hourly_samples"):
            candidate = value.get(key)
            if isinstance(candidate, list) and len(candidate) == 24:
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


def estimate_runtime_hours(
    snapshot: Snapshot, learning: LearningState, policy: Policy
) -> tuple[float | None, float]:
    load_w = learning.expected_load_w(snapshot)
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


def decide(snapshot: Snapshot, learning: LearningState, policy: Policy) -> Decision:
    runtime, load_w = estimate_runtime_hours(snapshot, learning, policy)
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

    charging = snapshot.battery_power_w is not None and snapshot.battery_power_w < -100
    solar_surplus = (
        snapshot.solar_power_w is not None
        and snapshot.estate_load_w is not None
        and snapshot.solar_power_w > snapshot.estate_load_w + 200
    )
    can_restore = mode == "normal" and soc >= policy.restore_soc and (charging or solar_surplus)
    return Decision(mode, reason, runtime, load_w, tiers, can_restore, True)


def protected_overlap(protected: Iterable[str], tiers: Iterable[Iterable[str]]) -> set[str]:
    protected_set = {item.strip() for item in protected if item.strip()}
    return protected_set.intersection(
        item.strip() for tier in tiers for item in tier if item.strip()
    )


def _ewma(previous: float, current: float, alpha: float) -> float:
    return current if previous <= 0 else previous * (1 - alpha) + current * alpha
