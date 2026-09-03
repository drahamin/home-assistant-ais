import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tuya_bridge.py"
SPEC = importlib.util.spec_from_file_location("tuya_bridge", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def state(entity_id, value="off", name=None):
    return {
        "entity_id": entity_id,
        "state": value,
        "attributes": {"friendly_name": name or entity_id},
    }


def registry(entity_id, platform):
    return {"entity_id": entity_id, "platform": platform}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = set()

    def call_service(self, domain, service, entity_id, data):
        self.calls.append((domain, service, entity_id, data))
        if entity_id in self.fail:
            raise OSError("route down")

    def publish(self, *_args, **_kwargs):
        pass


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.bridge = MODULE.Bridge(self.client)
        self.bridge.options.update({
            "prefer_local": True,
            "cloud_fallback": True,
            "controls_enabled": True,
            "auto_pair": True,
            "discover_local_platforms": "tuya_local,localtuya",
            "cloud_platforms": "tuya",
            "excluded_name_terms": [],
            "managed_entities": [],
            "device_pairs": [],
        })

    def test_pairs_local_and_cloud_by_domain_and_normalized_name(self):
        states = [
            state("switch.courtyard_local", "off", "Courtyard Switch Local Tuya"),
            state("switch.courtyard", "on", "Courtyard Switch Tuya Cloud"),
        ]
        registry_rows = [
            registry("switch.courtyard_local", "tuya_local"),
            registry("switch.courtyard", "tuya"),
        ]
        routes = self.bridge.build_routes(states, registry_rows)
        self.assertEqual(1, len(routes))
        self.assertEqual("local", routes[0].active_path)
        self.assertEqual("switch.courtyard", routes[0].cloud_entity)

    def test_pairs_tuya_local_and_cloud_feature_aliases(self):
        states = [
            state("switch.bluetti_main_breaker", "unavailable", "Bluetti Main Breaker"),
            state("switch.bluetti_main_breaker_switch", "on", "Bluetti Main Breaker Switch"),
            state("sensor.bluetti_main_breaker_current", "unavailable", "Bluetti Main Breaker Current"),
            state("sensor.bluetti_main_breaker_phase_a_current", "2.1", "Bluetti Main Breaker Phase A current"),
            state("sensor.bluetti_main_breaker_energy", "unavailable", "Bluetti Main Breaker Energy"),
            state("sensor.bluetti_main_breaker_total_energy", "1816", "Bluetti Main Breaker Total energy"),
        ]
        registry_rows = [
            registry("switch.bluetti_main_breaker", "tuya_local"),
            registry("switch.bluetti_main_breaker_switch", "tuya"),
            registry("sensor.bluetti_main_breaker_current", "tuya_local"),
            registry("sensor.bluetti_main_breaker_phase_a_current", "tuya"),
            registry("sensor.bluetti_main_breaker_energy", "tuya_local"),
            registry("sensor.bluetti_main_breaker_total_energy", "tuya"),
        ]

        routes = self.bridge.build_routes(states, registry_rows)

        self.assertEqual(3, len(routes))
        self.assertTrue(all(route.active_path == "cloud" for route in routes))
        self.assertTrue(all(route.local_entity and route.cloud_entity for route in routes))

    def test_does_not_auto_import_all_matter_or_zha_entities(self):
        states = [
            state("light.unrelated_matter", name="Unrelated Matter Light"),
            state("sensor.unrelated_zigbee", name="Unrelated Zigbee Sensor"),
        ]
        registry_rows = [
            registry("light.unrelated_matter", "matter"),
            registry("sensor.unrelated_zigbee", "zha"),
        ]
        self.assertEqual([], self.bridge.build_routes(states, registry_rows))

    def test_excludes_devices_outside_the_estate_by_name(self):
        self.bridge.options["excluded_name_terms"] = ["Miami", "Office Blinds"]
        states = [
            state("binary_sensor.miami_gateway", name="Miami Multimode gateway Problem"),
            state("cover.office_blinds_left", name="Office Blinds Left"),
            state("switch.baiamonte_lights", name="Baiamonte Lights"),
        ]
        registry_rows = [
            registry("binary_sensor.miami_gateway", "tuya"),
            registry("cover.office_blinds_left", "tuya"),
            registry("switch.baiamonte_lights", "tuya"),
        ]
        routes = self.bridge.build_routes(states, registry_rows)
        self.assertEqual(["Baiamonte Lights"], [route.name for route in routes])

    def test_manual_pair_can_intentionally_override_name_exclusion(self):
        self.bridge.options["excluded_name_terms"] = ["Miami"]
        self.bridge.options["device_pairs"] = ["Approved remote|switch.miami_local|switch.miami_cloud"]
        states = [
            state("switch.miami_local", name="Miami Pump Local"),
            state("switch.miami_cloud", name="Miami Pump Cloud"),
        ]
        registry_rows = [
            registry("switch.miami_local", "tuya_local"),
            registry("switch.miami_cloud", "tuya"),
        ]
        routes = self.bridge.build_routes(states, registry_rows)
        self.assertEqual(["Approved remote"], [route.name for route in routes])

    def test_explicitly_enrolls_a_matter_entity_as_local(self):
        self.bridge.options["managed_entities"] = ["light.wine_room"]
        routes = self.bridge.build_routes(
            [state("light.wine_room", "on", "Wine Room")],
            [registry("light.wine_room", "matter")],
        )
        self.assertEqual(1, len(routes))
        self.assertEqual("local", routes[0].active_path)
        self.assertEqual("matter", routes[0].local_platform)

    def test_manual_pair_takes_priority_over_automatic_discovery(self):
        self.bridge.options["device_pairs"] = [
            "Pool Pump|switch.pool_local|switch.pool_cloud"
        ]
        states = [
            state("switch.pool_local", name="Pool Pump Local"),
            state("switch.pool_cloud", name="Pool Pump Cloud"),
        ]
        registry_rows = [
            registry("switch.pool_local", "tuya_local"),
            registry("switch.pool_cloud", "tuya"),
        ]
        routes = self.bridge.build_routes(states, registry_rows)
        self.assertEqual(1, len(routes))
        self.assertEqual("Pool Pump", routes[0].name)

    def test_rejects_cross_domain_manual_pair(self):
        self.bridge.options["device_pairs"] = [
            "Bad Pair|switch.pool_local|light.pool_cloud"
        ]
        routes = self.bridge.build_routes(
            [state("switch.pool_local"), state("light.pool_cloud")],
            [registry("switch.pool_local", "tuya_local"), registry("light.pool_cloud", "tuya")],
        )
        self.assertEqual(2, len(routes))
        self.assertFalse(any(route.local_entity and route.cloud_entity for route in routes))

    def test_rejects_cloud_entity_from_additional_local_entities(self):
        self.bridge.options["managed_entities"] = ["switch.cloud_only"]
        routes = self.bridge.build_routes(
            [state("switch.cloud_only", name="Cloud Only")],
            [registry("switch.cloud_only", "tuya")],
        )
        self.assertEqual(1, len(routes))
        self.assertIsNone(routes[0].local_entity)
        self.assertEqual("switch.cloud_only", routes[0].cloud_entity)

    def configure_control_route(self):
        self.bridge.status["routes"] = [{
            "name": "Courtyard",
            "domain": "switch",
            "local_entity": "switch.courtyard_local",
            "cloud_entity": "switch.courtyard_cloud",
            "local_available": True,
            "cloud_available": True,
        }]

    def test_control_prefers_local(self):
        self.configure_control_route()
        result = self.bridge.control("Courtyard", "turn_on", {})
        self.assertEqual("local", result["path"])
        self.assertEqual("switch.courtyard_local", self.client.calls[0][2])

    def test_control_retries_cloud_after_local_failure(self):
        self.configure_control_route()
        self.client.fail.add("switch.courtyard_local")
        result = self.bridge.control("Courtyard", "turn_off", {})
        self.assertEqual("cloud", result["path"])
        self.assertEqual(2, len(self.client.calls))

    def test_rejects_arbitrary_service(self):
        self.configure_control_route()
        with self.assertRaises(ValueError):
            self.bridge.control("Courtyard", "reload_config_entry", {})
        self.assertEqual([], self.client.calls)

    def test_rejects_broader_target_data(self):
        self.configure_control_route()
        with self.assertRaises(ValueError):
            self.bridge.control("Courtyard", "turn_on", {"area_id": "all"})
        self.assertEqual([], self.client.calls)

    def test_rejects_controls_when_disabled(self):
        self.configure_control_route()
        self.bridge.options["controls_enabled"] = False
        with self.assertRaises(PermissionError):
            self.bridge.control("Courtyard", "turn_on", {})

    def test_route_payload_does_not_copy_raw_attributes(self):
        states = [{
            "entity_id": "switch.cellar",
            "state": "on",
            "attributes": {"friendly_name": "Cellar", "local_key": "must-not-leak"},
        }]
        route = self.bridge.build_routes(states, [registry("switch.cellar", "tuya_local")])[0]
        payload = MODULE.asdict(route)
        self.assertNotIn("attributes", payload)
        self.assertNotIn("must-not-leak", str(payload))


if __name__ == "__main__":
    unittest.main()
