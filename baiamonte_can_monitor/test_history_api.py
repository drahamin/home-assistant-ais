import io
import json
import unittest
from datetime import datetime, timezone

from history_api import HistoryClient, bucket_points, daily_changes


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class HistoryTests(unittest.TestCase):
    def test_bucket_points_averages_numeric_values_and_ignores_unknown(self):
        states = [
            {"state": "3.30", "last_changed": "2026-09-14T10:00:01Z"},
            {"state": "3.34", "last_changed": "2026-09-14T10:04:59Z"},
            {"state": "unknown", "last_changed": "2026-09-14T10:03:00Z"},
        ]
        points = bucket_points(states, 300)
        self.assertEqual(points[0][1], 3.32)

    def test_daily_changes_handles_cumulative_sensor_reset(self):
        states = [
            {"state": "10", "last_changed": "2026-09-12T00:00:00Z"},
            {"state": "12.5", "last_changed": "2026-09-12T23:00:00Z"},
            {"state": "13", "last_changed": "2026-09-13T01:00:00Z"},
            {"state": "1.5", "last_changed": "2026-09-13T23:00:00Z"},
        ]
        self.assertEqual([point[1] for point in daily_changes(states)], [2.5, 1.5])

    def test_client_fetches_only_predefined_entities_and_caches(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout, request.headers))
            entity = "sensor.baiamonte_can_bank_soc"
            return Response(json.dumps([[{"entity_id": entity, "state": "42", "last_changed": "2026-09-14T10:00:00Z"}]]).encode())

        client = HistoryClient("secret", opener=opener)
        first = client.chart("charging")
        second = client.chart("charging")
        self.assertEqual(len(calls), 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertIn("filter_entity_id=sensor.baiamonte_can_bank_soc", calls[0][0])
        self.assertEqual(first["series"][0]["points"][0][1], 42)

    def test_unknown_chart_is_rejected_without_request(self):
        client = HistoryClient("secret")
        with self.assertRaises(KeyError):
            client.chart("arbitrary")

    def test_battery_three_cell_chart_is_predefined(self):
        client = HistoryClient("secret", opener=lambda *_args, **_kwargs: Response(b"[]"))
        payload = client.chart("battery3_cells")
        self.assertEqual(len(payload["series"]), 16)
        self.assertEqual(payload["series"][0]["entity_id"], "sensor.baiamonte_can_battery_3_cell_1_voltage")


if __name__ == "__main__":
    unittest.main()
