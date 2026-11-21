"""Advisory forecast based on recent complete estate power balances."""

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


def local_forecast(
    statistics: dict, sources: list[str], observer: Observer, today: date,
    *, battery_discharge_positive: bool = True,
) -> dict:
    """Scale a recent hourly profile to the median of five complete days."""
    def hours(day):
        start = midnight(day)
        count = (midnight(day + timedelta(days=1)) - start) // HOUR
        return [start + index * HOUR for index in range(count)]

    def daylight(day):
        rise = sunrise(observer, day, ROME)
        setting = sunset(observer, day, ROME)
        return [stamp for stamp in hours(day) if stamp < setting and stamp + HOUR > rise]

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

    samples = []
    training_dates = []
    for offset in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=offset)
        stamps = daylight(day)
        # Ignore an incomplete daylight day instead of treating missing power as zero.
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
        samples.append(profile)
        training_dates.append(day.isoformat())
        if len(samples) == TRAINING_DAYS:
            break
    if len(samples) != TRAINING_DAYS:
        return {"days_used": len(samples), "target_day": today.isoformat(), "points": []}

    daily_wh = median(sum(profile.values()) for profile in samples)
    daylight_today = set(daylight(today))
    estimates = [
        median(profile.get(stamp.astimezone(ROME).hour, 0) for profile in samples)
        if stamp in daylight_today else 0
        for stamp in hours(today)
    ]
    profile_wh = sum(estimates)
    if not profile_wh and daily_wh:
        return {"days_used": len(samples), "target_day": today.isoformat(), "points": []}
    scale = daily_wh / profile_wh if profile_wh else 0
    return {
        "days_used": len(samples),
        "training_dates": training_dates,
        "target_day": today.isoformat(),
        "total_kwh": round(daily_wh / 1000, 2),
        "points": [
            {"observed_at": stamp.astimezone(ROME).isoformat(), "power_w": round(power * scale, 1)}
            for stamp, power in zip(hours(today), estimates)
        ],
    }
