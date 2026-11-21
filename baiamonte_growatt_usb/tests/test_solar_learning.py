import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solar_learning import apply_learning, archive_forecast, evaluate_forecasts, load_learning, save_learning


class SolarLearningTests(unittest.TestCase):
    TODAY = date(2026, 12, 1)

    def records(self, actual_totals):
        profiles, records = {}, {}
        start = self.TODAY - timedelta(days=len(actual_totals))
        for index, energy in enumerate(actual_totals):
            day = start + timedelta(days=index)
            profiles[day] = {9: energy * 1000}
            records[day.isoformat()] = {"baseline_kwh": 10, "forecast_kwh": 10}
        return records, profiles

    def test_does_not_apply_a_gain_from_unvalidated_days(self):
        records, profiles = self.records([8] * 13)
        status = evaluate_forecasts(records, profiles, self.TODAY)
        self.assertEqual(status["scored_days"], 13)
        self.assertEqual(status["gain"], 1)

    def test_activates_only_when_replayed_forecasts_improve(self):
        records, profiles = self.records([8] * 14)
        status = evaluate_forecasts(records, profiles, self.TODAY)
        self.assertEqual(status["phase"], "calibrating")
        self.assertEqual(status["validation_days"], 7)
        self.assertEqual(status["gain"], 0.8)
        self.assertEqual(status["validation_mae_kwh"], 0)
        forecast = apply_learning({
            "total_kwh": 10, "solcast_used": True, "weather_hours": 10,
            "points": [{"power_w": 1000}],
        }, status)
        self.assertEqual(forecast["total_kwh"], 8)
        self.assertEqual(forecast["uncalibrated_kwh"], 10)
        self.assertEqual(forecast["points"][0]["power_w"], 800)

        records, profiles = self.records([8] * 7 + [12] * 7)
        status = evaluate_forecasts(records, profiles, self.TODAY)
        self.assertEqual(status["phase"], "monitoring")
        self.assertEqual(status["gain"], 1)

    def test_archives_only_day_ahead_and_can_reload_after_restart(self):
        recorded = {}
        forecast = {
            "target_day": "2026-11-22", "solcast_used": True, "weather_hours": 10,
            "total_kwh": 7.2, "uncalibrated_kwh": 8, "points": [{"power_w": 720}],
        }
        issued = datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc)
        archive_forecast(recorded, forecast, issued)
        archive_forecast(recorded, {**forecast, "total_kwh": 4}, issued + timedelta(hours=1))
        self.assertEqual(recorded["2026-11-22"]["forecast_kwh"], 7.2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "solar-learning.json"
            save_learning(path, recorded)
            self.assertEqual(load_learning(path), recorded)
