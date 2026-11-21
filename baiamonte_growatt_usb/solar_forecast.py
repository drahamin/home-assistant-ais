"""Advisory forecast based on recent complete estate power balances."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sunrise, sunset

ROME = ZoneInfo("Europe/Rome")
HOUR = timedelta(hours=1)
LOOKBACK_DAYS = 14
TRAINING_DAYS = 5


def midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ROME).astimezone(timezone.utc)


def _number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _hour(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    except (TypeError, ValueError):
        pass
    return None


def solcast_forecast(sensor: dict | None, day: date) -> dict:
    """Convert the combined configured arrays' P50 and range to hourly watts."""
    sensor = sensor or {}
    attrs = sensor.get("attributes") or {}
    slices = attrs.get("detailedHourly") or attrs.get("detailedForecast") or []
    buckets = defaultdict(list)
    for row in slices:
        if not isinstance(row, dict):
            continue
        stamp = _hour(row.get("period_start") or row.get("datetime") or row.get("time"))
        power = _number(row.get("pv_estimate", row.get("estimate")))
        if stamp is not None and stamp.astimezone(ROME).date() == day and power is not None and power >= 0:
            low = _number(row.get("pv_estimate10", row.get("estimate10")))
            high = _number(row.get("pv_estimate90", row.get("estimate90")))
            buckets[stamp].append((power, low, high))
    points = []
    for stamp, values in sorted(buckets.items()):
        def watts(index):
            valid = [row[index] for row in values if row[index] is not None]
            return round(1000 * sum(valid) / len(valid), 1) if valid else None

        points.append({
            "observed_at": stamp.astimezone(ROME).isoformat(),
            "power_w": watts(0), "low_w": watts(1), "high_w": watts(2),
        })
    return {
        "total_kwh": _number(sensor.get("state")),
        "low": _number(attrs.get("estimate10")),
        "high": _number(attrs.get("estimate90")),
        "points": points,
    }


def weather_hours(rows: list[dict]) -> dict[datetime, float]:
    """Read hourly predicted cloud percentages from Home Assistant."""
    result = {}
    for row in rows:
        stamp = _hour(row.get("datetime"))
        cover = _number(row.get("cloud_coverage"))
        if stamp is not None and cover is not None and 0 <= cover <= 100:
            result[stamp] = cover
    return result



def hours(day: date) -> list[datetime]:
    start = midnight(day)
    count = (midnight(day + timedelta(days=1)) - start) // HOUR
    return [start + index * HOUR for index in range(count)]

def daylight(observer: Observer, day: date) -> list[datetime]:
    rise = sunrise(observer, day, ROME)
    setting = sunset(observer, day, ROME)
    return [stamp for stamp in hours(day) if stamp < setting and stamp + HOUR > rise]


def reconstructed_days(
    statistics: dict, sources: list[str], observer: Observer, today: date,
    *, battery_discharge_positive: bool = True,
) -> dict[date, dict[int, float]]:
    """Return only past days with a complete daylight power balance."""
    values = {}
    for entity_id in sources:
        series = {}
        for row in statistics.get(entity_id, []):
            try:
                mean = float(row["mean"])
                stamp = datetime.fromisoformat(row["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if isfinite(mean) and stamp.tzinfo is not None:
                series[stamp] = mean
        values[entity_id] = series

    profiles = {}
    for offset in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        stamps = daylight(observer, day)
        if not stamps or any(
            stamp not in values[entity] for stamp in stamps for entity in sources
        ):
            continue
        profile = {}
        for stamp in stamps:
            load, battery, *other = [values[entity][stamp] for entity in sources]
            if not battery_discharge_positive:
                battery = -battery
            hour = stamp.astimezone(ROME).hour
            profile[hour] = max(0, load - battery - sum(max(0, power) for power in other))
        profiles[day] = profile
    return profiles


def local_forecast(
    profiles: dict[date, dict[int, float]], observer: Observer, today: date,
    *, target_day: date | None = None, solcast: dict | None = None,
    cloud_hours: dict[datetime, float] | None = None,
) -> dict:
    """Calibrate a weather and panel forecast against five complete local days."""
    target_day = target_day or today
    solcast = solcast or {}
    cloud_hours = cloud_hours or {}
    recent = sorted(day for day in profiles if day < today)[-TRAINING_DAYS:][::-1]
    samples = [profiles[day] for day in recent]
    training_dates = [day.isoformat() for day in recent]
    if len(samples) != TRAINING_DAYS:
        return {"days_used": len(samples), "target_day": target_day.isoformat(), "points": []}

    baseline_wh = median(sum(profile.values()) for profile in samples)
    target_hours = hours(target_day)
    daylight_target = set(daylight(observer, target_day))
    estimates = [
        median(profile.get(stamp.astimezone(ROME).hour, 0) for profile in samples)
        if stamp in daylight_target else 0
        for stamp in target_hours
    ]
    profile_wh = sum(estimates)
    if not profile_wh and baseline_wh:
        return {"days_used": len(samples), "target_day": target_day.isoformat(), "points": []}

    # Blend only usable Solcast panels for the target date. Keep the local
    # baseline dominant because the day-ahead backtest favored it.
    solcast_profile = {_hour(row["observed_at"]): row["power_w"] for row in solcast.get("points", [])}
    model_profile = [solcast_profile.get(stamp, 0) if stamp in daylight_target else 0 for stamp in target_hours]
    model_wh = sum(model_profile)
    total_kwh = solcast.get("total_kwh")
    solcast_wh = model_wh if total_kwh is None else total_kwh * 1000
    solcast_used = bool(model_wh and sum(stamp in solcast_profile for stamp in daylight_target) >= len(daylight_target) / 2)
    daily_wh = baseline_wh
    if solcast_used:
        daily_wh += max(-0.1 * baseline_wh, min(0.1 * baseline_wh, 0.1 * (solcast_wh - baseline_wh)))
        if profile_wh:
            estimates = [0.65 * local + 0.35 * pv * profile_wh / model_wh for local, pv in zip(estimates, model_profile)]
    weather_count = sum(stamp in cloud_hours for stamp in daylight_target)
    estimates = [power * (1 - 0.25 * cloud_hours.get(stamp, 0) / 100) for stamp, power in zip(target_hours, estimates)]
    shaped_wh = sum(estimates)
    scale = daily_wh / shaped_wh if shaped_wh else 0
    return {
        "days_used": len(samples),
        "training_dates": training_dates,
        "target_day": target_day.isoformat(),
        "total_kwh": round(daily_wh / 1000, 2),
        "solcast_used": solcast_used,
        "weather_hours": weather_count,
        "solcast": solcast,
        "points": [
            {"observed_at": stamp.astimezone(ROME).isoformat(), "power_w": round(power * scale, 1)}
            for stamp, power in zip(target_hours, estimates)
        ],
    }


def display_forecast_values(forecast: dict, tomorrow: dict, now: datetime) -> dict:
    """Read current, future and peak values from the curve shown by Energy."""
    moment = now.astimezone(timezone.utc)
    today = moment.astimezone(ROME).date()
    points = []
    if forecast.get("target_day") == today.isoformat():
        for row in forecast.get("points", []):
            stamp = _hour(row.get("observed_at"))
            power = _number(row.get("power_w"))
            if stamp is not None and power is not None and power >= 0:
                points.append((stamp, power))
    peak = max(points, key=lambda row: row[1], default=(None, None))
    remaining_wh = sum(
        power * min(1, max(0, (stamp + HOUR - moment) / HOUR))
        for stamp, power in points
    )
    return {
        "power_now": next((power for stamp, power in points if stamp <= moment < stamp + HOUR), None),
        "remaining": round(remaining_wh / 1000, 3) if points else None,
        "tomorrow": _number(tomorrow.get("total_kwh"))
            if tomorrow.get("target_day") == (today + timedelta(days=1)).isoformat() else None,
        "peak": peak[1],
        "peak_time": peak[0].isoformat() if peak[1] else None,
    }
