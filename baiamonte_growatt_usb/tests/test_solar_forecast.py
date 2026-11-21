import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

from astral import Observer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solar_forecast import midnight, local_forecast, solcast_forecast, weather_hours


class LocalForecastTests(unittest.TestCase):
    def test_uses_five_complete_prior_days_and_keeps_the_profile_energy(self):
        today = date(2026, 11, 21)
        observer = Observer(37.9, 15.0)
        data = {"load": [], "bank": []}
        # Today is incomplete. Yesterday has a daylight gap. The next five days
        # have 100 to 500 W of charge and 200 W of AC load throughout daylight.
        for offset in range(7):
            start = midnight(today - timedelta(days=offset))
            for hour in range(24):
                if offset == 1 and hour == 11:
                    continue
                stamp = (start + timedelta(hours=hour)).isoformat()
                data["load"].append({"start": stamp, "mean": 200})
                data["bank"].append({"start": stamp, "mean": -100 * (offset - 1)})
        forecast = local_forecast(data, ["load", "bank"], observer, today)
        self.assertEqual(forecast["days_used"], 5)
        self.assertEqual(forecast["training_dates"][0], "2026-11-19")
        powers = [point["power_w"] for point in forecast["points"]]
        self.assertEqual((len(powers), powers[0], powers[-1], max(powers)), (24, 0, 0, 500))
        self.assertAlmostEqual(sum(powers) / 1000, forecast["total_kwh"], places=2)

    def test_missing_coverage_leaves_forecast_unavailable(self):
        result = local_forecast({}, ["load", "bank"], Observer(37.9, 15.0), date(2026, 11, 21))
        self.assertEqual(result["points"], [])
        self.assertNotIn("total_kwh", result)


    def test_solcast_and_cloud_weight_hourly_panel_shape(self):
        today = date(2026, 11, 21)
        observer = Observer(37.9, 15.0)
        data = {"load": [], "bank": []}
        for offset in range(1, 6):
            start = midnight(today - timedelta(days=offset))
            for hour in range(24):
                stamp = (start + timedelta(hours=hour)).isoformat()
                data["load"].append({"start": stamp, "mean": 200})
                data["bank"].append({"start": stamp, "mean": -500})
        day = today + timedelta(days=1)
        start = midnight(day)
        forecast_rows = [
            {"period_start": (start + timedelta(hours=hour)).isoformat(), "pv_estimate": 2 if hour < 12 else 1}
            for hour in range(7, 17)
        ]
        solcast = solcast_forecast({"state": "12", "attributes": {"detailedHourly": forecast_rows}}, day)
        covered = start + timedelta(hours=9)
        cloud = weather_hours([{"datetime": covered.isoformat(), "cloud_coverage": 100}])
        baseline = local_forecast(data, ["load", "bank"], observer, today, target_day=day)
        hybrid = local_forecast(
            data, ["load", "bank"], observer, today, target_day=day,
            solcast=solcast, cloud_hours=cloud,
        )
        self.assertTrue(hybrid["solcast_used"])
        self.assertEqual(hybrid["weather_hours"], 1)
        self.assertGreater(hybrid["total_kwh"], baseline["total_kwh"])
        self.assertLess(hybrid["points"][9]["power_w"], hybrid["points"][10]["power_w"])
        self.assertEqual(hybrid["points"][1]["power_w"], 0)
        self.assertAlmostEqual(sum(p["power_w"] for p in hybrid["points"]) / 1000, hybrid["total_kwh"], places=2)
