import unittest

from can_decoder import decode_frame


class DecoderTests(unittest.TestCase):
    def test_system_measurements(self):
        values = decode_frame(0x313, bytes.fromhex("1400 FF9C 00FA 645F"))
        self.assertEqual(values["battery_voltage"].value, 51.2)
        self.assertEqual(values["battery_current"].value, -10.0)
        self.assertEqual(values["battery_power"].value, -512.0)
        self.assertEqual(values["maximum_cell_temperature"].value, 25.0)
        self.assertEqual(values["battery_soc"].value, 100)
        self.assertEqual(values["battery_soh"].value, 95)

    def test_cells(self):
        values = decode_frame(0x315, bytes.fromhex("0D0C 0D0D 0D0E 0D0F"))
        self.assertEqual(values["cell_1_voltage"].value, 3.34)
        self.assertEqual(values["cell_4_voltage"].value, 3.343)

    def test_short_frame_is_ignored(self):
        self.assertEqual(decode_frame(0x313, b"\x00"), {})


if __name__ == "__main__":
    unittest.main()
