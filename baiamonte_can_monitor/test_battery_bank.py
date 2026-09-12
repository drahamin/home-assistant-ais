import unittest

from battery_bank import derive_bank_readings
from can_decoder import Reading


class BatteryBankTests(unittest.TestCase):
    def setUp(self):
        self.readings = {}
        for address, voltage, current, soc, spread in (
            (1, 51.90, 2.5, 29, 3),
            (2, 51.88, 2.6, 7, 60),
        ):
            prefix = f"battery_{address}_"
            self.readings.update({
                prefix + "battery_voltage": Reading(voltage, "V"),
                prefix + "battery_current": Reading(current, "A"),
                prefix + "battery_power": Reading(round(voltage * current, 1), "W"),
                prefix + "battery_soc": Reading(soc, "%"),
                prefix + "cell_voltage_difference": Reading(spread, "mV"),
            })

    def test_equal_parallel_pack_totals(self):
        result = derive_bank_readings(self.readings, [1, 2], {1, 2})
        self.assertEqual(result["bank_voltage"].value, 51.89)
        self.assertEqual(result["bank_current"].value, 5.1)
        self.assertEqual(result["bank_soc"].value, 18.0)
        self.assertEqual(result["bank_nominal_energy"].value, 10.24)
        self.assertEqual(result["bank_remaining_energy"].value, 1.843)
        self.assertEqual(result["bank_soc_difference"].value, 22.0)
        self.assertEqual(result["bank_maximum_cell_spread"].value, 60.0)
        self.assertEqual(result["bank_health"].value, "attention")
        self.assertEqual(result["battery_soc"].value, 18.0)

    def test_offline_pack_is_reported_without_inventing_its_data(self):
        result = derive_bank_readings(self.readings, [1, 2], {1})
        self.assertEqual(result["battery_1_online"].value, "on")
        self.assertEqual(result["battery_2_online"].value, "off")
        self.assertEqual(result["bank_online_batteries"].value, 1)
        self.assertEqual(result["bank_health"].value, "attention")


if __name__ == "__main__":
    unittest.main()
