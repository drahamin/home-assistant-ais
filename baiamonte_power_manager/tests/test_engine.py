import unittest

from baiamonte_power_manager.engine import (
    LearningState,
    Policy,
    Snapshot,
    decide,
    protected_overlap,
)
from baiamonte_power_manager.power_manager import configure, validate_ui_options


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
        economy = Snapshot(**{**sample(soc=65, energy=15.36).__dict__, "nominal_energy_kwh": 15.36})
        self.assertEqual(decide(economy, self.learning, self.policy).mode, "economy")
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
        self.assertAlmostEqual(decision.expected_load_w, 805)

    def test_solar_credit_is_weather_discounted_and_capped(self):
        sunny = Snapshot(**{
            **sample(soc=75, load=700, energy=5).__dict__,
            "sun_above_horizon": True,
            "solar_remaining_today_kwh": 20,
            "weather_condition": "sunny",
        })
        rainy = Snapshot(**{**sunny.__dict__, "weather_condition": "pouring"})
        sunny_decision = decide(sunny, self.learning, self.policy)
        rainy_decision = decide(rainy, self.learning, self.policy)
        self.assertEqual(sunny_decision.solar_credit_kwh, 2)
        self.assertLess(rainy_decision.solar_credit_kwh, sunny_decision.solar_credit_kwh)
        self.assertGreater(sunny_decision.runtime_hours, sunny_decision.battery_only_runtime_hours)

    def test_nighttime_forecast_is_not_credited_before_sunrise(self):
        night = Snapshot(**{
            **sample(soc=80, load=700, energy=6).__dict__,
            "sun_above_horizon": False,
            "hours_to_sunrise": 12,
            "solar_tomorrow_kwh": 20,
            "weather_condition": "clear-night",
        })
        decision = decide(night, self.learning, self.policy)
        self.assertEqual(decision.solar_credit_kwh, 0)
        self.assertEqual(decision.mode, "emergency")
        self.assertIn("sunrise", decision.reason)

    def test_monthly_history_influences_planning_load(self):
        learning = LearningState(learned_load_w=500)
        month = __import__("datetime").datetime.fromtimestamp(1_700_000_000).month - 1
        learning.monthly_load_w[month] = 1200
        decision = decide(sample(soc=90, load=200, energy=10), learning, self.policy)
        self.assertAlmostEqual(decision.expected_load_w, 1380)

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

    def test_web_ui_requires_explicit_automatic_confirmation(self):
        with self.assertRaisesRegex(ValueError, "ENABLE AUTOMATIC"):
            validate_ui_options({"control_mode": "automatic"}, {"control_mode": "observe"})
        clean = validate_ui_options(
            {"control_mode": "automatic", "automatic_confirmation": "ENABLE AUTOMATIC"},
            {"control_mode": "observe"},
        )
        self.assertEqual(clean["control_mode"], "automatic")

    def test_web_ui_validates_category_overlap(self):
        with self.assertRaisesRegex(ValueError, "only one shedding tier"):
            validate_ui_options(
                {"shed_first": "switch.lte", "shed_tier_1": "switch.lte"},
                {"control_mode": "observe"},
            )


if __name__ == "__main__":
    unittest.main()
