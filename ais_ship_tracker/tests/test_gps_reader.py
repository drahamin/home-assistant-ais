import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

GPS_READER = Path(__file__).parents[1] / "gps_reader.py"
SPEC = importlib.util.spec_from_file_location("baiamonte_ais_gps", GPS_READER)
gps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gps)


class GPSReaderTests(unittest.TestCase):
    def test_parses_valid_gga_position(self):
        body = "GPGGA,123519,3745.000,N,01506.000,E,1,08,0.9,959.0,M,46.9,M,,"
        checksum = 0
        for character in body:
            checksum ^= ord(character)
        fix = gps.parse_sentence("$%s*%02X" % (body, checksum))
        self.assertAlmostEqual(fix["lat"], 37.75)
        self.assertAlmostEqual(fix["lon"], 15.10)
        self.assertEqual(fix["alt"], 959.0)

    def test_rejects_bad_checksum(self):
        self.assertIsNone(gps.parse_sentence("$GPRMC,1,A,3745.0,N,01506.0,E,0,0,010101,,,A*00"))

    @patch.object(gps.glob, "glob")
    @patch.object(gps.os.path, "realpath")
    def test_auto_prefers_gps_by_id_and_skips_unrelated_serial_adapters(self, realpath, glob_paths):
        glob_paths.side_effect = lambda pattern: {
            "/dev/serial/by-id/*": [
                "/dev/serial/by-id/usb-u-blox_GNSS_receiver",
                "/dev/serial/by-id/usb-Silicon_Labs_CP2102N",
            ],
            "/dev/ttyACM*": ["/dev/ttyACM0"],
            "/dev/ttyUSB*": ["/dev/ttyUSB0"],
        }.get(pattern, [])
        realpath.side_effect = lambda path: path
        self.assertEqual(gps.candidates("auto"), ["/dev/serial/by-id/usb-u-blox_GNSS_receiver"])

    @patch.object(gps.glob, "glob")
    @patch.object(gps.os.path, "realpath")
    def test_auto_deduplicates_by_id_and_tty_aliases(self, realpath, glob_paths):
        glob_paths.side_effect = lambda pattern: {
            "/dev/serial/by-id/*": ["/dev/serial/by-id/usb-serial-radio"],
            "/dev/ttyACM*": [],
            "/dev/ttyUSB*": ["/dev/ttyUSB0"],
        }.get(pattern, [])
        realpath.return_value = "/dev/ttyUSB0"
        self.assertEqual(gps.candidates("auto"), ["/dev/serial/by-id/usb-serial-radio"])


if __name__ == "__main__":
    unittest.main()
