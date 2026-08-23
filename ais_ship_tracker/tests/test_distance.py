import importlib.util
import gzip
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch


TRACKER = Path(__file__).parents[1] / "ais_ship_tracker.py"


def load_tracker(overrides=None):
    options = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    config = {
        "latitude_south": 37.70,
        "longitude_west": 15.00,
        "latitude_north": 37.80,
        "longitude_east": 15.10,
    }
    config.update(overrides or {})
    json.dump(config, options)
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
        self._position_is_confidently_inland = tracker.position_is_confidently_inland
        tracker.position_is_confidently_inland = lambda latitude, longitude, clearance_km=3.0: False
        tracker.dashboard_vessels.clear()
        tracker.position_filter_state.update({
            "rejected_total": 0,
            "rejected_by_reason": {},
            "last_rejected": None,
        })
        tracker._position_is_confidently_inland.cache_clear()

    def tearDown(self):
        tracker.position_is_confidently_inland = self._position_is_confidently_inland

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

    def test_positioned_vessels_are_not_limited_to_ten(self):
        tracker.dashboard_vessels.update({
            str(200000000 + index): {
                "mmsi": str(200000000 + index), "name": f"Vessel {index}",
                "area_id": "baiamonte", "latitude": 37.5 + index / 1000, "longitude": 15.0,
            }
            for index in range(18)
        })
        self.assertEqual(len(tracker.dashboard_snapshot("baiamonte", compact=True)["vessels"]), 18)
        self.assertEqual(len(tracker.dashboard_snapshot("baiamonte")["nearest_vessels"]), 18)

    def test_area_filtered_compact_tv_snapshot_reduces_work(self):
        tracker.dashboard_vessels.update({
            "sicily": {"mmsi": "247000001", "name": "Sicily", "area_id": "baiamonte", "latitude": 37.75, "longitude": 15.05, "call_sign": "IT1"},
            "miami": {"mmsi": "367000001", "name": "Miami", "area_id": "miami", "latitude": 25.80, "longitude": -80.10},
        })
        snapshot = tracker.dashboard_snapshot("baiamonte", compact=True)
        self.assertEqual(snapshot["config"]["area_id"], "baiamonte")
        self.assertEqual([vessel["name"] for vessel in snapshot["vessels"]], ["Sicily"])
        self.assertNotIn("call_sign", snapshot["vessels"][0])
        self.assertNotIn("events", snapshot)
        self.assertNotIn("receiver_log", snapshot)
        self.assertNotIn("flightaware_weather", snapshot)

    def test_large_status_payload_is_gzip_compressed_for_browsers(self):
        payload = (b'{"vessels":[]}' * 200)
        encoded, encoding = tracker.compress_http_payload(payload, "br, gzip")
        self.assertEqual(encoding, "gzip")
        self.assertEqual(gzip.decompress(encoded), payload)
        self.assertLess(len(encoded), len(payload) // 4)

    def test_small_status_payload_is_not_compressed(self):
        payload = b'{}'
        encoded, encoding = tracker.compress_http_payload(payload, "gzip")
        self.assertIsNone(encoding)
        self.assertEqual(encoded, payload)

    def test_gzip_quality_zero_is_honored(self):
        payload = (b'{"vessels":[]}' * 200)
        encoded, encoding = tracker.compress_http_payload(payload, "br, gzip;q=0")
        self.assertIsNone(encoding)
        self.assertEqual(encoded, payload)

    def test_explicit_gzip_quality_overrides_wildcard(self):
        payload = (b'{"vessels":[]}' * 200)
        encoded, encoding = tracker.compress_http_payload(payload, "*;q=1, gzip;q=0")
        self.assertIsNone(encoding)
        self.assertEqual(encoded, payload)

    def test_legacy_x_gzip_alias_is_supported(self):
        payload = (b'{"vessels":[]}' * 200)
        encoded, encoding = tracker.compress_http_payload(payload, "x-gzip, identity;q=0")
        self.assertEqual(encoding, "gzip")
        self.assertEqual(gzip.decompress(encoded), payload)

    def test_legacy_x_gzip_quality_zero_is_honored(self):
        payload = (b'{"vessels":[]}' * 200)
        encoded, encoding = tracker.compress_http_payload(payload, "x-gzip;q=0")
        self.assertIsNone(encoding)
        self.assertEqual(encoded, payload)

    def test_position_outside_every_operating_area_is_rejected(self):
        accepted = tracker.remember_dashboard_vessel({
            "mmsi": "247000001", "name": "Impossible target",
            "latitude": 40.0, "longitude": 18.0, "area_id": "baiamonte",
        })
        self.assertFalse(accepted)
        self.assertNotIn("247000001", tracker.dashboard_vessels)
        self.assertEqual(
            tracker.position_filter_state["last_rejected"]["reason"],
            "outside_operating_area",
        )

    def test_confidently_inland_position_is_rejected(self):
        previous = tracker.position_is_confidently_inland
        tracker.position_is_confidently_inland = lambda latitude, longitude, clearance_km=3.0: True
        try:
            accepted = tracker.remember_dashboard_vessel({
                "mmsi": "247000002", "name": "Inland target",
                "latitude": 37.75, "longitude": 15.05, "area_id": "baiamonte",
            })
        finally:
            tracker.position_is_confidently_inland = previous
        self.assertFalse(accepted)
        self.assertNotIn("247000002", tracker.dashboard_vessels)
        self.assertEqual(
            tracker.position_filter_state["last_rejected"]["reason"],
            "inland_position",
        )

    def test_snapshot_purges_preexisting_invalid_cached_positions(self):
        tracker.dashboard_vessels["247000003"] = {
            "mmsi": "247000003", "name": "Cached bad target",
            "latitude": 40.0, "longitude": 18.0, "area_id": "baiamonte",
        }
        snapshot = tracker.dashboard_snapshot()
        self.assertEqual(snapshot["vessels"], [])
        self.assertNotIn("247000003", tracker.dashboard_vessels)
        self.assertEqual(snapshot["position_filter"]["rejected_total"], 1)

    def test_snapshot_includes_receiver_gps_weather_and_hardware_log(self):
        tracker.receiver_logs.clear()
        tracker.log("AIS receiver test event")
        snapshot = tracker.dashboard_snapshot()
        self.assertEqual(snapshot["config"]["receiver_mode"], "sdr")
        self.assertEqual(snapshot["config"]["receiver_channel"], "dual")
        self.assertEqual(snapshot["config"]["tv_default_map_area"], "baiamonte")
        self.assertTrue(snapshot["config"]["dashboard_map_vessels"])
        self.assertTrue(snapshot["config"]["tv_map_vessels"])
        self.assertTrue(snapshot["config"]["tv_live_traffic_only"])
        self.assertEqual(snapshot["config"]["tv_target_size"], 100)
        self.assertTrue(snapshot["config"]["rahamin_proxy_enabled"])
        self.assertEqual(snapshot["config"]["aishub_data_source"], "rahamin_proxy")
        self.assertFalse(snapshot["config"]["aishub_username_in_use"])
        self.assertEqual(snapshot["config"]["rahamin_proxy_interval"], 15)
        self.assertIn("rahamin_proxy", snapshot)
        self.assertIn("reference_location", snapshot["config"])
        self.assertFalse(snapshot["flightaware_weather"]["enabled"])
        self.assertEqual(snapshot["receiver_log"][0]["message"], "AIS receiver test event")
        self.assertTrue(snapshot["decoder"]["enabled"])
        self.assertIn("marine_vhf", snapshot)
        self.assertFalse(snapshot["marine_vhf"]["enabled"])
        self.assertFalse(snapshot["config"]["sharing_enabled"])
        self.assertEqual(snapshot["feed"]["sharing_state"], "Disabled")

    def test_receiver_health_is_independent_from_aishub(self):
        previous_feed = tracker.feed_state["state"]
        previous_decoder = tracker.decoder_state["state"]
        previous_proxy = tracker.rahamin_proxy_state["state"]
        try:
            tracker.feed_state["state"] = "Receiving"
            tracker.decoder_state["state"] = "Error"
            tracker.rahamin_proxy_state["state"] = "Proxy error"
            tracker.aishub_state.update({"state": "Credentials rejected", "error": "Invalid username or password"})
            self.assertTrue(tracker.receiver_path_operational())
        finally:
            tracker.feed_state["state"] = previous_feed
            tracker.decoder_state["state"] = previous_decoder
            tracker.rahamin_proxy_state["state"] = previous_proxy

    def test_network_data_source_is_authoritative(self):
        direct = load_tracker({
            "aishub_data_source": "direct_aishub",
            "aishub_username": "TEST_ONLY",
            "rahamin_proxy_enabled": True,
        })
        snapshot = direct.dashboard_snapshot()
        self.assertFalse(snapshot["config"]["rahamin_proxy_enabled"])
        self.assertEqual(snapshot["config"]["aishub_data_source"], "direct_aishub")
        self.assertTrue(snapshot["config"]["aishub_username_in_use"])

    def test_pasted_aishub_feed_url_is_normalized_for_udp(self):
        configured = load_tracker({
            "aishub_feed_host": "http://data.aishub.net/",
            "aishub_feed_port": 2261,
        })
        self.assertEqual(configured.AISHUB_FEED_HOST, "data.aishub.net")


class AisCatcherTests(unittest.TestCase):
    def setUp(self):
        self._position_is_confidently_inland = tracker.position_is_confidently_inland
        tracker.position_is_confidently_inland = lambda latitude, longitude, clearance_km=3.0: False
        tracker.dashboard_vessels.clear()
        tracker.static_ship_data.clear()
        tracker.nmea_fragment_buffer.clear()

    def tearDown(self):
        tracker.position_is_confidently_inland = self._position_is_confidently_inland

    def test_nooelec_safe_default_command(self):
        command = tracker.build_ais_catcher_command("/usr/local/bin/AIS-catcher")
        self.assertEqual(command[0:2], ["/usr/local/bin/AIS-catcher", "-d:0"])
        self.assertIn("-gr", command)
        self.assertEqual(command[command.index("TUNER") + 1], "auto")
        self.assertEqual(command[command.index("RTLAGC") + 1], "on")
        self.assertEqual(command[command.index("BIASTEE") + 1], "off")
        self.assertEqual(command[command.index("-a") + 1], "192K")
        self.assertEqual(command[-3:], ["10110", "JSON_FULL", "on"])

    def test_identical_sdrs_can_be_assigned_by_stable_usb_port(self):
        inventory = [
            {"index": 0, "port": "1-2.3", "serial": "00000001"},
            {"index": 1, "port": "1-2.4", "serial": "00000001"},
        ]
        self.assertEqual(tracker.resolve_rtl_sdr_selector("port:1-2.3", "AIS", inventory), "0")
        self.assertEqual(tracker.resolve_rtl_sdr_selector("port:1-2.4", "marine VHF", inventory, "0"), "1")
        with self.assertRaisesRegex(ValueError, "duplicated"):
            tracker.resolve_rtl_sdr_selector("serial:00000001", "AIS", inventory)

    def test_auto_marine_assignment_avoids_ais_radio(self):
        inventory = [
            {"index": 0, "port": "1-2.3", "serial": "AIS001"},
            {"index": 1, "port": "1-2.4", "serial": "VHF001"},
        ]
        self.assertEqual(tracker.resolve_rtl_sdr_selector("auto", "AIS", inventory), "0")
        self.assertEqual(tracker.resolve_rtl_sdr_selector("auto", "marine VHF", inventory, "0"), "1")

    def test_json_full_updates_local_vessel_and_preserves_nmea(self):
        payload = json.dumps({
            "class": "AIS",
            "type": 1,
            "mmsi": 247123456,
            "lat": 37.75,
            "lon": 15.05,
            "speed": 8.5,
            "course": 121.2,
            "heading": 120,
            "status": 0,
            "status_text": "Under way using engine",
            "nmea": ["!AIVDM,1,1,,A,TEST,0*00"],
        }).encode()
        lines, ignored, local_updates = tracker.decode_receiver_payload(payload)
        self.assertEqual(lines, ["!AIVDM,1,1,,A,TEST,0*00"])
        self.assertEqual(ignored, 0)
        self.assertEqual(local_updates, 1)
        self.assertEqual(tracker.dashboard_vessels["247123456"]["source"], "Local AIS-catcher")
        self.assertEqual(tracker.dashboard_vessels["247123456"]["nav_status_string"], "Under way using engine")

    def test_static_message_is_merged_into_later_position(self):
        tracker.decode_receiver_payload(json.dumps({
            "class": "AIS", "type": 5, "mmsi": 247123456,
            "shipname": "BAIAMONTE TEST", "destination": "CATANIA", "shiptype": 70,
            "nmea": ["!AIVDM,2,1,1,A,STATIC,0*00"],
        }).encode())
        tracker.decode_receiver_payload(json.dumps({
            "class": "AIS", "type": 1, "mmsi": 247123456,
            "lat": 37.75, "lon": 15.05, "nmea": ["!AIVDM,1,1,,A,POSITION,0*00"],
        }).encode())
        vessel = tracker.dashboard_vessels["247123456"]
        self.assertEqual(vessel["name"], "BAIAMONTE TEST")
        self.assertEqual(vessel["destination"], "CATANIA")

    def test_container_build_pins_ais_catcher_and_rtlsdr_runtime(self):
        dockerfile = (TRACKER.parent / "Dockerfile").read_text()
        self.assertIn("AISCATCHER_VERSION=v0.70", dockerfile)
        self.assertIn("pyais==3.2.1", dockerfile)
        self.assertIn("global-land-mask==1.0.0", dockerfile)
        self.assertIn("numpy==1.26.4", dockerfile)
        self.assertIn("AS land-mask-build", dockerfile)
        self.assertIn("COPY --from=land-mask-build /compact-land-mask.zip", dockerfile)
        self.assertEqual(dockerfile.count("RUN pip install --no-cache-dir pyais==3.2.1"), 1)
        self.assertIn("librtlsdr0", dockerfile)
        self.assertIn("/usr/local/bin/AIS-catcher", dockerfile)
        self.assertIn("RTL_AIRBAND_VERSION=v5.2.0", dockerfile)
        self.assertIn("-DNFM=TRUE", dockerfile)
        self.assertIn("icecast2", dockerfile)
        self.assertIn("libfftw3-single3", dockerfile)
        self.assertIn("/usr/local/bin/rtl_airband", dockerfile)

    def test_udp_proxy_nmea_decodes_into_miami_map_vessel(self):
        previous_mode = tracker.RECEIVER_MODE
        previous_name = tracker.RECEIVER_NAME
        tracker.RECEIVER_MODE = "udp"
        tracker.RECEIVER_NAME = "Rahamin AIS Miami"
        try:
            payload = b"!AIVDM,1,1,,A,ENk`s@l973h9@6:@@@@@@@@@@@@=8UnD7MjBp00003vP000,2*26\r\n"
            lines, ignored, local_updates = tracker.decode_receiver_payload(payload)
        finally:
            tracker.RECEIVER_MODE = previous_mode
            tracker.RECEIVER_NAME = previous_name
        self.assertEqual(len(lines), 1)
        self.assertEqual(ignored, 0)
        self.assertEqual(local_updates, 1)
        vessel = tracker.dashboard_vessels["993672003"]
        self.assertEqual(vessel["name"], "RNG R LT")
        self.assertEqual(vessel["area_id"], "miami")
        self.assertEqual(vessel["station"], "Rahamin AIS Miami")
        self.assertEqual(vessel["source"], "Network AIS · Rahamin AIS Miami")

    def test_udp_proxy_reassembles_multipart_static_message(self):
        previous_mode = tracker.RECEIVER_MODE
        tracker.RECEIVER_MODE = "udp"
        try:
            first = b"!AIVDM,2,1,4,A,55O0W7`00001L@gCWGA2uItLth@DqtL5@F22220j1h742t0Ht0000000,0*08\r\n"
            second = b"!AIVDM,2,2,4,A,000000000000000,2*20\r\n"
            self.assertEqual(tracker.decode_receiver_payload(first)[2], 0)
            self.assertEqual(tracker.decode_receiver_payload(second)[2], 0)
        finally:
            tracker.RECEIVER_MODE = previous_mode
        static = tracker.static_ship_data["368060190"]
        self.assertEqual(static["name"], "P/V_GOLDEN_GATE")
        self.assertEqual(static["call_sign"], "WDK4954")

    def test_private_status_proxy_imports_valid_miami_vessels(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "367123456", "name": "RAHAMIN PROXY", "latitude": 25.82, "longitude": -80.14,
            "sog": 7.2, "cog": 95, "heading": 96, "destination": "MIAMI", "vessel_type": "Cargo",
        })
        outside = tracker.process_rahamin_proxy_record({
            "mmsi": "367999999", "name": "OUTSIDE", "latitude": 95.0, "longitude": -70.0,
        })
        self.assertTrue(imported)
        self.assertFalse(outside)
        vessel = tracker.dashboard_vessels["367123456"]
        self.assertEqual(vessel["area_id"], "miami")
        self.assertEqual(vessel["source"], "Rahamin AIS private proxy · Rahamin Miami")
        self.assertEqual(vessel["destination"], "MIAMI")

    def test_private_proxy_trusts_source_area_when_local_bounds_differ(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "367222222", "name": "RAHAMIN EDGE", "latitude": 26.70, "longitude": -80.10,
            "sog": 11.4, "cog": 175,
        }, "miami")
        self.assertTrue(imported)
        self.assertEqual(tracker.dashboard_vessels["367222222"]["area_id"], "miami")

    def test_private_proxy_accepts_standard_ais_and_nested_attribute_fields(self):
        self.assertTrue(tracker.process_rahamin_proxy_record({
            "attributes": {"MMSI": 367333333, "LATITUDE": 25.82, "LONGITUDE": -80.14},
            "NAME": "RAHAMIN UPPER", "SOG": 8.5, "COG": 91, "DEST": "MIAMI",
        }, "miami"))
        vessel = tracker.dashboard_vessels["367333333"]
        self.assertEqual(vessel["name"], "RAHAMIN UPPER")
        self.assertEqual(vessel["destination"], "MIAMI")

    def test_private_proxy_extracts_all_supported_payload_shapes(self):
        records = [{"MMSI": "367444444"}]
        self.assertIs(tracker.rahamin_proxy_records(records), records)
        self.assertIs(tracker.rahamin_proxy_records({"vessels": records}), records)
        self.assertIs(tracker.rahamin_proxy_records({"VESSELS": records}), records)
        self.assertIs(tracker.rahamin_proxy_records({"records": records}), records)

    def test_private_proxy_targets_do_not_expand_configured_watch_area(self):
        payload = {"config": {"map_areas": [{
            "id": "miami", "bounds": {"south": 25.4, "west": -80.6, "north": 26.3, "east": -79.7},
        }]}}
        bounds = tracker.rahamin_proxy_map_bounds(payload, "miami", [{
            "MMSI": "367444444", "LATITUDE": 26.5, "LONGITUDE": -80.2,
        }])
        self.assertEqual(bounds, tracker.MAP_AREAS["miami"]["bounds"])

    def test_dashboard_keeps_configured_bounds_when_proxy_reports_global_coverage(self):
        proxy_area = tracker.rahamin_proxy_state["areas"]["miami"]
        previous = dict(proxy_area)
        try:
            proxy_area.update({
                "state": "Connected",
                "map_bounds": {"south": 25.2, "west": -80.7, "north": 26.6, "east": -79.6},
            })
            miami = next(area for area in tracker.dashboard_map_areas() if area["id"] == "miami")
            self.assertEqual(miami["bounds"], tracker.MAP_AREAS["miami"]["bounds"])
        finally:
            proxy_area.clear()
            proxy_area.update(previous)

    def test_proxy_map_coverage_expands_but_does_not_shrink(self):
        existing = {"south": 25.0, "west": -81.0, "north": 27.0, "east": -79.0}
        refresh = {"south": 25.5, "west": -80.5, "north": 26.5, "east": -79.5}
        self.assertEqual(tracker.union_map_bounds(existing, refresh), existing)

    def test_private_status_proxy_imports_sicily_cache_into_baiamonte_map(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123456", "name": "SICILY PROXY", "latitude": 37.62, "longitude": 15.18,
            "sog": 9.1, "cog": 210, "destination": "CATANIA", "vessel_type": "Cargo",
        }, "baiamonte")
        self.assertTrue(imported)
        vessel = tracker.dashboard_vessels["247123456"]
        self.assertEqual(vessel["area_id"], "baiamonte")
        self.assertEqual(vessel["station"], "Baiamonte AIS")
        self.assertEqual(vessel["source"], "Rahamin AIS private proxy · Baiamonte Sicily")
        self.assertEqual(vessel["destination"], "CATANIA")
        self.assertEqual(tracker.dashboard_events[0]["area_id"], "baiamonte")
        self.assertEqual(tracker.dashboard_events[0]["area_name"], "Baiamonte Sicily")

    def test_private_status_proxy_rejects_stale_timestamped_contact(self):
        generated_at = tracker.utc_now_iso()
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123457", "name": "STALE PROXY", "latitude": 37.62, "longitude": 15.18,
            "last_seen": "2026-08-12T10:00:00+00:00",
        }, "baiamonte", generated_at)
        self.assertFalse(imported)
        self.assertNotIn("247123457", tracker.dashboard_vessels)

    def test_private_status_proxy_keeps_fresh_timestamped_contact(self):
        generated_at = tracker.utc_now_iso()
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123458", "name": "FRESH PROXY", "latitude": 37.62, "longitude": 15.18,
            "last_seen": generated_at,
        }, "baiamonte", generated_at)
        self.assertTrue(imported)
        self.assertEqual(tracker.dashboard_vessels["247123458"]["source_last_seen"], generated_at)

    def test_private_status_proxy_accepts_fresh_naive_remote_clock(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123460", "name": "REMOTE CLOCK", "latitude": 38.13, "longitude": 15.06,
            "last_seen": "2026-08-16T16:23:11",
        }, "baiamonte", "2026-08-16T16:25:19")
        self.assertTrue(imported)
        self.assertIn("247123460", tracker.dashboard_vessels)

    def test_private_status_proxy_rejects_stale_contact_on_naive_remote_clock(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123461", "name": "STALE REMOTE", "latitude": 38.13, "longitude": 15.06,
            "last_seen": "2026-08-16T15:25:19",
        }, "baiamonte", "2026-08-16T16:25:19")
        self.assertFalse(imported)
        self.assertNotIn("247123461", tracker.dashboard_vessels)

    def test_private_status_proxy_rejects_entire_stale_snapshot(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "247123459", "name": "CACHED PROXY", "latitude": 37.62, "longitude": 15.18,
            "last_seen": "2026-08-12T10:00:00+00:00",
        }, "baiamonte", "2026-08-12T10:00:30+00:00")
        self.assertFalse(imported)
        self.assertNotIn("247123459", tracker.dashboard_vessels)

    def test_private_proxy_area_url_preserves_existing_query(self):
        previous_url = tracker.RAHAMIN_PROXY_URL
        tracker.RAHAMIN_PROXY_URL = "http://192.168.86.196:8999/api/status?token=local&area=miami"
        try:
            url = tracker.rahamin_proxy_area_url("baiamonte")
        finally:
            tracker.RAHAMIN_PROXY_URL = previous_url
        self.assertEqual(url, "http://192.168.86.196:8999/api/status?token=local&area=baiamonte")


class MarineVhfTests(unittest.TestCase):
    def test_config_uses_second_nooelec_and_private_audio_mount(self):
        rendered = tracker.build_marine_vhf_config("test-secret")
        self.assertIn("index = 1;", rendered)
        self.assertIn('mode = "scan";', rendered)
        self.assertIn('modulation = "nfm";', rendered)
        self.assertIn("156.800", rendered)
        self.assertIn('mountpoint = "baiamonte-marine.mp3";', rendered)
        self.assertIn('password = "test-secret";', rendered)

    def test_config_uses_adaptive_squelch_by_default(self):
        previous = tracker.MARINE_VHF_AUTO_SQUELCH
        tracker.MARINE_VHF_AUTO_SQUELCH = True
        try:
            rendered = tracker.build_marine_vhf_config("secret")
            self.assertNotIn("squelch_threshold", rendered)
        finally:
            tracker.MARINE_VHF_AUTO_SQUELCH = previous

    def test_manual_squelch_is_emitted_only_when_selected(self):
        previous = tracker.MARINE_VHF_AUTO_SQUELCH
        tracker.MARINE_VHF_AUTO_SQUELCH = False
        try:
            rendered = tracker.build_marine_vhf_config("secret")
            self.assertIn(f"squelch_threshold = {tracker.MARINE_VHF_SQUELCH};", rendered)
        finally:
            tracker.MARINE_VHF_AUTO_SQUELCH = previous

    def test_serial_device_is_supported(self):
        previous = tracker.MARINE_VHF_DEVICE
        tracker.MARINE_VHF_DEVICE = "MARINE002"
        try:
            rendered = tracker.build_marine_vhf_config("secret")
            self.assertIn('serial = "MARINE002";', rendered)
            self.assertNotIn("index =", rendered)
        finally:
            tracker.MARINE_VHF_DEVICE = previous

    def test_device_conflict_requires_distinct_radios(self):
        previous = (tracker.MARINE_VHF_ENABLED, tracker.RECEIVER_MODE, tracker.MARINE_VHF_DEVICE, tracker.SDR_DEVICE)
        tracker.MARINE_VHF_ENABLED = True
        tracker.RECEIVER_MODE = "sdr"
        tracker.MARINE_VHF_DEVICE = "0"
        tracker.SDR_DEVICE = "0"
        try:
            self.assertTrue(tracker.marine_vhf_device_conflict())
            tracker.MARINE_VHF_DEVICE = "1"
            self.assertFalse(tracker.marine_vhf_device_conflict())
        finally:
            tracker.MARINE_VHF_ENABLED, tracker.RECEIVER_MODE, tracker.MARINE_VHF_DEVICE, tracker.SDR_DEVICE = previous

    def test_manual_usb_recovery_requires_explicit_configuration(self):
        previous = (tracker.MARINE_VHF_ENABLED, tracker.MARINE_VHF_USB_RESET_ENABLED)
        tracker.MARINE_VHF_ENABLED = True
        tracker.MARINE_VHF_USB_RESET_ENABLED = False
        try:
            accepted, message = tracker.request_marine_vhf_recovery()
            self.assertFalse(accepted)
            self.assertIn("configuration", message)
            self.assertFalse(tracker.marine_vhf_recovery_requested.is_set())
        finally:
            tracker.MARINE_VHF_ENABLED, tracker.MARINE_VHF_USB_RESET_ENABLED = previous

    def test_usb_recovery_resets_only_resolved_marine_device(self):
        previous_enabled = tracker.MARINE_VHF_USB_RESET_ENABLED
        previous_resets = tracker.marine_vhf_state["usb_resets"]
        tracker.MARINE_VHF_USB_RESET_ENABLED = True
        inventory = [{"index": 1, "port": "1-2.4", "device_node": "/dev/bus/usb/001/009"}]
        try:
            with patch.object(tracker, "resolved_radio_devices", return_value=("0", "1", inventory)), \
                    patch.object(tracker.os, "open", return_value=71) as open_device, \
                    patch.object(tracker.os, "close") as close_device, \
                    patch.object(tracker.fcntl, "ioctl") as reset_device:
                tracker.reset_marine_vhf_usb()
            open_device.assert_called_once_with("/dev/bus/usb/001/009", tracker.os.O_WRONLY)
            reset_device.assert_called_once_with(71, tracker.USBDEVFS_RESET, 0)
            close_device.assert_called_once_with(71)
        finally:
            tracker.MARINE_VHF_USB_RESET_ENABLED = previous_enabled
            tracker.marine_vhf_state["usb_resets"] = previous_resets

    def test_snapshot_never_exposes_audio_password(self):
        snapshot = tracker.marine_vhf_snapshot()
        self.assertNotIn("password", snapshot)
        self.assertNotIn(tracker.MARINE_VHF_PASSWORD, json.dumps(snapshot))


class AisHubPayloadTests(unittest.TestCase):
    def setUp(self):
        self._position_is_confidently_inland = tracker.position_is_confidently_inland
        tracker.position_is_confidently_inland = lambda latitude, longitude, clearance_km=3.0: False

    def tearDown(self):
        tracker.position_is_confidently_inland = self._position_is_confidently_inland

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

    def test_same_aishub_api_builds_separate_sicily_and_miami_queries(self):
        sicily = urllib.parse.parse_qs(urllib.parse.urlparse(tracker.build_aishub_url("baiamonte")).query, keep_blank_values=True)
        miami = urllib.parse.parse_qs(urllib.parse.urlparse(tracker.build_aishub_url("miami")).query, keep_blank_values=True)
        self.assertEqual(sicily["username"], miami["username"])
        self.assertNotEqual(sicily["lonmin"], miami["lonmin"])
        self.assertLess(float(miami["lonmin"][0]), tracker.MIAMI_BOUNDS["west"])
        self.assertGreater(float(miami["lonmax"][0]), tracker.MIAMI_BOUNDS["east"])

    def test_miami_motion_classifies_inbound_and_outbound_contacts(self):
        area = tracker.MAP_AREAS["miami"]
        self.assertEqual(tracker.vessel_area_status(25.85, -80.50, 12, 90, area), "inbound")
        self.assertEqual(tracker.vessel_area_status(25.85, -80.50, 12, 270, area), "nearby")
        self.assertEqual(tracker.vessel_area_status(25.85, -80.20, 0, 0, area), "in_area")

    def test_miami_record_keeps_source_station_and_ship_details(self):
        tracker.dashboard_vessels.clear()
        tracker.process_aishub_record({
            "MMSI": 367123456, "NAME": "RAHAMIN TEST", "LATITUDE": 25.85,
            "LONGITUDE": -80.20, "SOG": 9.2, "COG": 45, "TYPE": 70,
            "A": 20, "B": 30, "C": 5, "D": 6, "DRAUGHT": 4.5,
            "DEST": "MIAMI", "CALLSIGN": "WTEST",
        }, "miami")
        vessel = tracker.dashboard_vessels["367123456"]
        self.assertEqual(vessel["area_id"], "miami")
        self.assertEqual(vessel["area_status"], "in_area")
        self.assertEqual(vessel["station"], "Rahamin AIS Miami")
        self.assertEqual(vessel["source"], "AISHub API")
        self.assertEqual(vessel["ship_width"], 11.0)
        self.assertEqual(vessel["draught"], 4.5)


class WeatherTileTests(unittest.TestCase):
    def test_current_rainviewer_hash_path_is_valid(self):
        self.assertTrue(tracker.valid_weather_tile_path("v2/radar/25dbbe425e29/256/7/67/48/2/1_1.png"))

    def test_weather_tile_path_rejects_traversal(self):
        self.assertFalse(tracker.valid_weather_tile_path("v2/radar/../../options.json"))


class DashboardAssetTests(unittest.TestCase):
    def test_dashboard_follows_browser_color_scheme(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        theme = (web / "theme.css").read_text()
        self.assertIn('href="theme.css?v=260"', dashboard)
        self.assertIn('media="(prefers-color-scheme: dark)"', dashboard)
        self.assertIn("@media(prefers-color-scheme:dark)", theme)
        self.assertIn("color-scheme:light dark", theme)

    def test_shared_maritime_flags_are_loaded_by_dashboard_and_tv(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        television = (web / "tv.html").read_text()
        flags = (web / "maritime-flags.js").read_text()
        self.assertIn("maritime-flags.js", dashboard)
        self.assertIn("maritime-flags.js", television)
        self.assertIn("midFromMmsi", flags)
        self.assertIn("'00','98','99'", flags)
        self.assertIn("247','IT','Italy", flags)
        self.assertIn("636 637','LR','Liberia", flags)

    def test_dashboard_defers_map_rendering_until_overview_is_visible(self):
        script = (TRACKER.parent / "web" / "app.js").read_text()
        self.assertIn("function mapIsVisible()", script)
        self.assertIn("if(!mapIsVisible())return", script)
        self.assertIn("Update delayed · retrying", script)

    def test_dashboard_and_tv_publish_baiamonte_browser_icons(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        television = (web / "tv.html").read_text()
        manifest = json.loads((web / "manifest.webmanifest").read_text())
        config = (TRACKER.parent / "config.yaml").read_text()
        for markup in (dashboard, television):
            self.assertIn('rel="icon"', markup)
            self.assertIn('rel="apple-touch-icon"', markup)
            self.assertIn('rel="manifest"', markup)
        self.assertTrue((web / "favicon-32.png").exists())
        self.assertTrue((web / "apple-touch-icon.png").exists())
        self.assertEqual([icon["sizes"] for icon in manifest["icons"]], ["192x192", "512x512"])
        self.assertIn('panel_icon: "mdi:ferry"', config)

    def test_dashboard_offers_labels_selected_and_tv_map_displays(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        script = (web / "app.js").read_text()
        styles = (web / "overview-map.css").read_text()
        self.assertIn('data-map-display="labels"', dashboard)
        self.assertIn('data-map-display="focus"', dashboard)
        self.assertIn('href="tv"', dashboard)
        self.assertIn("mapVesselDetail", script)
        self.assertIn("layoutMapLabels", script)
        self.assertIn("map-label-leader", script)
        self.assertIn("overviewVesselIcon", script)
        self.assertIn("capacity=Math.max", script)
        self.assertIn("event.area_id", script)
        self.assertIn("baiamonteAisMapDisplay", script)
        self.assertIn(".map-vessel-label", styles)
        self.assertIn(".vessel-symbol", styles)
        self.assertIn(".map-vessel-detail", styles)

    def test_dashboard_and_tv_offer_baiamonte_and_miami_api_areas(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        dashboard_script = (web / "app.js").read_text()
        television = (web / "tv.html").read_text()
        television_script = (web / "tv.js").read_text()
        self.assertIn('id="map-area-switch"', dashboard)
        self.assertIn("map_areas", dashboard_script)
        self.assertIn('id="tv-area-switch"', television)
        self.assertIn("tvParams.get('area')", television_script)
        self.assertIn("area_status==='inbound'", television_script)

    def test_dashboard_and_tv_offer_live_vessel_map_controls(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        dashboard_script = (web / "app.js").read_text()
        television = (web / "tv.html").read_text()
        television_script = (web / "tv.js").read_text()
        config = (TRACKER.parent / "config.yaml").read_text()
        self.assertIn('id="dashboard-vessels-toggle"', dashboard)
        self.assertIn("cfg.dashboard_map_vessels!==false", dashboard_script)
        self.assertIn('id="tv-vessels-toggle"', television)
        self.assertIn("config.tv_default_map_area||'baiamonte'", television_script)
        self.assertIn("layoutTvLabels", television_script)
        self.assertNotIn("declutterTvPoint", television_script)
        self.assertNotIn("boat-position-line", television_script)
        self.assertIn("function vesselIcon", television_script)
        self.assertIn("boat-passenger", (web / "tv.css").read_text())
        self.assertIn("flex:0 0 360px", (web / "tv.css").read_text())
        self.assertIn("padding:8px 5px", (web / "tv.css").read_text())
        self.assertIn("clamp(300px,23vw,390px)", (web / "tv.css").read_text())
        self.assertNotIn("liveTraffic.slice(0,10)", television_script)
        self.assertIn("liveTraffic.map(vesselRow)", television_script)
        self.assertIn("overflow-y:auto", (web / "tv.css").read_text())
        self.assertIn("grid-template-rows:minmax(0,1fr)", (web / "tv.css").read_text())
        self.assertIn("max-height:100%", (web / "tv.css").read_text())
        self.assertIn("#fleet{position:relative;display:flex;flex:0 0 360px;flex-direction:column;min-height:0", (web / "tv.css").read_text())
        self.assertIn("ArrowDown", television_script)
        self.assertIn("tvHomeCenter", television_script)
        self.assertIn("tvHomeView", television_script)
        self.assertIn("boundsKey", television_script)
        self.assertIn("const tvHomeViews={}", television_script)
        self.assertIn("return areas.length?areas:fallback", television_script)
        self.assertNotIn("config.reference_location", television_script)
        self.assertIn("renderedTiles[key]", television_script)
        self.assertIn("'PointerEvent' in window", television_script)
        self.assertIn("tvRefreshQueued", television_script)
        self.assertIn("?view=tv&area=", television_script)
        self.assertIn("(?:tv|t)", television_script)
        self.assertIn("if(!document.hidden)refresh()", television_script)
        self.assertIn("if(!tvMapExplore||!latest", television_script)
        self.assertIn("event.buttons!==1", television_script)
        self.assertIn("lostpointercapture", television_script)
        self.assertNotIn("scheduleTvReset", television_script)
        self.assertNotIn("tvResetTimer", television_script)
        self.assertIn("manualCenter={lat:home.center.lat,lon:home.center.lon}", television_script)
        self.assertIn("cancel(mapFrame)", television_script)
        self.assertIn('id="tv-map-explore" aria-label="Allow map pan and pinch"', television)
        self.assertIn('id="tv-map-reset" aria-label="Reset and lock map at the fixed AIS home location">Reset</button>', television)
        self.assertIn("vesselIsOnScreen", television_script)
        self.assertIn("vessel.last_seen||vessel.source_last_seen", television_script)
        self.assertIn("vesselSeenAt(v)", television_script)
        self.assertIn("parseAisTime", television_script)
        self.assertNotIn("const seen=Date.parse(v.source_last_seen||v.last_seen||'')", television_script)
        self.assertIn("stale after", television_script)
        self.assertIn('data-mmsi="${escapeHtml(vessel.mmsi)}"', television_script)
        self.assertIn("tvParams.get('map_zoom')", television_script)
        self.assertIn("tvParams.get('target_size')", television_script)
        self.assertIn("tvParams.has('target_size')", television_script)
        self.assertIn("clampedParam(tvParams.get('target_size'),30,180,100)", television_script)
        self.assertIn("Number(cfg.tv_target_size)||100", television_script)
        self.assertIn("area.id==='baiamonte'?2:0", television_script)
        self.assertIn("return{lat:37.55,lon:15.16}", television_script)
        self.assertNotIn('class="boat-position-line"', television_script)
        self.assertIn('tv_default_map_area: "baiamonte"', config)
        self.assertIn("dashboard_map_vessels: true", config)
        self.assertIn("tv_map_vessels: true", config)
        self.assertIn("tv_live_traffic_only: true", config)
        self.assertIn("tv_target_size: 100", config)

    def test_overview_and_tv_maps_reject_stale_weather_and_recenter(self):
        web = TRACKER.parent / "web"
        dashboard_script = (web / "app.js").read_text()
        television_script = (web / "tv.js").read_text()
        dashboard = (web / "index.html").read_text()
        self.assertIn("dashboardWeatherGeneration", dashboard_script)
        self.assertIn("generation!==dashboardWeatherGeneration", dashboard_script)
        self.assertIn("scheduleOverviewReset", dashboard_script)
        self.assertIn("resetOverviewMap();rerenderVisibleMap();refresh()", dashboard_script)
        self.assertEqual(dashboard_script.count("overviewMap.addEventListener('wheel'"), 1)
        self.assertIn("token!==weatherRenderToken", television_script)
        self.assertNotIn("tvResetTimer", television_script)
        self.assertIn('app.js?v=2734', dashboard)
        self.assertNotIn("declutterOverviewPoint", dashboard_script)
        self.assertNotIn("marker-position-line", dashboard_script)
        self.assertIn("vessel.last_seen||vessel.source_last_seen", dashboard_script)
        self.assertIn("vesselSeenAt(v)", dashboard_script)
        self.assertIn("parseAisTime", dashboard_script)
        self.assertNotIn("const seen=Date.parse(v.source_last_seen||v.last_seen||'')", dashboard_script)
        self.assertIn("--overview-inverse-scale", dashboard_script)

    def test_status_timestamps_are_explicit_utc(self):
        source = TRACKER.read_text()
        self.assertIn("def utc_now_iso()", source)
        self.assertIn('replace("+00:00", "Z")', source)
        self.assertIn('merged["last_seen"] = utc_now_iso()', source)
        self.assertIn('"generated_at": utc_now_iso()', source)

    def test_dashboard_and_tv_treat_each_private_area_proxy_as_live(self):
        dashboard_script = (TRACKER.parent / "web" / "app.js").read_text()
        television_script = (TRACKER.parent / "web" / "tv.js").read_text()
        self.assertIn("proxyArea=(proxy.areas||{})[area.id]", dashboard_script)
        self.assertIn("proxyOperational=proxyArea.state==='Connected'", dashboard_script)
        self.assertIn("proxyArea=(proxy.areas||{})[area.id]", television_script)
        self.assertIn("proxyConnected=proxyArea.state==='Connected'", television_script)

    def test_watch_area_shows_the_decoder_profile(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        script = (web / "app.js").read_text()
        styles = (web / "decoder.css").read_text()
        self.assertIn('id="decoder-status"', dashboard)
        self.assertIn('id="decoder-tuning"', dashboard)
        self.assertIn("decoder.state", script)
        self.assertIn("grid-column: 1 / -1", styles)

    def test_dashboard_has_marine_radio_page_and_player(self):
        web = TRACKER.parent / "web"
        dashboard = (web / "index.html").read_text()
        script = (web / "app.js").read_text()
        styles = (web / "marine-radio.css").read_text()
        self.assertIn('data-page="radio"', dashboard)
        self.assertIn('id="marine-player"', dashboard)
        self.assertIn('id="marine-player" preload="none"', dashboard)
        self.assertNotIn('id="marine-player" controls', dashboard)
        self.assertIn('id="marine-listen-start"', dashboard)
        self.assertIn('id="marine-listen-stop"', dashboard)
        self.assertIn('id="marine-recover"', dashboard)
        self.assertIn("renderMarineRadio", script)
        self.assertIn("function stopMarineAudio", script)
        self.assertIn("player.removeAttribute('src')", script)
        self.assertNotIn("addEventListener('pause'", script)
        self.assertIn("addEventListener('playing'", script)
        self.assertIn("addEventListener('error'", script)
        self.assertIn("addEventListener('visibilitychange'", script)
        self.assertIn("addEventListener('pagehide'", script)
        self.assertIn("api/marine-radio/recover", script)
        self.assertIn("marine-radio-grid", styles)

    def test_marine_audio_proxy_forwards_small_chunks_promptly(self):
        source = TRACKER.read_text()
        self.assertIn('stream.read1(4096)', source)
        self.assertNotIn('stream.read(16384)', source)


if __name__ == "__main__":
    unittest.main()
