"""Learn a bounded daily correction from archived day-ahead forecast errors."""

import json
from datetime import date, datetime, timedelta
from math import isfinite
from pathlib import Path
from statistics import mean, median

from solar_forecast import ROME

FIT_DAYS = 7
TEST_DAYS = 7
TRAINING_WINDOW = 28
RETENTION = 60


def load_learning(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_learning(path: Path, records: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(path)


def archive_forecast(records: dict, forecast: dict, issued: datetime) -> None:
    """Keep the latest genuine day-ahead forecast, and its unadjusted reference."""
    target = (issued.astimezone(ROME).date() + timedelta(days=1)).isoformat()
    if forecast.get("target_day") != target or not forecast.get("points"):
        return
    local = forecast.get("total_kwh")
    baseline = forecast.get("uncalibrated_kwh", local)
    if not isinstance(local, (int, float)) or not isfinite(local) or local < 0:
        return
    previous = records.get(target, {})
    solcast = (forecast.get("solcast") or {}).get("total_kwh")
    updated = {
        "baseline_kwh": baseline,
        "forecast_kwh": local,
        "solcast_kwh": solcast if isinstance(solcast, (int, float)) and isfinite(solcast) and solcast >= 0 else None,
        "learning_eligible": bool(
            forecast.get("solcast_used") and forecast.get("weather_hours")
            and isinstance(baseline, (int, float)) and isfinite(baseline) and baseline > 0
        ),
    }
    if all(previous.get(key) == value for key, value in updated.items()):
        return
    records[target] = {**updated, "issued_at": issued.isoformat()}


def _gain(pairs: list[tuple[float, float]]) -> float:
    return min(1.2, max(0.8, median(actual / predicted for predicted, actual in pairs[-TRAINING_WINDOW:])))


def evaluate_forecasts(records: dict, profiles: dict[date, dict[int, float]], today: date) -> dict:
    """Test a gain on later days before using it, with no look-ahead."""
    for day, profile in profiles.items():
        item = records.get(day.isoformat())
        if item and day < today:
            item["actual_kwh"] = round(sum(profile.values()) / 1000, 3)
    cutoff = (today - timedelta(days=RETENTION)).isoformat()
    for day in list(records):
        if day < cutoff:
            del records[day]
    completed = [item for day, item in sorted(records.items())
                 if day < today.isoformat() and "actual_kwh" in item and item.get("learning_eligible", True)]
    pairs = [(item["baseline_kwh"], item["actual_kwh"]) for item in completed]
    status = {
        "phase": "collecting",
        "scored_days": len(pairs),
        "validation_days": 0,
        "gain": 1.0,
        "reference": "Completed daylight reconstruction from load and battery flow",
        "required_days": FIT_DAYS + TEST_DAYS,
    }
    if len(pairs) < FIT_DAYS + TEST_DAYS:
        return status

    baseline_errors = []
    replay_errors = []
    for index in range(max(FIT_DAYS, len(pairs) - TRAINING_WINDOW), len(pairs)):
        forecast, actual = pairs[index]
        baseline_errors.append(abs(forecast - actual))
        replay_errors.append(abs(forecast * _gain(pairs[:index]) - actual))
    baseline_mae, corrected_mae = mean(baseline_errors), mean(replay_errors)
    improved = corrected_mae < baseline_mae * 0.98
    return {
        **status,
        "phase": "calibrating" if improved else "monitoring",
        "validation_days": len(replay_errors),
        "baseline_mae_kwh": round(baseline_mae, 3),
        "validation_mae_kwh": round(corrected_mae, 3),
        "issued_mae_kwh": round(mean(abs(row["forecast_kwh"] - row["actual_kwh"]) for row in completed[-TRAINING_WINDOW:]), 3),
        "gain": round(_gain(pairs), 4) if improved else 1.0,
    }


def accuracy_archive(records: dict) -> dict:
    """Expose the saved totals without exposing internal calibration records."""
    return {
        day: {
            "issued_at": row.get("issued_at"),
            "local_kwh": row.get("forecast_kwh"),
            "solcast_kwh": row.get("solcast_kwh"),
        }
        for day, row in sorted(records.items())[-32:]
    }



def apply_learning(forecast: dict, status: dict) -> dict:
    if "total_kwh" not in forecast:
        return forecast
    original = forecast["total_kwh"]
    gain = status["gain"] if forecast.get("solcast_used") and forecast.get("weather_hours") else 1
    return {
        **forecast,
        "uncalibrated_kwh": original,
        "total_kwh": round(original * gain, 2),
        "points": [{**point, "power_w": round(point["power_w"] * gain, 1)} for point in forecast["points"]],
        "applied_gain": gain,
    }
