import importlib.util
import sys
import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard_vpn.py"
SPEC = importlib.util.spec_from_file_location("dashboard_vpn", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ProfileTests(unittest.TestCase):
    def test_connected_event_is_authoritative_when_status_file_lags(self):
        connector = MODULE.Connector()
        connector.connected_at = "2026-08-26T00:00:00+00:00"
        with patch.object(MODULE, "STATUS_PATH", Path("/missing/openvpn.status")):
            self.assertTrue(connector.is_connected())

    def test_accepts_basic_connector_profile(self):
        profile = "client\nproto udp\nremote example.openvpn.com 1194\n" + ("# filler\n" * 20)
        self.assertEqual((True, ""), MODULE.valid_profile(profile))

    def test_rejects_executable_directives(self):
        profile = "client\nremote example.openvpn.com 1194\nscript-security 2\nup /tmp/run\n" + ("# x\n" * 30)
        ok, message = MODULE.valid_profile(profile)
        self.assertFalse(ok)
        self.assertIn("scripts", message)

    def test_stores_profile_with_private_permissions(self):
        profile = "client\nproto udp\nremote example.openvpn.com 1194\n" + ("# filler\n" * 20)
        with tempfile.TemporaryDirectory() as folder:
            old_path = MODULE.PROFILE_PATH
            MODULE.PROFILE_PATH = Path(folder) / "connector.ovpn"
            try:
                MODULE.store_profile(profile)
                self.assertEqual(0o600, MODULE.PROFILE_PATH.stat().st_mode & 0o777)
                self.assertTrue(MODULE.PROFILE_PATH.read_text().startswith("client\n"))
            finally:
                MODULE.PROFILE_PATH = old_path

    def test_downloads_and_decrypts_setup_token_profile(self):
        profile = "client\nproto udp\nremote example.openvpn.com 1194\n" + ("# filler\n" * 20)
        password = b"test-connector-key"
        salt = bytes(range(32))
        key_material = PBKDF2HMAC(
            algorithm=SHA256(), length=44, salt=salt, iterations=25000,
        ).derive(password)
        encryptor = Cipher(
            algorithms.AES(key_material[:32]), modes.GCM(key_material[32:44]),
        ).encryptor()
        ciphertext = encryptor.update(profile.encode()) + encryptor.finalize()
        encrypted = b64encode(salt + ciphertext + encryptor.tag)
        token = b64encode(password).decode() + ("a" * 40)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return encrypted

        with patch.object(MODULE.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(profile, MODULE.profile_from_setup_token(token))


if __name__ == "__main__":
    unittest.main()
