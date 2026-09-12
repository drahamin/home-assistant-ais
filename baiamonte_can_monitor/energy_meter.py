"""Small persistent energy integrator for the battery bank."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from can_decoder import Reading


class EnergyMeter:
    """Integrate signed battery power without counting downtime or noisy idle watts."""

    def __init__(self, path: str | Path, *, noise_floor_w: float = 5.0, max_gap_s: float = 30.0) -> None:
        self.path = Path(path)
        self.noise_floor_w = noise_floor_w
        self.max_gap_s = max_gap_s
        self.charged_kwh = 0.0
        self.discharged_kwh = 0.0
        self._last_power_w: float | None = None
        self._last_sample_at: float | None = None
        self._last_saved_at = 0.0
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.charged_kwh = max(0.0, float(saved.get("charged_kwh", 0.0)))
            self.discharged_kwh = max(0.0, float(saved.get("discharged_kwh", 0.0)))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    @staticmethod
    def _clean_power(power_w: float, floor_w: float) -> float:
        return 0.0 if abs(power_w) < floor_w else power_w

    def update(self, power_w: float, *, now: float | None = None) -> dict[str, Reading]:
        sample_at = time.monotonic() if now is None else now
        power_w = self._clean_power(float(power_w), self.noise_floor_w)
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

    def readings(self) -> dict[str, Reading]:
        return {
            "bank_energy_charged": Reading(round(self.charged_kwh, 6), "kWh", "energy", "total_increasing"),
            "bank_energy_discharged": Reading(round(self.discharged_kwh, 6), "kWh", "energy", "total_increasing"),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"charged_kwh": self.charged_kwh, "discharged_kwh": self.discharged_kwh}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
