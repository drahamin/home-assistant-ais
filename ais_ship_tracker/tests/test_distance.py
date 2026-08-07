import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


TRACKER = Path(__file__).parents[1] / "ais_ship_tracker.py"


def load_tracker():
    options = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({
        "latitude_south": 37.70,
        "longitude_west": 15.00,
        "latitude_north": 37.80,
        "longitude_east": 15.10,
    }, options)
    options.close()
    previous = os.environ.get("BAIAMONTE_AIS_OPTIONS")
    os.environ["BAIAMONTE_AIS_OPTIONS"] = options.name
    try:
        spec = importlib.util.spec_from_file_location("baiamonte_ais_tracker", TRACKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            os.environ.pop("BAIAMONTE_AIS_OPTIONS", None)
        else:
            os.environ["BAIAMONTE_AIS_OPTIONS"] = previous
        os.unlink(options.name)


tracker = load_tracker()


class DistanceTests(unittest.TestCase):
    def setUp(self):
        tracker.dashboard_vessels.clear()

    def test_distance_is_great_circle_kilometres(self):
        self.assertAlmostEqual(tracker.distance_km(37.75, 15.00, 37.75, 15.10), 8.79, delta=0.1)

    def test_snapshot_returns_only_positioned_nearest_vessels(self):
        tracker.dashboard_vessels.update({
            "near": {"mmsi": "111111111", "name": "Near", "latitude": 37.75, "longitude": 15.05},
            "far": {"mmsi": "222222222", "name": "Far", "latitude": 37.80, "longitude": 15.10},
            "unknown": {"mmsi": "333333333", "name": "No position"},
        })
        snapshot = tracker.dashboard_snapshot()
        self.assertEqual([item["name"] for item in snapshot["nearest_vessels"]], ["Near", "Far"])
        self.assertIsNone(snapshot["vessels"][-1]["distance_km"])


class AisHubPayloadTests(unittest.TestCase):
    def test_documented_metadata_and_records_shape(self):
        records = [{"MMSI": 123456789, "NAME": "Test Vessel"}]
        payload = [{"ERROR": False, "RECORDS": 1}, records]
        self.assertEqual(tracker.parse_aishub_payload(payload), records)

    def test_zero_record_metadata_is_a_successful_empty_result(self):
        self.assertEqual(tracker.parse_aishub_payload([{"ERROR": False, "RECORDS": 0}]), [])

    def test_proxy_dictionary_shape_is_supported(self):
        records = [{"MMSI": 123456789}]
        self.assertEqual(
            tracker.parse_aishub_payload({"ERROR": False, "RECORDS": 1, "VESSELS": records}),
            records,
        )

    def test_aishub_error_message_is_preserved(self):
        with self.assertRaisesRegex(ValueError, "Access pending"):
            tracker.parse_aishub_payload([{"ERROR": True, "ERROR_MESSAGE": "Access pending"}])


class WeatherTileTests(unittest.TestCase):
    def test_current_rainviewer_hash_path_is_valid(self):
        self.assertTrue(tracker.valid_weather_tile_path("v2/radar/25dbbe425e29/256/7/67/48/2/1_1.png"))

    def test_weather_tile_path_rejects_traversal(self):
        self.assertFalse(tracker.valid_weather_tile_path("v2/radar/../../options.json"))


if __name__ == "__main__":
    unittest.main()
