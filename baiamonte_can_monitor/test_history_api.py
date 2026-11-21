import io
import json
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
            if request.full_url.endswith("/config"):
                return Response(json.dumps({"time_zone": "Europe/Rome"}).encode())
            entity = "sensor.baiamonte_can_bank_soc"
            return Response(json.dumps([[{"entity_id": entity, "state": "42", "last_changed": "2026-09-14T10:00:00Z"}]]).encode())

        client = HistoryClient("secret", opener=opener)
        first = client.chart("charging")
        second = client.chart("charging")
        self.assertEqual(len(calls), 2)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertIn("filter_entity_id=sensor.baiamonte_can_bank_soc", calls[1][0])
        self.assertEqual(first["series"][0]["points"][0][1], 42)
        self.assertEqual(first["time_zone"], "Europe/Rome")

    def test_unknown_chart_is_rejected_without_request(self):
        client = HistoryClient("secret")
        with self.assertRaises(KeyError):
            client.chart("arbitrary")

    def test_unknown_range_is_rejected_without_request(self):
        client = HistoryClient("secret")
        with self.assertRaises(ValueError):
            client.chart("charging", "year")

    def test_battery_three_cell_chart_is_predefined(self):
        client = HistoryClient("secret", opener=lambda *_args, **_kwargs: Response(b"[]"))
        payload = client.chart("battery3_cells")
        self.assertEqual(len(payload["series"]), 16)
        self.assertEqual(payload["series"][0]["entity_id"], "sensor.baiamonte_can_battery_3_cell_1_voltage")

    def test_pack_power_and_health_charts_are_predefined(self):
        def opener(request, timeout):
            payload = {"service_response": {"statistics": {}}} if request.data else []
            return Response(json.dumps(payload).encode())

        client = HistoryClient("secret", opener=opener)
        power = client.chart("pack_power")
        health = client.chart("health")
        self.assertEqual(len(power["series"]), 3)
        self.assertEqual(power["series"][2]["entity_id"], "sensor.baiamonte_can_battery_3_net_input_power")
        self.assertEqual(health["hours"], 24 * 7)
        self.assertEqual(len(health["series"]), 3)

    def test_day_requests_raw_history_and_long_ranges_request_hourly_averages(self):
        calls = []
        entity = "sensor.baiamonte_can_bank_soc"

        def opener(request, timeout):
            calls.append(request)
            if request.full_url.endswith("/config"):
                return Response(b'{"time_zone":"Europe/Rome"}')
            if not request.data:
                return Response(b"[]")
            rows = [
                {"start": "2026-11-01T00:00:00Z", "mean": 40},
                {"start": "2026-11-01T01:00:00Z", "mean": 42},
                {"start": "2026-11-01T02:00:00Z"},
            ]
            return Response(json.dumps({"service_response": {"statistics": {entity: rows}}}).encode())

        client = HistoryClient("secret", opener=opener)
        day = client.chart("charging", "day")
        week = client.chart("charging", "week")
        month = client.chart("charging", "month")

        self.assertEqual([day["hours"], week["hours"], month["hours"]], [24, 168, 720])
        self.assertEqual([day["bucket_seconds"], week["bucket_seconds"], month["bucket_seconds"]], [300, 3600, 7200])
        self.assertIn("/history/period/", calls[1].full_url)
        for request in calls[2:]:
            self.assertTrue(request.full_url.endswith("/services/recorder/get_statistics?return_response"))
            body = json.loads(request.data)
            self.assertEqual(body["period"], "hour")
            self.assertEqual(body["types"], ["mean"])
            self.assertIn(entity, body["statistic_ids"])
        self.assertEqual([p[1] for p in week["series"][0]["points"]], [40, 42])
        self.assertEqual([p[1] for p in month["series"][0]["points"]], [41])
        self.assertFalse(month["series"][1]["points"])
        self.assertTrue(client.chart("charging", "month")["cached"])
        self.assertEqual(len(calls), 4)

    def test_long_range_energy_uses_recorder_daily_changes(self):
        entity = "sensor.baiamonte_can_bank_energy_charged"

        def opener(request, timeout):
            if request.full_url.endswith("/config"):
                return Response(b'{"time_zone":"Europe/Rome"}')
            body = json.loads(request.data)
            self.assertEqual(body["period"], "day")
            self.assertEqual(body["types"], ["change"])
            start = datetime.fromisoformat(body["start_time"])
            self.assertEqual(start.hour, 0)
            self.assertEqual(start.minute, 0)
            rows = [{"start": "2026-10-06T22:00:00Z", "change": 2.5}]
            return Response(json.dumps({"service_response": {"statistics": {entity: rows}}}).encode())

        payload = HistoryClient("secret", opener=opener).chart("multi_day", "week")
        timestamp, change = payload["series"][0]["points"][0]
        self.assertEqual(change, 2.5)
        local = datetime.fromtimestamp(timestamp / 1000, ZoneInfo("Europe/Rome"))
        self.assertEqual((local.day, local.hour), (7, 12))

    def test_daily_energy_uses_home_assistant_local_calendar_day(self):
        states = [
            {"state": "10", "last_changed": "2026-10-06T21:30:00Z"},
            {"state": "11", "last_changed": "2026-10-06T22:30:00Z"},
            {"state": "13", "last_changed": "2026-10-07T20:00:00Z"},
        ]

        points = daily_changes(states, ZoneInfo("Europe/Rome"))

        self.assertEqual([point[1] for point in points], [0.0, 3.0])
        self.assertEqual(datetime.fromtimestamp(points[1][0] / 1000, ZoneInfo("Europe/Rome")).hour, 12)


if __name__ == "__main__":
    unittest.main()
