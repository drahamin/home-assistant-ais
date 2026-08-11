import importlib.util
import json
import os
import tempfile
import unittest
import urllib.parse
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
        self.assertEqual(snapshot["config"]["tv_default_map_area"], "baiamonte")
        self.assertTrue(snapshot["config"]["dashboard_map_vessels"])
        self.assertTrue(snapshot["config"]["tv_map_vessels"])
        self.assertTrue(snapshot["config"]["tv_live_traffic_only"])
        self.assertTrue(snapshot["config"]["rahamin_proxy_enabled"])
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


class AisCatcherTests(unittest.TestCase):
    def setUp(self):
        tracker.dashboard_vessels.clear()
        tracker.static_ship_data.clear()
        tracker.nmea_fragment_buffer.clear()

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
        self.assertIn("pyais==3.2.1", dockerfile)
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

    def test_private_status_proxy_imports_only_miami_approach_vessels(self):
        imported = tracker.process_rahamin_proxy_record({
            "mmsi": "367123456", "name": "RAHAMIN PROXY", "latitude": 25.82, "longitude": -80.14,
            "sog": 7.2, "cog": 95, "heading": 96, "destination": "MIAMI", "vessel_type": "Cargo",
        })
        outside = tracker.process_rahamin_proxy_record({
            "mmsi": "367999999", "name": "OUTSIDE", "latitude": 40.0, "longitude": -70.0,
        })
        self.assertTrue(imported)
        self.assertFalse(outside)
        vessel = tracker.dashboard_vessels["367123456"]
        self.assertEqual(vessel["area_id"], "miami")
        self.assertEqual(vessel["source"], "Rahamin AIS private proxy · Rahamin Miami")
        self.assertEqual(vessel["destination"], "MIAMI")

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
        self.assertIn("URLSearchParams(location.search).get('area')", television_script)
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
        self.assertIn("const tvHomeViews={}", television_script)
        self.assertIn("return areas.length?areas:fallback", television_script)
        self.assertNotIn("config.reference_location", television_script)
        self.assertIn("renderedTiles[key]", television_script)
        self.assertIn("'PointerEvent' in window", television_script)
        self.assertIn("tvRefreshQueued", television_script)
        self.assertIn("if(!tvMapExplore||!latest", television_script)
        self.assertIn("event.buttons!==1", television_script)
        self.assertIn("lostpointercapture", television_script)
        self.assertNotIn("scheduleTvReset", television_script)
        self.assertNotIn("tvResetTimer", television_script)
        self.assertIn("manualCenter={lat:home.center.lat,lon:home.center.lon}", television_script)
        self.assertIn("cancel(mapFrame)", television_script)
        self.assertIn('id="tv-map-explore" aria-label="Allow map pan and pinch"', television)
        self.assertIn('id="tv-map-reset" aria-label="Reset and lock map at the fixed AIS home location">Reset</button>', television)
        self.assertIn("cfg.tv_live_traffic_only===false?nearest:visible", television_script)
        self.assertIn('tv_default_map_area: "baiamonte"', config)
        self.assertIn("dashboard_map_vessels: true", config)
        self.assertIn("tv_map_vessels: true", config)
        self.assertIn("tv_live_traffic_only: true", config)

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
        self.assertIn('app.js?v=2710', dashboard)

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
        self.assertIn("renderMarineRadio", script)
        self.assertIn("marine-radio-grid", styles)


if __name__ == "__main__":
    unittest.main()
