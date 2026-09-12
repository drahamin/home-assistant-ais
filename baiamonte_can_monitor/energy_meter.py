"""Small persistent energy integrator for the battery bank."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from can_decoder import Reading


class EnergyMeter:
    """Integrate signed battery power without counting downtime or noisy idle watts."""

    def __init__(
        self,
        path: str | Path,
        *,
        noise_floor_w: float = 5.0,
        max_gap_s: float = 30.0,
        fallback_discharge_w: float = 500.0,
    ) -> None:
        self.path = Path(path)
        self.noise_floor_w = noise_floor_w
        self.max_gap_s = max_gap_s
        self.charged_kwh = 0.0
        self.discharged_kwh = 0.0
        self.average_charge_w = 0.0
        self.average_discharge_w = 0.0
        self.fallback_discharge_w = fallback_discharge_w
        self._last_power_w: float | None = None
        self._last_sample_at: float | None = None
        self._last_saved_at = 0.0
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.charged_kwh = max(0.0, float(saved.get("charged_kwh", 0.0)))
            self.discharged_kwh = max(0.0, float(saved.get("discharged_kwh", 0.0)))
            self.average_charge_w = max(0.0, float(saved.get("average_charge_w", 0.0)))
            self.average_discharge_w = max(0.0, float(saved.get("average_discharge_w", 0.0)))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    @staticmethod
    def _clean_power(power_w: float, floor_w: float) -> float:
        return 0.0 if abs(power_w) < floor_w else power_w

    def update(self, power_w: float, *, now: float | None = None) -> dict[str, Reading]:
        sample_at = time.monotonic() if now is None else now
        power_w = self._clean_power(float(power_w), self.noise_floor_w)
        # A slow exponential average rejects brief inverter/load spikes while
        # continuously adapting to how this installation is actually used.
        if power_w > 0:
            self.average_charge_w = self._learn(self.average_charge_w, power_w)
        elif power_w < 0:
            self.average_discharge_w = self._learn(self.average_discharge_w, -power_w)
        if self._last_sample_at is not None and self._last_power_w is not None:
            elapsed = sample_at - self._last_sample_at
            if 0 < elapsed <= self.max_gap_s:
                average_w = (self._last_power_w + power_w) / 2
                energy_kwh = average_w * elapsed / 3_600_000
                if energy_kwh >= 0:
                    self.charged_kwh += energy_kwh
                else:
                    self.discharged_kwh += -energy_kwh
        self._last_power_w = power_w
        self._last_sample_at = sample_at
        if sample_at - self._last_saved_at >= 60:
            self.save()
            self._last_saved_at = sample_at
        return self.readings()

    @staticmethod
    def _learn(previous: float, sample: float, alpha: float = 0.08) -> float:
        return sample if previous <= 0 else previous + alpha * (sample - previous)

    def forecast_readings(self, remaining_kwh: float, nominal_kwh: float, power_w: float) -> dict[str, Reading]:
        """Produce stable runtime forecasts using live power and learned usage."""
        current_charge_w = max(float(power_w), 0.0)
        current_discharge_w = max(-float(power_w), 0.0)
        if current_discharge_w > self.noise_floor_w:
            discharge_basis_w = self._learn(self.average_discharge_w, current_discharge_w)
            basis = "live discharge smoothed with learned load"
        elif self.average_discharge_w > self.noise_floor_w:
            discharge_basis_w = self.average_discharge_w
            basis = "learned normal discharge load"
        else:
            discharge_basis_w = self.fallback_discharge_w
            basis = f"conservative {self.fallback_discharge_w:.0f} W load until discharge history is learned"

        charge_basis_w = current_charge_w if current_charge_w > self.noise_floor_w else self.average_charge_w
        empty_hours = min(240.0, max(0.0, remaining_kwh * 1000.0 / discharge_basis_w))
        full_hours: float | str = "unavailable"
        if charge_basis_w > self.noise_floor_w:
            full_hours = round(min(240.0, max(0.0, (nominal_kwh - remaining_kwh) * 1000.0 / charge_basis_w)), 1)

        return {
            "bank_time_to_empty": Reading(round(empty_hours, 1), "h", "duration", "measurement"),
            "bank_time_to_full": Reading(full_hours, "h", "duration", "measurement"),
            "bank_learned_charge_power": Reading(round(self.average_charge_w, 1), "W", "power", "measurement"),
            "bank_learned_discharge_power": Reading(round(discharge_basis_w, 1), "W", "power", "measurement"),
            "bank_runtime_estimate_basis": Reading(basis),
        }

    def readings(self) -> dict[str, Reading]:
        return {
            "bank_energy_charged": Reading(round(self.charged_kwh, 6), "kWh", "energy", "total_increasing"),
            "bank_energy_discharged": Reading(round(self.discharged_kwh, 6), "kWh", "energy", "total_increasing"),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "charged_kwh": self.charged_kwh,
                    "discharged_kwh": self.discharged_kwh,
                    "average_charge_w": self.average_charge_w,
                    "average_discharge_w": self.average_discharge_w,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
