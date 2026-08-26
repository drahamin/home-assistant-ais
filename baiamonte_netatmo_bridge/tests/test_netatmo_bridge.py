import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "netatmo_bridge.py"
spec = importlib.util.spec_from_file_location("netatmo_bridge", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


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


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.bridge = module.Bridge(self.client)
        self.bridge.options.update({"auto_pair": True, "prefer_local": True, "cloud_fallback": True})

    def test_build_routes_pairs_same_friendly_name(self):
        states = [
            {"entity_id": "switch.dishwasher_local", "state": "off", "attributes": {"friendly_name": "Dishwasher Outlet Local"}},
            {"entity_id": "switch.dishwasher", "state": "on", "attributes": {"friendly_name": "Dishwasher Outlet Netatmo"}},
        ]
        registry = [
            {"entity_id": "switch.dishwasher_local", "platform": "homekit_controller"},
            {"entity_id": "switch.dishwasher", "platform": "netatmo"},
        ]
        routes = self.bridge.build_routes(states, registry)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].active_path, "local")
        self.assertEqual(routes[0].cloud_entity, "switch.dishwasher")

    def test_cloud_used_when_local_unavailable(self):
        self.bridge.status["routes"] = [{
            "name": "Cistern Outlet", "domain": "switch", "local_entity": "switch.cistern_local",
            "cloud_entity": "switch.cistern", "local_available": False, "cloud_available": True,
        }]
        result = self.bridge.control("Cistern Outlet", "turn_on", {})
        self.assertEqual(result["path"], "cloud")
        self.assertEqual(self.client.calls[0][2], "switch.cistern")

    def test_cloud_route_is_active_when_cloud_is_preferred(self):
        self.bridge.options["prefer_local"] = False
        states = [
            {"entity_id": "switch.light_local", "state": "off", "attributes": {"friendly_name": "Light Local"}},
            {"entity_id": "switch.light", "state": "on", "attributes": {"friendly_name": "Light Netatmo"}},
        ]
        registry = [
            {"entity_id": "switch.light_local", "platform": "matter"},
            {"entity_id": "switch.light", "platform": "netatmo"},
        ]
        route = self.bridge.build_routes(states, registry)[0]
        self.assertEqual(route.active_path, "cloud")
        self.assertEqual(route.state, "on")

    def test_cloud_retried_after_local_failure(self):
        self.bridge.status["routes"] = [{
            "name": "Battery Input", "domain": "switch", "local_entity": "switch.battery_local",
            "cloud_entity": "switch.battery", "local_available": True, "cloud_available": True,
        }]
        self.client.fail.add("switch.battery_local")
        result = self.bridge.control("Battery Input", "turn_off", {})
        self.assertEqual(result["path"], "cloud")
        self.assertEqual(len(self.client.calls), 2)

    def test_unmanaged_entity_is_rejected(self):
        with self.assertRaises(KeyError):
            self.bridge.control("switch.not_managed", "turn_on", {})


if __name__ == "__main__":
    unittest.main()
