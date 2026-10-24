import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import stat
from pathlib import Path
from unittest import mock


class FakeFlask:
    def __init__(self, *args, **kwargs):
        self.config = {}

    def _decorator(self, *args, **kwargs):
        return lambda function: function

    before_request = _decorator
    get = _decorator
    post = _decorator

    def run(self, *args, **kwargs):
        return None


fake_flask = types.ModuleType("flask")
fake_flask.Flask = FakeFlask
fake_flask.jsonify = lambda value, *args, **kwargs: value
fake_flask.render_template = lambda *args, **kwargs: ""
fake_flask.request = types.SimpleNamespace(remote_addr="127.0.0.1", files={})
sys.modules.setdefault("flask", fake_flask)

TEMPORARY = tempfile.TemporaryDirectory()
ROOT = Path(TEMPORARY.name)
os.environ["EGRESS_DATA_DIR"] = str(ROOT / "data")
os.environ["EGRESS_OPTIONS_FILE"] = str(ROOT / "options.json")
os.environ["EGRESS_PROFILE_FILE"] = str(ROOT / "client.ovpn")

SPEC = importlib.util.spec_from_file_location(
    "baiamonte_egress_manager", Path(__file__).parents[1] / "manager.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

VALID_PROFILE = b"""client
dev tun
proto udp
remote example.openvpn.com 1194 udp
remote-cert-tls server
<ca>
-----BEGIN CERTIFICATE-----
dGVzdC1jYQ==
-----END CERTIFICATE-----
</ca>
<cert>
-----BEGIN CERTIFICATE-----
dGVzdC1jZXJ0
-----END CERTIFICATE-----
</cert>
<key>
-----BEGIN PRIVATE KEY-----
dGVzdC1rZXk=
-----END PRIVATE KEY-----
</key>
"""


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 999999

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class ConfigurationTests(unittest.TestCase):
    def test_defaults_are_narrow_and_probe_only(self):
        settings = module.normalize_settings({})
        self.assertEqual(settings["mode"], "probe_only")
        self.assertEqual(settings["allowed_source_cidrs"], ["192.168.86.127/32"])
        self.assertEqual(settings["allowed_udp_ports"], [500, 4500])

    def test_broad_source_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"allowed_source_cidrs": ["192.168.0.0/16"]})

    def test_invalid_interface_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"wan_interface": "eth0; reboot"})

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"allowed_udp_ports": [0, 4500]})

    def test_non_https_health_url_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"public_ip_url": "http://example.com"})

    def test_unapproved_health_endpoint_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"public_ip_url": "https://192.168.0.1/status"})

    def test_profile_rejects_inline_command_and_password_file(self):
        for unsafe in (
            b"up /tmp/script\n",
            b"auth-user-pass /tmp/password\n",
            b"log /share/profile.log\n",
            b"key /share/private.key\n",
            b"redirect-gateway def1\n",
            b"route 0.0.0.0 0.0.0.0\n",
            b"pull-filter accept route\n",
            b"config /share/extra.conf\n",
            b"include /share/extra.conf\n",
            b"script-security 2\n",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(module.ConfigurationError):
                    module.validate_profile(VALID_PROFILE + unsafe)

    def test_profile_accepts_certificate_only_connector(self):
        self.assertIn("remote example.openvpn.com", module.validate_profile(VALID_PROFILE))

    def test_profile_storage_is_owner_only(self):
        target = ROOT / "mode-test.ovpn"
        module.atomic_write(target, module.validate_profile(VALID_PROFILE))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)


class FirewallTests(unittest.TestCase):
    def setUp(self):
        module.OPTIONS_FILE.write_text(json.dumps({
            "mode": "tim_egress",
            "tim_destination_ips": ["217.200.184.104"],
        }))
        self.commands = []

        def runner(command, check=True):
            self.commands.append(command)
            if command[:4] == ["ip", "-o", "route", "show"]:
                output = "default via 172.30.32.1 dev eth0\n"
            elif command[:4] == ["ip", "-o", "link", "show"]:
                output = "1: lo: <LOOPBACK>\n7: tun0: <POINTOPOINT>\n"
            elif "-S" in command:
                output = ""
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")

        self.manager = module.GatewayManager(runner=runner)
        self.forwarding = False
        self.manager._read_forwarding = lambda: self.forwarding
        self.manager._set_forwarding = lambda enabled: setattr(self, "forwarding", enabled)
        self.manager.establish_guard()
        self.manager.session_ready = True

    def test_interface_selection_uses_default_route(self):
        self.assertEqual(self.manager.wan_interface(), "eth0")
        self.assertEqual(self.manager.tunnel_interface(), "tun0")

    def test_kernel_tunl_interface_is_not_openvpn(self):
        def runner(command, check=True):
            return subprocess.CompletedProcess(command, 0, "1: tunl0@NONE: <NOARP>\n", "")

        manager = module.GatewayManager(runner=runner)
        self.assertIsNone(manager.tunnel_interface())

    def test_rules_are_source_port_and_interface_scoped(self):
        self.manager.apply_firewall()
        rendered = [" ".join(command) for command in self.commands]
        accepts = [line for line in rendered if module.FWD_CHAIN in line and " -j ACCEPT" in line]
        self.assertTrue(any("-i tun0 -o eth0 -s 192.168.86.127/32 -d 217.200.184.104 -p udp --dport 500" in line for line in accepts))
        self.assertTrue(any("-i tun0 -o eth0 -s 192.168.86.127/32 -d 217.200.184.104 -p udp --dport 4500" in line for line in accepts))
        self.assertFalse(any("0.0.0.0/0" in line for line in rendered))
        self.assertFalse(any("-F FORWARD" in line or "-F POSTROUTING" in line for line in rendered))

    def test_nat_matches_same_source_and_ports(self):
        self.manager.apply_firewall()
        rendered = [" ".join(command) for command in self.commands]
        nat = [line for line in rendered if module.NAT_CHAIN in line and "MASQUERADE" in line]
        self.assertEqual(len(nat), 2)
        self.assertTrue(all("-s 192.168.86.127/32 -o eth0 -d 217.200.184.104 -p udp" in line for line in nat))

    def test_tim_mode_without_destination_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"mode": "tim_egress"})

    def test_https_probe_is_exact_destination_and_tcp_443_only(self):
        self.manager.settings = module.normalize_settings({
            "mode": "https_probe",
            "probe_destination_ips": ["8.8.8.8"],
        })
        self.manager.apply_firewall()
        rendered = [" ".join(command) for command in self.commands]
        accepts = [line for line in rendered if module.FWD_CHAIN in line and " -j ACCEPT" in line]
        self.assertTrue(any(
            "-i tun0 -o eth0 -s 192.168.86.127/32 -d 8.8.8.8 -p tcp --dport 443" in line
            for line in accepts
        ))
        self.assertFalse(any("--dport 500" in line or "--dport 4500" in line for line in rendered))

    def test_https_probe_without_destination_is_rejected(self):
        with self.assertRaises(module.ConfigurationError):
            module.normalize_settings({"mode": "https_probe"})

    def test_repeated_apply_cleans_only_owned_chains(self):
        self.manager.apply_firewall()
        self.manager.apply_firewall()
        rendered = [" ".join(command) for command in self.commands]
        self.assertGreaterEqual(sum(f"-F {module.FWD_CHAIN}" in line for line in rendered), 2)
        self.assertGreaterEqual(sum(f"-F {module.NAT_CHAIN}" in line for line in rendered), 2)
        self.assertFalse(any("-F FORWARD" in line or "-F POSTROUTING" in line for line in rendered))

    def test_cleanup_removes_owned_jumps_and_chains(self):
        def runner(command, check=True):
            self.commands.append(command)
            if command[-2:] == ["-S", "FORWARD"]:
                output = f"-A FORWARD -j {module.GUARD_CHAIN}\n-A FORWARD -j USER_CHAIN\n"
            elif command[-2:] == ["-S", "POSTROUTING"]:
                output = f"-A POSTROUTING -j {module.NAT_CHAIN}\n"
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")

        manager = module.GatewayManager(runner=runner)
        manager.cleanup_firewall()
        rendered = [" ".join(command) for command in self.commands]
        self.assertTrue(any(f"-D FORWARD -j {module.GUARD_CHAIN}" in line for line in rendered))
        self.assertFalse(any("-D FORWARD -j USER_CHAIN" in line for line in rendered))

    def test_probe_mode_installs_no_forwarding_rules(self):
        self.manager.settings["mode"] = "probe_only"
        self.manager.apply_firewall()
        rendered = [" ".join(command) for command in self.commands]
        self.assertFalse(any("--dport 500" in line or "--dport 4500" in line for line in rendered))
        self.assertFalse(self.manager.firewall_applied)

    def test_counters_read_only_owned_chains(self):
        self.commands.clear()
        self.manager.firewall_counters()
        rendered = [" ".join(command) for command in self.commands]
        self.assertEqual(len(rendered), 3)
        self.assertTrue(any(f"-L {module.GUARD_CHAIN}" in line for line in rendered))
        self.assertTrue(any(f"-L {module.FWD_CHAIN}" in line for line in rendered))
        self.assertTrue(any(f"-L {module.NAT_CHAIN}" in line for line in rendered))
        self.assertFalse(any("-L FORWARD" in line or "-L POSTROUTING" in line for line in rendered))

    def test_missing_tunnel_fails_before_installing_rules(self):
        self.manager.tunnel_interface = lambda: None
        with self.assertRaises(RuntimeError):
            self.manager.apply_firewall()
        self.assertFalse(self.manager.firewall_applied)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        module.OPTIONS_FILE.write_text(json.dumps({"mode": "probe_only"}))
        module.PROFILE_FILE.write_bytes(VALID_PROFILE)

        def runner(command, check=True):
            output = "" if command[0] != "ip" else "1: lo: <LOOPBACK>\n"
            return subprocess.CompletedProcess(command, 0, output, "")

        self.manager = module.GatewayManager(runner=runner)
        self.manager.cleanup_firewall = mock.Mock()
        self.manager._read_forwarding = lambda: False
        self.manager._set_forwarding = mock.Mock()

    def test_repeated_start_is_idempotent(self):
        process = FakeProcess()
        with mock.patch.object(module.subprocess, "Popen", return_value=process) as popen:
            with mock.patch.object(module.threading.Thread, "start", return_value=None):
                self.manager.start()
                self.manager.start()
        self.assertEqual(popen.call_count, 1)
        command = popen.call_args.args[0]
        self.assertIn("--route-nopull", command)
        self.assertIn(str(module.RUNTIME_PROFILE_FILE), command)
        self.manager._log_handle.close()
        self.manager._log_handle = None

    def test_repeated_stop_is_idempotent_and_cleans(self):
        self.manager.process = None
        self.manager.stop()
        self.manager.stop()
        self.assertEqual(self.manager.cleanup_firewall.call_count, 2)

    def test_failed_tunnel_state_is_truthfully_disconnected(self):
        self.manager.tunnel_interface = lambda: None
        status = self.manager.status()
        self.assertFalse(status["tunnel"]["connected"])
        self.assertEqual(status["truth"]["tim_registration"], "Requires the physical TIM handset")

if __name__ == "__main__":
    unittest.main()
