import unittest
from unittest.mock import MagicMock

import sys

sys.modules.setdefault("can", MagicMock())

from can_monitor import uses_listen_only


class BusModeTests(unittest.TestCase):
    def test_default_is_hardware_listen_only(self):
        self.assertTrue(uses_listen_only({}))

    def test_explicit_passive_mode_is_hardware_listen_only(self):
        self.assertTrue(uses_listen_only({"bus_mode": "listen_only"}))

    def test_standalone_mode_acknowledges_frames(self):
        self.assertFalse(uses_listen_only({"bus_mode": "standalone_ack"}))


if __name__ == "__main__":
    unittest.main()
