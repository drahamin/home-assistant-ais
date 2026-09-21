import unittest

from battery_bank import derive_bank_readings
from can_decoder import Reading


class BatteryBankTests(unittest.TestCase):
    def setUp(self):
        self.readings = {}
        for address, voltage, current, soc, spread in (
            (1, 51.90, -2.5, 29, 3),
            (2, 51.88, -2.6, 7, 60),
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
        self.assertEqual(result["bank_current"].value, -5.1)
        self.assertEqual(result["bank_soc"].value, 18.0)
        self.assertEqual(result["bank_nominal_energy"].value, 10.24)
        self.assertEqual(result["bank_remaining_energy"].value, 1.843)
        self.assertEqual(result["bank_soc_difference"].value, 22.0)
        self.assertEqual(result["bank_maximum_cell_spread"].value, 60.0)
        self.assertEqual(result["bank_charging_power"].value, 264.7)
        self.assertEqual(result["bank_discharging_power"].value, 0.0)
        self.assertEqual(result["bank_time_to_empty_current_load"].value, "unavailable")
        self.assertEqual(result["bank_time_to_full_current_input"].value, 31.7)
        self.assertEqual(result["battery_1_charging_power"].value, 129.8)
        self.assertEqual(result["battery_2_charging_power"].value, 134.9)
        self.assertEqual(result["bank_remaining_energy"].device_class, "energy")
        self.assertEqual(result["bank_remaining_energy"].state_class, "measurement")
        self.assertEqual(result["bank_health"].value, "attention")
        self.assertEqual(result["bank_charging"].value, "on")
        self.assertEqual(result["bank_discharging"].value, "off")
        self.assertEqual(result["bank_flow_direction"].value, "charging")
        self.assertEqual(result["bank_power_magnitude"].value, 264.7)
        self.assertEqual(result["bank_current_magnitude"].value, 5.1)
        self.assertIn("CHARGING", result["bank_charge_verdict"].value)
        self.assertIn("entering batteries", result["bank_charge_verdict"].value)
        self.assertEqual(result["battery_soc"].value, 18.0)

    def test_offline_pack_is_reported_without_inventing_its_data(self):
        result = derive_bank_readings(self.readings, [1, 2], {1})
        self.assertEqual(result["battery_1_online"].value, "on")
        self.assertEqual(result["battery_2_online"].value, "off")
        self.assertEqual(result["bank_online_batteries"].value, 1)
        self.assertEqual(result["bank_health"].value, "attention")

    def test_standby_pack_is_visible_but_excluded_until_activated(self):
        self.readings["battery_2_battery_soc"] = Reading(29, "%")
        self.readings["battery_2_cell_voltage_difference"] = Reading(3, "mV")
        result = derive_bank_readings(self.readings, [1, 2], {1, 2}, [1, 2, 3])

        self.assertEqual(result["bank_configured_batteries"].value, 2)
        self.assertEqual(result["bank_provisioned_batteries"].value, 3)
        self.assertEqual(result["bank_nominal_energy"].value, 10.24)
        self.assertEqual(result["bank_health"].value, "healthy")
        self.assertEqual(result["battery_3_online"].value, "off")
        self.assertEqual(result["battery_3_provisioning_status"].value, "awaiting_connection")
        self.assertEqual(result["battery_3_battery_soc"].value, "unavailable")
        self.assertEqual(result["battery_3_cell_16_voltage"].value, "unavailable")

    def test_positive_felicity_power_is_reported_as_discharge(self):
        self.readings["battery_1_battery_power"] = Reading(100.0, "W")
        self.readings["battery_1_battery_current"] = Reading(2.0, "A")
        self.readings["battery_2_battery_power"] = Reading(150.0, "W")
        self.readings["battery_2_battery_current"] = Reading(3.0, "A")
        result = derive_bank_readings(self.readings, [1, 2], {1, 2})
        self.assertEqual(result["bank_charging_power"].value, 0.0)
        self.assertEqual(result["bank_discharging_power"].value, 250.0)
        self.assertEqual(result["bank_time_to_empty_current_load"].value, 7.4)
        self.assertEqual(result["bank_time_to_full_current_input"].value, "unavailable")
        self.assertEqual(result["battery_1_discharging_power"].value, 100.0)
        self.assertEqual(result["battery_2_discharging_power"].value, 150.0)
        self.assertEqual(result["bank_charging"].value, "off")
        self.assertEqual(result["bank_discharging"].value, "on")
        self.assertEqual(result["bank_flow_direction"].value, "discharging")
        self.assertIn("DISCHARGING", result["bank_charge_verdict"].value)
        self.assertIn("supplying loads", result["bank_charge_verdict"].value)
        self.assertIn("heavy loads", result["bank_operating_recommendation"].value)

    def test_full_bank_keeps_charge_complete_message_during_small_load(self):
        for address in (1, 2):
            prefix = f"battery_{address}_"
            self.readings[prefix + "battery_soc"] = Reading(100, "%")
            self.readings[prefix + "battery_current"] = Reading(0.8, "A")
            self.readings[prefix + "battery_power"] = Reading(41.5, "W")

        result = derive_bank_readings(self.readings, [1, 2], {1, 2})

        self.assertEqual(result["bank_flow_direction"].value, "discharging")
        self.assertIn("Charge complete", result["bank_operating_recommendation"].value)
        self.assertIn("Do not force more current", result["bank_operating_recommendation"].value)


if __name__ == "__main__":
    unittest.main()
