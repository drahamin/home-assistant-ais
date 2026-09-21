"""Small, bounded Home Assistant history client for the battery charts."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


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


def daily_changes(states: list[dict]) -> list[list[float]]:
    """Convert a reset-safe cumulative energy sensor into per-day increments."""
    days: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for state in states:
        value = _number(state.get("state"))
        timestamp = _timestamp(state.get("last_changed") or state.get("last_updated"))
        if value is None or timestamp is None:
            continue
        day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        days[day].append((timestamp, value))
    result = []
    previous: float | None = None
    for day, samples in sorted(days.items()):
        samples.sort()
        first, last = samples[0][1], samples[-1][1]
        baseline = previous if previous is not None else first
        change = last - baseline if last >= baseline else last
        previous = last
        noon = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(hours=12)
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

    def chart(self, name: str) -> dict:
        if name not in CHARTS:
            raise KeyError(name)
        ttl = 900 if name == "multi_day" else 300
        with self._lock:
            cached = self._cache.get(name)
            if cached and time.time() - cached[0] < ttl:
                return {**cached[1], "cached": True}
        payload = self._fetch(name)
        with self._lock:
            self._cache[name] = (time.time(), payload)
        return payload

    def _fetch(self, name: str) -> dict:
        if not self.token:
            raise HistoryError("Home Assistant API token is unavailable")
        config = CHARTS[name]
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=config["hours"])
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
            points = daily_changes(states) if config.get("daily_change") else bucket_points(states, config["bucket"])
            series.append({"entity_id": entity_id, "name": label, "unit": unit, "points": points})
        return {
            "chart": name,
            "generated_at": end.isoformat(),
            "hours": config["hours"],
            "cached": False,
            "series": series,
        }
