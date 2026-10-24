import json
import tempfile
import unittest
from pathlib import Path

from relay import ConfigurationError, read_settings


class ConfigTests(unittest.TestCase):
    def read(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return read_settings(path)

    def assert_rejected(self, value):
        with self.assertRaises(ConfigurationError):
            self.read(value)

    def test_disabled_defaults_are_fail_closed(self):
        settings = self.read({})
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.provider_id, "tim")
        self.assertEqual(settings.allowed_source_ips, ())
        self.assertEqual(settings.upstream_ips, ())

    def test_enabled_requires_both_exact_allowlists(self):
        self.assert_rejected({"enabled": True})
        self.assert_rejected({"enabled": True, "allowed_source_ips": ["192.168.86.127"]})

    def test_accepts_exact_source_and_verified_public_style_address(self):
        settings = self.read(
            {
                "enabled": True,
                "provider_id": "tim",
                "allowed_source_ips": ["192.168.86.127"],
                "upstream_ips": ["217.200.184.104"],
            }
        )
        self.assertEqual(settings.allowed_source_ips, ("192.168.86.127",))
        self.assertEqual(settings.upstream_ips, ("217.200.184.104",))

    def test_rejects_unknown_provider(self):
        self.assert_rejected({"provider_id": "unreviewed-carrier"})

    def test_rejects_unknown_options_and_nonboolean_enabled(self):
        self.assert_rejected({"enabled": "false"})
        self.assert_rejected({"redirect_gateway": True})

    def test_rejects_networks_hostnames_ipv6_and_nonpublic_upstreams(self):
        for source in ("192.168.86.0/24", "phone.example", "::1", "0.0.0.0"):
            self.assert_rejected({"allowed_source_ips": [source]})
        for upstream in (
            "217.200.184.0/24", "epdg.example", "::1", "192.168.0.10",
            "127.0.0.1", "224.0.0.1", "255.255.255.255", "198.51.100.10"
        ):
            self.assert_rejected({"upstream_ips": [upstream]})

    def test_rejects_out_of_bounds_controls(self):
        for key, value in (
            ("max_sessions", 0),
            ("max_sessions", 33),
            ("idle_timeout_seconds", 29),
            ("packets_per_second", 9),
            ("burst_packets", 9),
            ("max_datagram_bytes", 511),
            ("max_datagram_bytes", 65508),
            ("max_pending_packets", 7),
            ("max_pending_packets", 513),
            ("max_pending_per_source", 3),
            ("max_pending_per_source", 129),
        ):
            self.assert_rejected({key: value})
        self.assert_rejected({"max_sessions": "4"})

    def test_rejects_too_many_addresses_and_deduplicates(self):
        self.assert_rejected({"allowed_source_ips": [f"10.0.0.{n}" for n in range(1, 10)]})
        settings = self.read({"allowed_source_ips": ["192.168.86.127", "192.168.86.127"]})
        self.assertEqual(settings.allowed_source_ips, ("192.168.86.127",))


if __name__ == "__main__":
    unittest.main()

