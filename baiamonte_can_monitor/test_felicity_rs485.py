import unittest
from unittest.mock import Mock, patch

from felicity_rs485 import (
    FelicityRs485Receiver,
    decode_battery_information,
    decode_cell_information,
    modbus_crc,
    parse_read_response,
    read_request,
)


class FelicityProtocolTests(unittest.TestCase):
    def test_known_pack_information_request(self):
        self.assertEqual(read_request(1, 0x1302, 10).hex(), "01031302000a6089")

    def test_parser_skips_noise_and_validates_crc(self):
        data = bytes(range(20))
        payload = bytes((1, 3, len(data))) + data
        frame = payload + modbus_crc(payload).to_bytes(2, "little")
        self.assertEqual(parse_read_response(b"\xfe\xff" + frame, 1, 20), (frame, data))
        self.assertIsNone(parse_read_response(frame[:-1] + b"\x00", 1, 20))

    def test_pack_decoder(self):
        data = bytearray(20)
        data[8:10] = (5234).to_bytes(2, "big")
        data[10:12] = (-123).to_bytes(2, "big", signed=True)
        data[18:20] = (81).to_bytes(2, "big")
        readings = decode_battery_information(bytes(data))
        self.assertEqual(readings["battery_voltage"].value, 52.34)
        self.assertEqual(readings["battery_current"].value, -12.3)
        self.assertEqual(readings["battery_soc"].value, 81)
        self.assertEqual(readings["battery_status"].value, "discharging")

    def test_cell_decoder(self):
        data = bytearray()
        for value in range(3300, 3316):
            data.extend(value.to_bytes(2, "big"))
        for value in (22, 23, 24, 25):
            data.extend(value.to_bytes(2, "big", signed=True))
        readings = decode_cell_information(bytes(data))
        self.assertEqual(readings["cell_1_voltage"].value, 3.3)
        self.assertEqual(readings["cell_16_voltage"].value, 3.315)
        self.assertEqual(readings["temperature_4"].value, 25)

    @patch("felicity_rs485.serial.Serial")
    def test_receiver_never_issues_write_function(self, serial_class):
        serial_class.return_value = Mock()
        receiver = FelicityRs485Receiver("/dev/test", 9600, [1, 2])
        for address, register, count, _label in receiver._polls:
            self.assertEqual(read_request(address, register, count)[1], 0x03)


if __name__ == "__main__":
    unittest.main()
