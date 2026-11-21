import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

from astral import Observer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solar_forecast import midnight, local_forecast


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
