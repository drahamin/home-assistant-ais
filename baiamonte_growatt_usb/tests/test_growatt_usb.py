import importlib.util
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "growatt_usb.py"
SPEC = importlib.util.spec_from_file_location("growatt_usb", MODULE_PATH)
growatt = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(growatt)


class GrowattUsbTests(unittest.TestCase):
    def test_retry_delay_backs_off_and_caps_at_five_minutes(self):
        self.assertEqual(growatt.retry_delay(10, 1), 10)
        self.assertEqual(growatt.retry_delay(10, 4), 80)
        self.assertEqual(growatt.retry_delay(10, 20), 300)

    @patch.object(growatt.time, "monotonic", return_value=100.0)
    @patch.object(growatt, "candidate_devices", return_value=["/dev/ttyUSB0"])
    def test_dashboard_device_discovery_cache_avoids_repeated_globs(self, candidates, _monotonic):
        growatt.DEVICE_CACHE.update({"configured": None, "at": 0.0, "devices": []})
        self.assertEqual(growatt.cached_candidate_devices("auto"), ["/dev/ttyUSB0"])
        self.assertEqual(growatt.cached_candidate_devices("auto"), ["/dev/ttyUSB0"])
        candidates.assert_called_once_with("auto")

    def test_home_assistant_publish_is_rate_limited_and_heartbeated(self):
        old_token = growatt.TOKEN
        growatt.TOKEN = "test"
        growatt.PUBLISHED_STATES.clear()
        response = MagicMock()
        response.__enter__.return_value = response
        try:
            with patch.object(growatt.time, "monotonic", side_effect=[100.0, 110.0, 131.0, 440.0]), patch.object(growatt.urllib.request, "urlopen", return_value=response) as opened:
                self.assertTrue(growatt.publish_state("sensor.test", 1, {"name": "Test"}, 30))
                self.assertFalse(growatt.publish_state("sensor.test", 2, {"name": "Test"}, 30))
                self.assertTrue(growatt.publish_state("sensor.test", 2, {"name": "Test"}, 30))
                self.assertTrue(growatt.publish_state("sensor.test", 2, {"name": "Test"}, 30))
                self.assertEqual(opened.call_count, 3)
        finally:
            growatt.TOKEN = old_token
            growatt.PUBLISHED_STATES.clear()

    def test_energy_state_is_buffered_between_disk_writes(self):
        energy = {"date": growatt.datetime.now().astimezone().date().isoformat(), "wh": 0.0, "last_power": 0.0, "last_at": None}
        old_save = growatt.LAST_ENERGY_SAVE
        growatt.LAST_ENERGY_SAVE = 0.0
        try:
            with patch.object(growatt.time, "monotonic", side_effect=[100.0, 100.0, 110.0, 170.0, 170.0]), patch.object(growatt.Path, "write_text", return_value=1) as write:
                growatt.update_energy(energy, {"pv_input_power": {"value": 1000}}, 10)
                growatt.update_energy(energy, {"pv_input_power": {"value": 1000}}, 10)
                growatt.update_energy(energy, {"pv_input_power": {"value": 1000}}, 10)
                self.assertEqual(write.call_count, 2)
        finally:
            growatt.LAST_ENERGY_SAVE = old_save

    def test_parse_json_units(self):
        output = 'warning on stderr\n{"ac_output_active_power":{"value":823,"unit":"W"},"Battery Capacity":{"value":76,"unit":"%"}}\n'
        parsed = growatt.parse_mpp_json(output)
        self.assertEqual(parsed["ac_output_active_power"], {"value": 823, "unit": "W"})
        self.assertEqual(parsed["battery_capacity"]["value"], 76)

    def test_parse_plain_json(self):
        parsed = growatt.parse_mpp_json('{"device_mode":"Line","pv_input_power":1200}')
        self.assertEqual(parsed["device_mode"]["value"], "Line")
        self.assertEqual(parsed["pv_input_power"]["unit"], None)

    def test_parse_mppsolar_list_values(self):
        parsed = growatt.parse_mpp_json('{"AC Output Active Power":[823,"W"],"Battery Capacity":[76,"%"]}')
        self.assertEqual(parsed["ac_output_active_power"], {"value": 823, "unit": "W"})
        self.assertEqual(parsed["battery_capacity"], {"value": 76, "unit": "%"})

    def test_parse_rejects_empty_response_diagnostic(self):
        with self.assertRaisesRegex(ValueError, "no valid response"):
            growatt.parse_mpp_json('{"Validity Check":["Error: Response was empty",""]}')

    def test_parse_rejects_nak_error(self):
        with self.assertRaisesRegex(ValueError, "rejected"):
            growatt.parse_mpp_json('{"ERROR":["NAK",""]}')

    def test_parse_rejects_non_json(self):
        with self.assertRaises(ValueError):
            growatt.parse_mpp_json("timeout waiting for response")

    def test_transport_detection_covers_tty_acm_by_id_and_hid(self):
        self.assertEqual(growatt.transport_for("/dev/ttyACM0"), "serial")
        self.assertEqual(growatt.transport_for("/dev/serial/by-id/usb-Growatt"), "serial")
        self.assertEqual(growatt.transport_for("/dev/hidraw0"), "usb")
        self.assertEqual(growatt.transport_for("/dev/hidraw0", "serial"), "serial")

    def test_auto_protocols_cover_spf_variants(self):
        discovered = growatt.protocols("auto")
        self.assertIn("PI30", discovered)
        self.assertIn("PI30MAX", discovered)
        self.assertIn("PI30M044", discovered)
        self.assertIn("PI41", discovered)

    def test_growatt_modbus_v014_decoder(self):
        registers = [0] * 91
        registers[0] = 12
        registers[1] = 1825
        registers[2] = 1760
        registers[4] = 12345
        registers[6] = 2345
        registers[7] = 42
        registers[8] = 11
        registers[10] = 9876
        registers[12] = 11000
        registers[17] = 5234
        registers[18] = 76
        registers[19] = 3850
        registers[20] = 2301
        registers[21] = 6001
        registers[22] = 2298
        registers[23] = 5999
        registers[25] = 431
        registers[26] = 398
        registers[27] = 197
        registers[41] = (1 << 2) | (1 << 14)
        registers[49] = 15
        registers[53] = 5
        registers[78] = 5234
        registers[83] = 127
        readings, mode, warnings = growatt.decode_modbus_input(registers)
        self.assertEqual(mode, "PV charge and discharge")
        self.assertEqual(readings["pv_input_power"]["value"], 1469.0)
        self.assertEqual(readings["battery_voltage"]["value"], 52.34)
        self.assertEqual(readings["ac_output_active_power"]["value"], 987.6)
        self.assertEqual(readings["battery_discharge_current"]["value"], 10.0)
        self.assertTrue(warnings["battery_voltage_low"]["value"])
        self.assertTrue(warnings["bms_communication_error"]["value"])

    def test_raw_modbus_decoder_accepts_echo_before_reply(self):
        registers = list(range(91))
        request_body = bytes((1, 4, 0, 0, 0, 91))
        request = request_body + growatt.modbus_crc(request_body)
        data = b"".join(value.to_bytes(2, "big") for value in registers)
        response_body = bytes((1, 4, len(data))) + data
        response = response_body + growatt.modbus_crc(response_body)
        self.assertEqual(growatt.decode_raw_modbus_response(request + response, 1, 91), registers)

    def test_raw_modbus_decoder_rejects_bad_crc(self):
        with self.assertRaisesRegex(ConnectionError, "no valid direct Modbus reply"):
            growatt.decode_raw_modbus_response(bytes((1, 4, 2, 0, 1, 0, 0)), 1, 1)

    def test_modbus_address_probe_accepts_normal_and_exception_frames(self):
        normal_body = bytes((17, 4, 2, 0, 12))
        normal = normal_body + growatt.modbus_crc(normal_body)
        exception_body = bytes((23, 0x84, 2))
        exception = exception_body + growatt.modbus_crc(exception_body)
        self.assertTrue(growatt._valid_modbus_probe(normal, 17))
        self.assertTrue(growatt._valid_modbus_probe(exception, 23))
        self.assertFalse(growatt._valid_modbus_probe(normal, 18))
        self.assertFalse(growatt._valid_modbus_probe(normal[:-1] + b"\x00", 17))

    def test_raw_qpigs_decoder_ignores_echo_and_noise(self):
        fields = b"230.0 60.0 230.0 60.0 1000 800 20 400 52.0 10 75 40 5 180.0 52.0 2 00000000 00 00 900"
        payload = b"(" + fields
        frame = payload + growatt.pi_crc(payload) + b"\r"
        readings, mode, warnings = growatt.decode_raw_qpigs(b"QPIGS\x00\rgarbage" + frame + b"tail")
        self.assertEqual(readings["ac_output_active_power"]["value"], 800.0)
        self.assertEqual(readings["pv_input_power"]["value"], 900.0)
        self.assertEqual(mode, "Live")
        self.assertEqual(warnings, {})

    def test_raw_qpigs_decoder_reports_non_pi_preview(self):
        with self.assertRaisesRegex(ValueError, "51 50 49 47 53"):
            growatt.decode_raw_qpigs(b"QPIGS\r")

    def test_growatt_modbus_holding_decoder(self):
        registers = [0] * 114
        registers[1] = 2
        registers[2] = 1
        registers[8] = 1
        registers[18] = 2
        registers[19] = 1
        registers[30] = 1
        registers[34] = 80
        registers[35] = 564
        registers[36] = 540
        registers[37] = 480
        registers[38] = 30
        registers[39] = 3
        registers[73] = 207
        registers[82] = 440
        registers[95] = 520
        settings = growatt.decode_modbus_holding(registers)
        self.assertEqual(settings["output_source_priority"]["value"], "Utility first")
        self.assertEqual(settings["battery_type"]["value"], "Lithium")
        self.assertEqual(settings["battery_bulk_charge_voltage"]["value"], 56.4)
        self.assertEqual(settings["modbus_version"]["value"], 2.07)

    def test_raw_pi_crc_and_qpigs_decoder(self):
        payload = b"(230.0 50.0 229.8 50.0 1000 823 18 390 52.40 12 76 43.0 4.2 180.0 53.0 10 00000000 0 0 756"
        frame = payload + growatt.pi_crc(payload) + b"\r"
        readings, mode, warnings = growatt.decode_raw_qpigs(frame)
        self.assertEqual(mode, "Live")
        self.assertEqual(readings["ac_output_active_power"]["value"], 823.0)
        self.assertEqual(readings["battery_voltage"]["value"], 52.4)
        self.assertEqual(readings["pv_input_power"]["value"], 756.0)
        self.assertEqual(warnings, {})

    @patch.object(growatt.Path, "exists", return_value=True)
    @patch.object(growatt, "candidate_devices", return_value=["/dev/serial/by-id/usb-Silicon_Labs_CP2102N"])
    @patch.object(growatt, "run_modbus_read")
    def test_auto_discovery_prefers_modbus_for_cp2102_serial_adapter(self, modbus_read, _candidates, _exists):
        modbus_read.return_value = ({"battery_voltage": {"value": 52.4, "unit": "V"}}, "PV charge", {})
        device, protocol, identity = growatt.discover({"device": "auto", "protocol": "auto", "transport": "auto"})
        self.assertEqual(device, "/dev/serial/by-id/usb-Silicon_Labs_CP2102N")
        self.assertEqual(protocol, growatt.MODBUS_PROTOCOL)
        self.assertEqual(identity["inverter_address"]["value"], 1)
        modbus_read.assert_called_once_with(device, 9600, 1)

    @patch.object(growatt.Path, "exists", return_value=True)
    @patch.object(growatt, "candidate_devices", return_value=["/dev/ttyUSB0"])
    @patch.object(growatt, "run_query")
    def test_discovery_falls_back_to_live_status_when_identity_is_unsupported(self, query, _candidates, _exists):
        query.side_effect = [ValueError("identity inquiry unavailable"), {"battery_voltage": {"value": 52.4, "unit": "V"}}]
        device, protocol, identity = growatt.discover({
            "device": "auto", "protocol": "PI30", "baud_rate": 2400, "transport": "auto",
        })
        self.assertEqual(device, "/dev/ttyUSB0")
        self.assertEqual(protocol, "PI30")
        self.assertEqual(identity["protocol_probe"]["value"], "live status")
        self.assertEqual(query.call_args_list[1].args[3], "QPIGS")

    def test_setting_command_whitelist_and_ranges(self):
        self.assertEqual(growatt.build_setting_command("output_source_priority", "sbu_first"), "POP02")
        self.assertEqual(growatt.build_setting_command("battery_bulk_charge_voltage", "56.4"), "PCVV56.4")
        self.assertEqual(growatt.build_setting_command("max_charging_current", 60), "MCHGC060")
        self.assertEqual(growatt.build_setting_command("buzzer", False), "PDa")
        with self.assertRaises(ValueError):
            growatt.build_setting_command("battery_bulk_charge_voltage", 60)
        with self.assertRaises(ValueError):
            growatt.build_setting_command("battery_bulk_charge_voltage", "nan")
        with self.assertRaises(ValueError):
            growatt.build_setting_command("buzzer", "maybe")
        with self.assertRaises(ValueError):
            growatt.build_setting_command("raw_command", "PF")

    def test_setting_catalog_requires_both_safety_gates(self):
        self.assertFalse(growatt.setting_catalog({"read_only": True, "allow_setting_changes": True})["writable"])
        self.assertFalse(growatt.setting_catalog({"read_only": False, "allow_setting_changes": False})["writable"])
        self.assertTrue(growatt.setting_catalog({"read_only": False, "allow_setting_changes": True})["writable"])

    @patch.object(growatt.glob, "glob")
    @patch.object(growatt.os.path, "realpath")
    def test_candidates_prefer_growatt_by_id_and_exclude_can_and_gps(self, mocked_realpath, mocked_glob):
        values = {
            "/dev/serial/by-id/*": [
                "/dev/serial/by-id/usb-CANable",
                "/dev/serial/by-id/usb-u-blox_GNSS_receiver",
                "/dev/serial/by-id/usb-Silicon_Labs_CP2102N",
            ],
            "/dev/ttyUSB*": ["/dev/ttyUSB0"], "/dev/ttyACM*": ["/dev/ttyACM0"], "/dev/hidraw*": ["/dev/hidraw0"],
        }
        mocked_glob.side_effect = lambda pattern: values.get(pattern, [])
        targets = {
            "/dev/serial/by-id/usb-CANable": "/dev/ttyACM9",
            "/dev/serial/by-id/usb-u-blox_GNSS_receiver": "/dev/ttyACM0",
            "/dev/serial/by-id/usb-Silicon_Labs_CP2102N": "/dev/ttyUSB0",
        }
        mocked_realpath.side_effect = lambda path: targets.get(path, path)
        candidates = growatt.candidate_devices()
        self.assertEqual(candidates[0], "/dev/serial/by-id/usb-Silicon_Labs_CP2102N")
        self.assertNotIn("/dev/serial/by-id/usb-CANable", candidates)
        self.assertNotIn("/dev/serial/by-id/usb-u-blox_GNSS_receiver", candidates)
        self.assertNotIn("/dev/ttyACM0", candidates)
        self.assertNotIn("/dev/ttyUSB0", candidates)

    @patch.object(growatt, "candidate_devices", return_value=[])
    def test_usb_missing_health(self, _candidates):
        health, diagnosis, steps = growatt.classify_health({"connected": False}, {"device": "auto"})
        self.assertEqual(health, "usb_missing")
        self.assertIn("USB", diagnosis)
        self.assertGreaterEqual(len(steps), 3)

    @patch.object(growatt, "candidate_devices", return_value=["/dev/ttyUSB0"])
    def test_no_response_health(self, _candidates):
        health, _, steps = growatt.classify_health({"connected": False}, {"device": "auto"})
        self.assertEqual(health, "no_response")
        self.assertTrue(any("manual" in step for step in steps))

    @patch.object(growatt, "candidate_devices", return_value=["/dev/ttyUSB0"])
    @patch.object(growatt.time, "time", return_value=1000)
    def test_healthy_status(self, _time, _candidates):
        health, _, _ = growatt.classify_health({"connected": True, "last_success_epoch": 990, "warnings": {}}, {"stale_after_seconds": 45, "device": "auto"})
        self.assertEqual(health, "healthy")

    @patch.object(growatt, "candidate_devices", return_value=["/dev/ttyUSB0"])
    @patch.object(growatt.time, "time", return_value=1000)
    def test_string_zero_warning_is_not_active(self, _time, _candidates):
        status = {"connected": True, "last_success_epoch": 990, "warnings": {"overload": {"value": "0"}}}
        health, _, _ = growatt.classify_health(status, {"stale_after_seconds": 45, "device": "auto"})
        self.assertEqual(health, "healthy")

    def test_daily_energy_resets_on_new_date(self):
        with tempfile.TemporaryDirectory() as directory:
            old = growatt.STATE_PATH
            try:
                growatt.STATE_PATH = Path(directory) / "energy.json"
                growatt.STATE_PATH.write_text(json.dumps({"date": "2000-01-01", "wh": 9999}))
                state = growatt.load_energy()
                self.assertEqual(state["wh"], 0.0)
            finally:
                growatt.STATE_PATH = old

    def test_firmware_package_requires_checksum_and_exact_model(self):
        package = b"Growatt official test package" * 20
        digest = hashlib.sha256(package).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            old = growatt.FIRMWARE_DIR
            growatt.FIRMWARE_DIR = Path(directory)
            try:
                with patch.object(growatt, "load_options", return_value={"firmware_tools_enabled": True, "firmware_max_package_mb": 1}):
                    staged = growatt.stage_firmware_package("official.bin", package, digest, "SPF 5000 ES")
                    self.assertEqual(staged["sha256"], digest)
                    self.assertFalse(staged["flash_supported"])
                    self.assertTrue((Path(directory) / staged["stored_filename"]).is_file())
                    with self.assertRaises(ValueError):
                        growatt.stage_firmware_package("official.bin", package, "0" * 64, "SPF 5000 ES")
                    with self.assertRaises(ValueError):
                        growatt.stage_firmware_package("official.bin", package, digest, "SPF 6000 ES")
            finally:
                growatt.FIRMWARE_DIR = old

    def test_firmware_tools_gate_blocks_package_staging(self):
        package = b"x" * 256
        with patch.object(growatt, "load_options", return_value={"firmware_tools_enabled": False}):
            with self.assertRaises(PermissionError):
                growatt.stage_firmware_package("official.bin", package, hashlib.sha256(package).hexdigest(), "SPF 5000 ES")

    def test_official_firmware_url_is_strictly_growatt_https(self):
        self.assertTrue(growatt.is_official_growatt_url("https://en.growatt.com/upload/file/SPF5000.bin"))
        self.assertTrue(growatt.is_official_growatt_url("https://growatt.com/SPF5000.fw"))
        self.assertFalse(growatt.is_official_growatt_url("http://en.growatt.com/file.bin"))
        self.assertFalse(growatt.is_official_growatt_url("https://growatt.com.example.net/file.bin"))
        self.assertFalse(growatt.is_official_growatt_url("https://user@growatt.com/file.bin"))

    def test_firmware_backup_contains_identity_versions_and_settings(self):
        with growatt.STATUS_LOCK:
            old = dict(growatt.STATUS)
            growatt.STATUS.update({"identity": {"serial": "test"}, "firmware": {"cpu1": {"value": "1.0"}}, "settings": {"buzzer": {"value": "on"}}})
        try:
            backup = growatt.firmware_backup()
            self.assertEqual(backup["inverter"]["identity"]["serial"], "test")
            self.assertEqual(backup["inverter"]["firmware"]["cpu1"]["value"], "1.0")
            self.assertIn("settings", backup["inverter"])
        finally:
            with growatt.STATUS_LOCK:
                growatt.STATUS.clear()
                growatt.STATUS.update(old)


if __name__ == "__main__":
    unittest.main()
