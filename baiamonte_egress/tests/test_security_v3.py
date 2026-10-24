import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class FakeFlask:
    def __init__(self, *args, **kwargs): self.config = {}
    def _decorator(self, *args, **kwargs): return lambda function: function
    before_request = get = post = _decorator
    def run(self, *args, **kwargs): return None


fake_flask = types.ModuleType("flask")
fake_flask.Flask = FakeFlask
fake_flask.jsonify = lambda value, *args, **kwargs: value
fake_flask.render_template = lambda *args, **kwargs: ""
fake_flask.request = types.SimpleNamespace(remote_addr="127.0.0.1", files={})
sys.modules.setdefault("flask", fake_flask)

TEMPORARY = tempfile.TemporaryDirectory()
ROOT = Path(TEMPORARY.name)
os.environ["EGRESS_DATA_DIR"] = str(ROOT / "data-v3")
os.environ["EGRESS_OPTIONS_FILE"] = str(ROOT / "options-v3.json")
os.environ["EGRESS_PROFILE_FILE"] = str(ROOT / "client-v3.ovpn")
os.environ["EGRESS_RUNTIME_PROFILE_FILE"] = str(ROOT / "runtime-v3.ovpn")

SPEC = importlib.util.spec_from_file_location("baiamonte_egress_v3", Path(__file__).parents[1] / "manager.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

VALID_PROFILE = b"""client
dev tun
proto udp
remote example.openvpn.com 1194 udp
remote-cert-tls server
auth SHA256
cipher AES-256-CBC
nobind
persist-key
persist-tun
verb 3
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
<tls-auth>
-----BEGIN OpenVPN Static key V1-----
00000000000000000000000000000000
11111111111111111111111111111111
22222222222222222222222222222222
33333333333333333333333333333333
44444444444444444444444444444444
55555555555555555555555555555555
66666666666666666666666666666666
77777777777777777777777777777777
88888888888888888888888888888888
99999999999999999999999999999999
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
cccccccccccccccccccccccccccccccc
dddddddddddddddddddddddddddddddd
eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
ffffffffffffffffffffffffffffffff
-----END OpenVPN Static key V1-----
</tls-auth>
"""


class FakeProcess:
    def __init__(self): self.returncode, self.pid = None, 999999
    def poll(self): return self.returncode
    def wait(self, timeout=None): self.returncode = 0; return 0


class FakeRunner:
    def __init__(self): self.commands = []
    def __call__(self, command, check=True):
        self.commands.append(list(command))
        output = ""
        if command[:5] == ["ip", "-o", "route", "show", "default"]:
            output = "default via 172.30.32.1 dev eth0\n"
        elif command[:4] == ["ip", "-o", "link", "show"]:
            output = "1: lo: <LOOPBACK>\n7: tun0: <POINTOPOINT>\n"
        elif command[:6] == ["ip", "-4", "route", "show", "table", "main"]:
            output = "default via 172.30.32.1 dev eth0\n"
        elif command[:4] == ["ip", "-4", "route", "show"] and len(command) == 5:
            output = f"{command[-1]} dev tun0\n"
        return subprocess.CompletedProcess(command, 0, output, "")


class ProfileSecurityTests(unittest.TestCase):
    @staticmethod
    def profile_with_block(tag):
        if tag == "tls-crypt":
            return VALID_PROFILE.replace(b"<tls-auth>", b"<tls-crypt>").replace(
                b"</tls-auth>", b"</tls-crypt>"
            )
        return VALID_PROFILE

    def test_profile_is_canonicalized_and_created_0600(self):
        canonical = module.validate_profile(VALID_PROFILE)
        self.assertIn("remote example.openvpn.com 1194 udp", canonical)
        target = ROOT / "canonical.ovpn"
        module.atomic_write(target, canonical)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_dash_prefixed_quoted_and_include_bypasses_are_rejected(self):
        unsafe_lines = (
            b"--client\n",
            b"--script-security 2\n", b"--up /tmp/script\n",
            b"--config /share/extra.conf\n", b"--redirect-gateway def1\n",
            b'--plugin "/tmp/evil plugin.so"\n', b"include /share/extra.conf\n",
            b"route 0.0.0.0 0.0.0.0\n", b"pull-filter accept route\n",
            b"auth-user-pass /tmp/password\n", b"key /share/private.key\n",
        )
        for unsafe in unsafe_lines:
            with self.subTest(unsafe=unsafe), self.assertRaises(module.ConfigurationError):
                module.validate_profile(VALID_PROFILE + unsafe)

    def test_unknown_duplicate_conflicting_and_malformed_profiles_are_rejected(self):
        cases = (
            VALID_PROFILE + b"unknown-option value\n",
            VALID_PROFILE + b"proto tcp-client\n",
            VALID_PROFILE + b"<ca>\nsecond\n</ca>\n",
            VALID_PROFILE.replace(b"</key>", b"</cert>"),
            VALID_PROFILE.replace(b"dev tun", b"dev tap"),
        )
        for profile in cases:
            with self.subTest(profile=profile[-40:]), self.assertRaises(module.ConfigurationError):
                module.validate_profile(profile)

    def test_every_inline_block_rejects_comment_suffix_whitespace_and_nesting(self):
        for tag in ("ca", "cert", "key", "tls-auth", "tls-crypt"):
            profile = self.profile_with_block(tag)
            opening = f"<{tag}>".encode()
            closing = f"</{tag}>".encode()
            malformed = (
                profile.replace(opening + b"\n", opening + b" # comment\n", 1),
                profile.replace(opening + b"\n", b" " + opening + b"\n", 1),
                profile.replace(closing + b"\n", closing + b" # comment\n", 1),
                profile.replace(closing + b"\n", closing + b" \n", 1),
                profile.replace(opening + b"\n", opening + b"\n<cert>\n", 1),
                profile.replace(
                    closing + b"\n",
                    closing + b" # comment\nplugin /tmp/not-loaded-review.so\n"
                    + opening + b" # comment\n" + closing + b"\n",
                    1,
                ),
            )
            for candidate in malformed:
                with self.subTest(tag=tag, tail=candidate[-80:]), self.assertRaises(
                    module.ConfigurationError
                ):
                    module.validate_profile(candidate)

    def test_inline_material_must_be_strict_pem_or_openvpn_static_key(self):
        malformed = (
            VALID_PROFILE.replace(b"dGVzdC1jYQ==", b"not pem data!"),
            VALID_PROFILE.replace(b"-----END CERTIFICATE-----", b"-----END PRIVATE KEY-----", 1),
            VALID_PROFILE.replace(b"00000000000000000000000000000000", b"00", 1),
        )
        for profile in malformed:
            with self.subTest(profile=profile[-80:]), self.assertRaises(module.ConfigurationError):
                module.validate_profile(profile)

    def test_production_config_is_exact(self):
        for raw in (
            {"allowed_udp_ports": [53, 500]},
            {"allowed_source_cidrs": ["192.168.86.0/24"]},
            {"mode": "tim_egress"},
        ):
            with self.subTest(raw=raw), self.assertRaises(module.ConfigurationError):
                module.normalize_settings(raw)


class GuardTransitionTests(unittest.TestCase):
    def setUp(self):
        module.OPTIONS_FILE.write_text(json.dumps({
            "mode": "tim_egress",
            "allowed_source_cidrs": ["192.168.86.127/32"],
            "tim_destination_ips": ["217.200.184.104"],
        }))
        self.runner = FakeRunner()
        self.manager = module.GatewayManager(runner=self.runner)
        self.forwarding = False
        self.manager._read_forwarding = lambda: self.forwarding
        def set_forwarding(enabled):
            self.forwarding = enabled
            self.runner.commands.append(["forwarding", "1" if enabled else "0"])
        self.manager._set_forwarding = set_forwarding
        self.manager.establish_guard()
        self.manager.session_ready = True

    def lines(self): return [" ".join(command) for command in self.runner.commands]

    def test_deny_guard_precedes_global_jump(self):
        lines = self.lines()
        drop = next(i for i, line in enumerate(lines) if f"-A {module.GUARD_CHAIN} -i tun+ -j DROP" in line)
        jump = next(i for i, line in enumerate(lines) if f"-I FORWARD 1 -j {module.GUARD_CHAIN}" in line)
        self.assertLess(drop, jump)
        self.assertFalse(self.forwarding)

    def test_activation_is_complete_before_forwarding(self):
        self.manager.apply_firewall()
        lines = self.lines()
        nat_jump = max(i for i, line in enumerate(lines) if f"-A POSTROUTING -j {module.NAT_CHAIN}" in line)
        activate = max(i for i, line in enumerate(lines) if f"-R {module.GUARD_CHAIN} 1 -j {module.FWD_CHAIN}" in line)
        enable = max(i for i, line in enumerate(lines) if line == "forwarding 1")
        self.assertLess(nat_jump, activate)
        self.assertLess(activate, enable)

    def test_forward_and_nat_have_exact_four_tuple_boundaries(self):
        self.manager.apply_firewall()
        lines = self.lines()
        for port in (500, 4500):
            expected = f"-i tun0 -o eth0 -s 192.168.86.127/32 -d 217.200.184.104 -p udp --dport {port}"
            self.assertTrue(any(expected in line and "-j ACCEPT" in line for line in lines))
            nat = f"-s 192.168.86.127/32 -o eth0 -d 217.200.184.104 -p udp --dport {port}"
            self.assertTrue(any(nat in line and "MASQUERADE" in line for line in lines))

    def test_deactivation_restores_drop_before_deleting_rules(self):
        self.manager.apply_firewall()
        self.runner.commands.clear()
        self.manager.deactivate_forwarding()
        lines = self.lines()
        drop = next(i for i, line in enumerate(lines) if f"-R {module.GUARD_CHAIN} 1 -i tun+ -j DROP" in line)
        flush = next(i for i, line in enumerate(lines) if f"-F {module.FWD_CHAIN}" in line)
        self.assertLess(drop, flush)
        self.assertFalse(self.forwarding)

    def test_probe_only_retains_guard_and_never_enables_forwarding(self):
        self.manager.settings["mode"] = "probe_only"
        self.manager.apply_firewall()
        self.assertTrue(self.manager.guard_installed)
        self.assertFalse(self.manager.firewall_applied)
        self.assertFalse(self.forwarding)

    def test_only_reviewed_return_route_is_added_and_removed(self):
        self.manager.apply_firewall()
        self.assertIn("ip -4 route add 192.168.86.127/32 dev tun0", self.lines())
        self.manager.deactivate_forwarding()
        self.assertIn("ip -4 route del 192.168.86.127/32 dev tun0", self.lines())

    def test_cleanup_never_flushes_global_chains(self):
        self.manager.cleanup_firewall()
        self.assertFalse(any("-F FORWARD" in line or "-F POSTROUTING" in line for line in self.lines()))


class ForwardingSysctlTests(unittest.TestCase):
    def setUp(self):
        module.OPTIONS_FILE.write_text(json.dumps({"mode": "probe_only"}))
        self.manager = module.GatewayManager(runner=FakeRunner())

    def test_matching_read_only_forwarding_state_is_a_noop(self):
        self.manager._read_forwarding = mock.Mock(return_value=True)
        with mock.patch.object(module.Path, "write_text", side_effect=AssertionError("must not write")):
            self.manager._set_forwarding(True)
        self.manager._read_forwarding.assert_called_once_with()

    def test_required_read_only_forwarding_transition_fails_closed(self):
        self.manager._read_forwarding = mock.Mock(return_value=False)
        with mock.patch.object(module.Path, "write_text", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(RuntimeError, "disabled.*read-only"):
                self.manager._set_forwarding(True)


class LifecycleSecurityTests(unittest.TestCase):
    def setUp(self):
        module.OPTIONS_FILE.write_text(json.dumps({"mode": "probe_only"}))
        module.PROFILE_FILE.write_bytes(VALID_PROFILE)
        self.runner = FakeRunner()
        self.manager = module.GatewayManager(runner=self.runner)
        self.manager._read_forwarding = lambda: False
        self.manager._set_forwarding = mock.Mock()

    def test_start_revalidates_and_uses_generated_route_nopull_profile(self):
        process = FakeProcess()
        with mock.patch.object(module.subprocess, "Popen", return_value=process) as popen:
            with mock.patch.object(module.threading.Thread, "start", return_value=None):
                self.manager.start()
        command = popen.call_args.args[0]
        self.assertIn(str(module.RUNTIME_PROFILE_FILE), command)
        self.assertIn("--route-nopull", command)
        self.assertNotIn(str(module.PROFILE_FILE), command)
        self.assertEqual(stat.S_IMODE(module.RUNTIME_PROFILE_FILE.stat().st_mode), 0o600)
        self.assertTrue(self.manager.guard_installed)
        self.manager._log_handle.close()
        self.manager._log_handle = None

    def test_interface_alone_is_not_session_readiness(self):
        self.manager.process = FakeProcess()
        self.manager.tunnel_interface = lambda: "tun0"
        status = self.manager.status()
        self.assertTrue(status["tunnel"]["process_running"])
        self.assertTrue(status["tunnel"]["interface_present"])
        self.assertFalse(status["tunnel"]["session_ready"])
        self.assertFalse(status["tunnel"]["connected"])

    def test_log_success_and_restart_change_session_readiness(self):
        module.OPENVPN_LOG.parent.mkdir(parents=True, exist_ok=True)
        module.OPENVPN_LOG.write_text("Initialization Sequence Completed\n")
        self.manager._log_position = 0
        self.manager._read_session_events()
        self.assertTrue(self.manager.session_ready)
        with module.OPENVPN_LOG.open("a") as handle:
            handle.write("SIGUSR1[soft,ping-restart] received, process restarting\n")
        self.manager._read_session_events()
        self.assertFalse(self.manager.session_ready)

    def test_unrecoverable_guard_failure_stops_connector(self):
        self.manager.process = FakeProcess()
        self.manager._terminate_openvpn = mock.Mock()
        self.manager.deactivate_forwarding = mock.Mock(side_effect=RuntimeError("guard stuck"))
        self.manager._fail_closed("setup failed")
        self.manager._terminate_openvpn.assert_called_once()
        self.assertIn("guard error", self.manager.last_error)


if __name__ == "__main__":
    unittest.main()
