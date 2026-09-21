import unittest

from baiamonte_power_manager.engine import (
    LearningState,
    Policy,
    Snapshot,
    decide,
    protected_overlap,
)
from baiamonte_power_manager.power_manager import configure


def sample(soc=70, power=700, load=700, energy=None, online=True, solar=0):
    return Snapshot(1_700_000_000, soc, power, load, energy, solar, online, True, True)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()
        self.learning = LearningState(learned_load_w=700, learned_discharge_w=650, samples=100)

    def test_healthy_battery_keeps_all_loads(self):
        snapshot = Snapshot(**{**sample(soc=90, energy=15.36).__dict__, "nominal_energy_kwh": 15.36})
        decision = decide(snapshot, self.learning, self.policy)
        self.assertEqual(decision.mode, "normal")
        self.assertEqual(decision.shed_tiers, ())

    def test_low_runtime_sheds_before_soc_threshold(self):
        decision = decide(sample(soc=60, load=3000, energy=2.0), self.learning, self.policy)
        self.assertEqual(decision.mode, "emergency")
        self.assertEqual(decision.shed_tiers, (0, 1, 2, 3))

    def test_low_soc_escalates_in_stages(self):
        self.assertEqual(decide(sample(soc=65, energy=10.24), self.learning, self.policy).mode, "economy")
        self.assertEqual(decide(sample(soc=55, energy=10.24), self.learning, self.policy).mode, "conserve")
        self.assertEqual(decide(sample(soc=42, energy=10.24), self.learning, self.policy).mode, "protect")
        self.assertEqual(decide(sample(soc=34, energy=10.24), self.learning, self.policy).mode, "emergency")

    def test_missing_battery_telemetry_freezes_switching(self):
        decision = decide(sample(soc=None, online=None), self.learning, self.policy)
        self.assertEqual(decision.mode, "telemetry_lost")
        self.assertFalse(decision.telemetry_ok)
        self.assertEqual(decision.shed_tiers, ())

    def test_restore_requires_healthy_soc_and_incoming_power(self):
        charging = Snapshot(**{**sample(soc=85, power=-500, load=500, energy=15.36).__dict__, "nominal_energy_kwh": 15.36})
        discharging = Snapshot(**{**sample(soc=85, power=500, load=500, energy=15.36).__dict__, "nominal_energy_kwh": 15.36})
        self.assertTrue(decide(charging, self.learning, self.policy).can_restore)
        self.assertFalse(decide(discharging, self.learning, self.policy).can_restore)

    def test_learning_is_conservative_about_runtime(self):
        decision = decide(sample(soc=70, load=200, energy=4), self.learning, self.policy)
        self.assertAlmostEqual(decision.expected_load_w, 700)

    def test_protected_load_overlap_is_detected(self):
        overlap = protected_overlap(["switch.cameras", "switch.lte"], [["switch.dishwasher"], ["switch.cameras"]])
        self.assertEqual(overlap, {"switch.cameras"})

    def test_fixed_critical_loads_cannot_be_configured_for_shedding(self):
        with self.assertRaisesRegex(ValueError, "Protected entities"):
            configure({"shed_tier_1": "switch.wifi_din_rail_10a_cameras_switch"})

    def test_lte_is_editable_and_defaults_to_first_category(self):
        _policy, tiers, protected = configure({"shed_first": "switch.wifi_din_rail_10a_nokia_lte_switch"})
        self.assertEqual(tiers[0], ["switch.wifi_din_rail_10a_nokia_lte_switch"])
        self.assertNotIn("switch.wifi_din_rail_10a_nokia_lte_switch", protected)

    def test_reported_bank_capacity_controls_reserve(self):
        two_pack = sample(soc=70, load=700, energy=7)
        three_pack = Snapshot(**{**two_pack.__dict__, "nominal_energy_kwh": 15.36})
        two_runtime = decide(two_pack, self.learning, self.policy).runtime_hours
        three_runtime = decide(three_pack, self.learning, self.policy).runtime_hours
        self.assertGreater(two_runtime, three_runtime)


if __name__ == "__main__":
    unittest.main()
