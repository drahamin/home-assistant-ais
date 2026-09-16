import unittest
from unittest.mock import Mock

from can_decoder import Reading
from recovery_control import RecoveryAssessment, RecoveryController, assess_recovery


def pack(readings, address, soc, minimum, maximum, spread, limit=50, allowed="on", temperature=23, cell=12):
    prefix = f"battery_{address}_"
    readings.update({
        prefix + "battery_soc": Reading(soc, "%"),
        prefix + "minimum_cell_voltage": Reading(minimum, "V"),
        prefix + "maximum_cell_voltage": Reading(maximum, "V"),
        prefix + "minimum_cell_number": Reading(cell),
        prefix + "cell_voltage_difference": Reading(spread, "mV"),
        prefix + "pack_temperature": Reading(temperature, "°C"),
        prefix + "charge_current_limit": Reading(limit, "A"),
        prefix + "charge_allowed": Reading(allowed),
    })


class RecoveryAssessmentTests(unittest.TestCase):
    def test_imbalanced_bank_is_capped_to_recovery_rate(self):
        readings = {}
        pack(readings, 1, 14, 3.17, 3.18, 10)
        pack(readings, 2, 1, 2.89, 3.20, 310)
        result = assess_recovery(readings, [1, 2], {1, 2})
        self.assertEqual(result.state, "recovery")
        self.assertTrue(result.charge_safe)
        self.assertFalse(result.full_rate_safe)
        self.assertEqual(result.recommended_charge_limit_a, 10.0)
        self.assertEqual(result.weakest_battery, 2)
        self.assertEqual(result.weakest_cell, 12)

    def test_balanced_bank_uses_bms_limits(self):
        readings = {}
        pack(readings, 1, 50, 3.30, 3.32, 20, limit=45)
        pack(readings, 2, 49, 3.30, 3.32, 20, limit=40)
        result = assess_recovery(readings, [1, 2], {1, 2})
        self.assertEqual(result.state, "normal")
        self.assertTrue(result.full_rate_safe)
        self.assertEqual(result.recommended_charge_limit_a, 85.0)

    def test_bms_charge_block_is_never_overridden(self):
        readings = {}
        pack(readings, 1, 50, 3.30, 3.32, 20)
        pack(readings, 2, 49, 3.30, 3.32, 20, allowed="off")
        result = assess_recovery(readings, [1, 2], {1, 2})
        self.assertEqual(result.state, "charge_blocked")
        self.assertFalse(result.charge_safe)
        self.assertEqual(result.recommended_charge_limit_a, 0.0)

    def test_full_battery_charge_block_is_reported_as_normal_tapering(self):
        readings = {}
        pack(readings, 1, 100, 3.37, 3.42, 50, allowed="on")
        pack(readings, 2, 100, 3.32, 3.42, 100, limit=0, allowed="off")
        result = assess_recovery(readings, [1, 2], {1, 2})
        self.assertEqual(result.state, "charge_complete")
        self.assertIn("full", result.summary)
        self.assertIn("normal", result.action.lower())
        self.assertFalse(result.charge_safe)
        self.assertEqual(result.recommended_charge_limit_a, 0.0)

    def test_full_battery_with_unsafe_cell_voltage_remains_blocked(self):
        readings = {}
        pack(readings, 1, 100, 3.40, 3.56, 160, allowed="on")
        pack(readings, 2, 100, 3.40, 3.56, 160, limit=0, allowed="off")
        result = assess_recovery(readings, [1, 2], {1, 2})
        self.assertEqual(result.state, "charge_blocked")

    def test_missing_pack_locks_control(self):
        result = assess_recovery({}, [1, 2], {1})
        self.assertEqual(result.state, "monitoring_unavailable")
        self.assertFalse(result.charge_safe)


class RecoveryControllerTests(unittest.TestCase):
    def controller(self):
        controller = RecoveryController({"recovery_control_enabled": True}, "token", Mock())
        controller.client = Mock()
        return controller

    def assessment(self, *, safe=True, full=False):
        return RecoveryAssessment("normal", "summary", "action", safe, full, 10, 2, 12)

    def test_never_closes_switch_without_generator_voltage(self):
        controller = self.controller()
        controller.client.state.side_effect = [{"state": "off"}, {"state": "0"}]
        controller.update(self.assessment(), 5)
        controller.client.switch.assert_not_called()

    def test_low_soc_closes_verified_generator_input(self):
        controller = self.controller()
        controller.client.state.side_effect = [{"state": "off"}, {"state": "230"}]
        controller.update(self.assessment(), 5)
        controller.client.switch.assert_called_once_with(controller.switch_entity, True)

    def test_unsafe_bms_state_opens_generator_input(self):
        controller = self.controller()
        controller.client.state.side_effect = [{"state": "on"}, {"state": "230"}]
        controller.update(self.assessment(safe=False), 5)
        controller.client.switch.assert_called_once_with(controller.switch_entity, False)

    def test_high_soc_does_not_stop_until_bank_is_balanced(self):
        controller = self.controller()
        controller.client.state.side_effect = [{"state": "on"}, {"state": "230"}]
        controller.update(self.assessment(full=False), 95)
        controller.client.switch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
