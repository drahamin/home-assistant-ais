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

    def test_snapshot_includes_receiver_gps_weather_and_hardware_log(self):
        tracker.receiver_logs.clear()
        tracker.log("AIS receiver test event")
        snapshot = tracker.dashboard_snapshot()
        self.assertEqual(snapshot["config"]["receiver_mode"], "sdr")
        self.assertEqual(snapshot["config"]["receiver_channel"], "dual")
        self.assertIn("reference_location", snapshot["config"])
        self.assertFalse(snapshot["flightaware_weather"]["enabled"])
        self.assertEqual(snapshot["receiver_log"][0]["message"], "AIS receiver test event")
        self.assertTrue(snapshot["decoder"]["enabled"])
        self.assertIn("marine_vhf", snapshot)
        self.assertFalse(snapshot["marine_vhf"]["enabled"])


class AisCatcherTests(unittest.TestCase):
    def setUp(self):
        tracker.dashboard_vessels.clear()
        tracker.static_ship_data.clear()

    def test_nooelec_safe_default_command(self):
        command = tracker.build_ais_catcher_command("/usr/local/bin/AIS-catcher")
        self.assertEqual(command[0:2], ["/usr/local/bin/AIS-catcher", "-d:0"])
        self.assertIn("-gr", command)
        self.assertEqual(command[command.index("TUNER") + 1], "auto")
        self.assertEqual(command[command.index("RTLAGC") + 1], "on")
        self.assertEqual(command[command.index("BIASTEE") + 1], "off")
        self.assertEqual(command[command.index("-a") + 1], "192K")
        self.assertEqual(command[-3:], ["10110", "JSON_FULL", "on"])

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
        self.assertIn("librtlsdr0", dockerfile)
        self.assertIn("/usr/local/bin/AIS-catcher", dockerfile)
        self.assertIn("RTL_AIRBAND_VERSION=v5.2.0", dockerfile)
        self.assertIn("-DNFM=TRUE", dockerfile)
        self.assertIn("icecast2", dockerfile)
        self.assertIn("libfftw3-single3", dockerfile)
        self.assertIn("/usr/local/bin/rtl_airband", dockerfile)


class MarineVhfTests(unittest.TestCase):
    def test_config_uses_second_nooelec_and_private_audio_mount(self):
        rendered = tracker.build_marine_vhf_config("test-secret")
        self.assertIn("index = 1;", rendered)
        self.assertIn('mode = "scan";', rendered)
        self.assertIn("156.800", rendered)
        self.assertIn('mountpoint = "baiamonte-marine.mp3";', rendered)
        self.assertIn('password = "test-secret";', rendered)

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

    def test_snapshot_never_exposes_audio_password(self):
        snapshot = tracker.marine_vhf_snapshot()
        self.assertNotIn("password", snapshot)
        self.assertNotIn(tracker.MARINE_VHF_PASSWORD, json.dumps(snapshot))


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


class DashboardAssetTests(unittest.TestCase):
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
        self.assertIn("baiamonteAisMapDisplay", script)
        self.assertIn(".map-vessel-label", styles)
        self.assertIn(".map-vessel-detail", styles)

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
        self.assertIn("renderMarineRadio", script)
        self.assertIn("marine-radio-grid", styles)


if __name__ == "__main__":
    unittest.main()
