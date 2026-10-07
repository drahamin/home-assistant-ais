"""Small, bounded Home Assistant history client for the battery charts."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime, time as datetime_time, timedelta, timezone, tzinfo
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PREFIX = "sensor.baiamonte_can_"
CHARTS = {
    "charging": {
        "hours": 24,
        "bucket": 300,
        "entities": [
            (PREFIX + "bank_soc", "Bank SOC", "%"),
            (PREFIX + "battery_1_battery_soc", "Battery 1 SOC", "%"),
            (PREFIX + "battery_2_battery_soc", "Battery 2 SOC", "%"),
            (PREFIX + "battery_3_battery_soc", "Battery 3 SOC", "%"),
            (PREFIX + "bank_charging_power", "Charging power", "W"),
        ],
    },
    "battery1_cells": {
        "hours": 24,
        "bucket": 600,
        "entities": [(PREFIX + f"battery_1_cell_{number}_voltage", f"Cell {number}", "V") for number in range(1, 17)],
    },
    "battery2_cells": {
        "hours": 24,
        "bucket": 600,
        "entities": [(PREFIX + f"battery_2_cell_{number}_voltage", f"Cell {number}", "V") for number in range(1, 17)],
    },
    "battery3_cells": {
        "hours": 24,
        "bucket": 600,
        "entities": [(PREFIX + f"battery_3_cell_{number}_voltage", f"Cell {number}", "V") for number in range(1, 17)],
    },
    "pack_power": {
        "hours": 24,
        "bucket": 300,
        "entities": [
            (PREFIX + "battery_1_net_input_power", "Battery 1", "W"),
            (PREFIX + "battery_2_net_input_power", "Battery 2", "W"),
            (PREFIX + "battery_3_net_input_power", "Battery 3", "W"),
        ],
    },
    "health": {
        "hours": 24 * 7,
        "bucket": 1800,
        "entities": [
            (PREFIX + "bank_soc_difference", "SOC difference", "%"),
            (PREFIX + "bank_maximum_cell_spread", "Maximum cell spread", "mV"),
            (PREFIX + "bank_voltage_difference", "Pack voltage difference", "mV"),
        ],
    },
    "multi_day": {
        "hours": 24 * 14,
        "bucket": 3600,
        "daily_change": True,
        "entities": [
            (PREFIX + "bank_energy_charged", "Energy charged", "kWh"),
            (PREFIX + "bank_energy_discharged", "Energy discharged", "kWh"),
        ],
    },
}

RANGES = {
    "day": {"hours": 24, "bucket": 0, "ttl": 300},
    "week": {"hours": 24 * 7, "bucket": 1800, "ttl": 300},
    "month": {"hours": 24 * 30, "bucket": 7200, "ttl": 900},
}


class HistoryError(RuntimeError):
    """History could not be read from Home Assistant."""


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def bucket_points(states: list[dict], seconds: int) -> list[list[float]]:
    """Average numeric states into fixed buckets and return epoch milliseconds."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for state in states:
        value = _number(state.get("state"))
        timestamp = _timestamp(state.get("last_changed") or state.get("last_updated"))
        if value is None or timestamp is None:
            continue
        bucket = int(timestamp // seconds) * seconds
        buckets[bucket].append(value)
    return [[bucket * 1000, round(sum(values) / len(values), 4)] for bucket, values in sorted(buckets.items())]


def daily_changes(states: list[dict], local_zone=timezone.utc) -> list[list[float]]:
    """Convert a reset-safe cumulative energy sensor into per-day increments."""
    days: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for state in states:
        value = _number(state.get("state"))
        timestamp = _timestamp(state.get("last_changed") or state.get("last_updated"))
        if value is None or timestamp is None:
            continue
        day = datetime.fromtimestamp(timestamp, local_zone).date().isoformat()
        days[day].append((timestamp, value))
    result = []
    previous: float | None = None
    for day, samples in sorted(days.items()):
        samples.sort()
        first, last = samples[0][1], samples[-1][1]
        baseline = previous if previous is not None else first
        change = last - baseline if last >= baseline else last
        previous = last
        local_day = datetime.fromisoformat(day).date()
        noon = datetime.combine(local_day, datetime_time(hour=12), tzinfo=local_zone)
        result.append([int(noon.timestamp() * 1000), round(max(0.0, change), 4)])
    return result


class HistoryClient:
    """Fetch only predefined battery charts, with a short shared cache."""

    def __init__(self, token: str, api_root: str = "http://supervisor/core/api", opener=urlopen):
        self.token = token
        self.api_root = api_root.rstrip("/")
        self.opener = opener
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._time_zone_cache: tuple[float, str, tzinfo] | None = None

    def chart(self, name: str, range_name: str | None = None) -> dict:
        if name not in CHARTS:
            raise KeyError(name)
        if range_name is not None and range_name not in RANGES:
            raise ValueError(range_name)
        ttl = RANGES[range_name]["ttl"] if range_name else 900 if name == "multi_day" else 300
        cache_key = f"{name}:{range_name or 'default'}"
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached[0] < ttl:
                return {**cached[1], "cached": True}
        payload = self._fetch(name, range_name)
        with self._lock:
            self._cache[cache_key] = (time.time(), payload)
        return payload

    def _home_assistant_time_zone(self) -> tuple[str, tzinfo]:
        now = time.time()
        if self._time_zone_cache and now - self._time_zone_cache[0] < 3600:
            return self._time_zone_cache[1], self._time_zone_cache[2]
        name = "UTC"
        request = Request(
            f"{self.api_root}/config",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=10) as response:
                raw = json.load(response)
            if isinstance(raw, dict) and isinstance(raw.get("time_zone"), str):
                name = raw["time_zone"]
            zone = ZoneInfo(name)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ZoneInfoNotFoundError):
            name, zone = "UTC", timezone.utc
        self._time_zone_cache = (now, name, zone)
        return name, zone

    def _fetch(self, name: str, range_name: str | None = None) -> dict:
        if not self.token:
            raise HistoryError("Home Assistant API token is unavailable")
        config = CHARTS[name]
        range_config = RANGES.get(range_name, {})
        hours = int(range_config.get("hours", config["hours"]))
        bucket = max(int(config["bucket"]), int(range_config.get("bucket", 0)))
        time_zone_name, local_zone = self._home_assistant_time_zone()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        entity_ids = [entity[0] for entity in config["entities"]]
        query = urlencode({
            "filter_entity_id": ",".join(entity_ids),
            "end_time": end.isoformat().replace("+00:00", "Z"),
            "minimal_response": "",
            "no_attributes": "",
        })
        start_path = quote(start.isoformat().replace("+00:00", "Z"), safe=":-")
        request = Request(
            f"{self.api_root}/history/period/{start_path}?{query}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=20) as response:
                raw = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HistoryError(f"Home Assistant history request failed: {exc}") from exc
        by_entity = {states[0].get("entity_id"): states for states in raw if states and states[0].get("entity_id")}
        series = []
        for entity_id, label, unit in config["entities"]:
            states = by_entity.get(entity_id, [])
            points = daily_changes(states, local_zone) if config.get("daily_change") else bucket_points(states, bucket)
            series.append({"entity_id": entity_id, "name": label, "unit": unit, "points": points})
        return {
            "chart": name,
            "range": range_name or "default",
            "time_zone": time_zone_name,
            "generated_at": end.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "hours": hours,
            "bucket_seconds": bucket,
            "cached": False,
            "series": series,
        }
