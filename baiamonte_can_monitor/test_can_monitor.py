import unittest

import sys
from types import SimpleNamespace


class FakeMessage:
    def __init__(self, **values):
        self.__dict__.update(values)


sys.modules.setdefault("can", SimpleNamespace(Message=FakeMessage))

from can_monitor import GROWATT_HEARTBEAT_DATA, GROWATT_HEARTBEAT_ID, growatt_heartbeat, uses_listen_only


class BusModeTests(unittest.TestCase):
    def test_default_is_hardware_listen_only(self):
        self.assertTrue(uses_listen_only({}))

    def test_explicit_passive_mode_is_hardware_listen_only(self):
        self.assertTrue(uses_listen_only({"bus_mode": "listen_only"}))

    def test_standalone_mode_acknowledges_frames(self):
        self.assertFalse(uses_listen_only({"bus_mode": "standalone_ack"}))

    def test_growatt_heartbeat_is_fixed_and_standard(self):
        message = growatt_heartbeat()
        self.assertEqual(message.arbitration_id, GROWATT_HEARTBEAT_ID)
        self.assertEqual(GROWATT_HEARTBEAT_ID, 0x301)
        self.assertEqual(bytes(message.data), GROWATT_HEARTBEAT_DATA)
        self.assertEqual(GROWATT_HEARTBEAT_DATA, bytes.fromhex("11 22 33 44 55 66 77 88"))
        self.assertFalse(message.is_extended_id)


if __name__ == "__main__":
    unittest.main()
