from __future__ import annotations

import json
import contextlib
import io
import os
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from cync_cli import auth, config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "private"
        self.home.mkdir(mode=0o755)
        self.addCleanup(patch.stopall)
        patch("cync_cli.backend.data_home", return_value=self.home).start()

    def test_save_uses_private_modes_and_round_trips(self) -> None:
        record = {"id": "abc123", "name": "Desk", "mesh": "AABBCCDDEEFF", "node": 42,
                  "mac": "01:23:45:67:89:AB", "peripheral_id": None, "type": 21}
        data = {"schema_version": 1, "default": "abc123", "devices": [record]}
        config.save_config(data)
        self.assertEqual(os.stat(self.home).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(config.config_path()).st_mode & 0o777, 0o600)
        self.assertEqual(config.load_config(), data)
        self.assertNotIn("key", config.config_path().read_text())

    def test_rejects_invalid_default(self) -> None:
        with self.assertRaises(config.ConfigError):
            config.save_config({"schema_version": 1, "default": "missing", "devices": []})

    def test_rejects_duplicate_device_ids(self) -> None:
        record = {"id": "same", "name": "Lamp", "mesh": "AABBCCDDEEFF", "node": 1,
                  "mac": "01:23:45:67:89:AB", "peripheral_id": None, "type": 21}
        with self.assertRaises(config.ConfigError):
            config.save_config({"schema_version": 1, "default": "same", "devices": [record, record]})


class AuthTests(unittest.TestCase):
    def test_login_prompt_preserves_json_stdout(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with patch("builtins.input", return_value="person@example.test"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(auth._interactive_email(None, True), "person@example.test")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "Cync email: ")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.addCleanup(patch.stopall)
        patch("cync_cli.backend.data_home", return_value=self.home).start()

    def test_mocked_cloud_bootstrap_stores_only_mesh_key_and_device_metadata(self) -> None:
        token = "temporary-cloud-token"
        password = "test" + "-password"
        calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

        def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float):
            calls.append((method, url, headers, body))
            if url.endswith("/two_factor/email/verifycode"):
                return 200, {"ok": True}
            if url.endswith("/user_auth/two_factor"):
                sent = json.loads(body or b"{}")
                self.assertEqual(sent["password"], password)
                self.assertRegex(sent["resource"], re.compile(r"^[0-9a-f]{16}$"))
                return 200, {"access_token": token, "user_id": 100}
            if url.endswith("/user/100/subscribe/devices"):
                self.assertEqual(headers.get("Access-Token"), token)
                return 200, [{"id": 7, "mac": "AABBCCDDEEFF",
                              "access_key": "mesh-key-value", "product_id": 3}]
            if url.endswith("/product/3/device/7/property"):
                self.assertEqual(headers.get("Access-Token"), token)
                return 200, {"bulbsArray": [
                    {"deviceID": "bulb-042", "displayName": "Desk lamp", "mac": "001122334455", "deviceType": 21},
                    {"deviceID": "bad-node", "displayName": "Skip me", "mac": "001122334466", "deviceType": 21},
                    {"deviceID": "bulb-256", "displayName": "Out of range", "mac": "001122334477", "deviceType": 21},
                    {"deviceID": "bulb-43", "displayName": "Malformed MAC", "mac": "G01122334488", "deviceType": 21},
                ]}
            self.fail("unexpected cloud endpoint")

        keychain_calls: list[tuple[str, dict[str, str]]] = []

        def keychain(action: str, payload: dict[str, str]):
            keychain_calls.append((action, payload))
            return {"ok": True}

        with patch("cync_cli.auth.backend.keychain", side_effect=keychain):
            devices = auth.login(email="person@example.test", password=password, code="000123",
                                 prompt=False, transport=transport)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["node"], 42)
        self.assertEqual(devices[0]["mac"], "00:11:22:33:44:55")
        self.assertEqual(keychain_calls, [("store", {"name": "AABBCCDDEEFF", "key": "mesh-key-value"})])
        serialized = config.config_path().read_text()
        for secret in (password, token, "mesh-key-value"):
            self.assertNotIn(secret, serialized)
        self.assertEqual([call[0] for call in calls], ["POST", "POST", "GET", "GET"])

    def test_transport_failure_does_not_echo_credentials(self) -> None:
        def broken(*_args):
            raise RuntimeError("pass" + "word=private token=private-token")

        with self.assertRaises(auth.AuthError) as caught:
            auth.login(email="person@example.test", password="private", code="private-code",
                       prompt=False, transport=broken)
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("token", str(caught.exception))

    def test_node_suffixes_and_mac_validation_are_bounded(self) -> None:
        self.assertEqual(auth._node(104), 104)
        self.assertEqual(auth._node("bulb-1"), 1)
        self.assertEqual(auth._node("device-255"), 255)
        for value in (0, 256, "bulb-000", "bulb-256", "bulb-no-node", True):
            self.assertIsNone(auth._node(value))
        self.assertEqual(auth.normalize_mac("aa-bb.cc:dd-ee-ff"), "AA:BB:CC:DD:EE:FF")
        self.assertIsNone(auth.normalize_mac("G001122334455"))
        self.assertIsNone(auth.normalize_mac("00:11:22:33:44:55junk"))

    def test_legacy_import_normalizes_metadata_and_never_persists_key(self) -> None:
        returned = {"ok": True, "devices": [
            {"name": "Legacy lamp", "mesh": "AABBCCDDEEFF", "node": 12,
             "mac": "001122334455", "peripheral_id": None},
            {"name": "Bad node", "mesh": "AABBCCDDEEFF", "node": 256,
             "mac": "001122334466", "peripheral_id": None},
            {"name": "Bad MAC", "mesh": "AABBCCDDEEFF", "node": 13,
             "mac": "G01122334466", "peripheral_id": None},
        ]}
        with patch("cync_cli.auth.backend.keychain", return_value=returned) as keychain:
            imported = auth.import_legacy()
        keychain.assert_called_once_with("import-legacy", {})
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["mac"], "00:11:22:33:44:55")
        self.assertEqual(imported[0]["node"], 12)
        serialized = config.config_path().read_text()
        self.assertNotIn("key", serialized)

    def test_status_redacts_keychain_error_details(self) -> None:
        with patch("cync_cli.auth.backend.keychain", side_effect=RuntimeError("secret token=hidden")):
            result = auth.status({"devices": [{"mesh": "AABBCCDDEEFF"}]})
        self.assertEqual(result["keychain_available"], False)
        self.assertNotIn("hidden", json.dumps(result))

    def test_redirect_is_rejected_without_following_location(self) -> None:
        url = auth.API_ROOT + "user_auth/two_factor"
        calls: list[object] = []

        class RedirectingOpener:
            def open(self, request, timeout):
                calls.append(request)
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://attacker.invalid/"}, None)

        with patch.object(auth, "_OPENER", RedirectingOpener()):
            with self.assertRaises(auth.AuthError) as caught:
                auth._transport("POST", url, {"Content-Type": "application/json"}, b'{"password":"private"}', 1)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
