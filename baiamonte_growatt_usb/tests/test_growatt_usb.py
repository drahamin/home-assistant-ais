import importlib.util
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "growatt_usb.py"
SPEC = importlib.util.spec_from_file_location("growatt_usb", MODULE_PATH)
growatt = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(growatt)


class GrowattUsbTests(unittest.TestCase):
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
        self.assertIn("/dev/ttyUSB0", candidates)

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
