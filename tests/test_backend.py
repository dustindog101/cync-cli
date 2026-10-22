from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch

from cync_cli import backend


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "cync-home"
        self.helper = Path(self.temp.name) / "Cync Bluetooth Helper.app" / "Contents" / "MacOS" / "CyncBluetoothHelper"

    def _patch_runtime(self):
        return (
            patch.object(backend, "data_home", return_value=self.home),
            patch.object(backend, "ensure_helper", return_value=self.helper),
        )

    def test_keychain_credentials_are_stdin_only(self) -> None:
        secret_key = "secret-mesh-key"
        captured: dict[str, object] = {}

        def run(args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, stdout=b'{"ok":true}', stderr=b"")

        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", side_effect=run):
            result = backend.keychain("store", {"name": "AABBCCDDEEFF", "key": secret_key})

        self.assertEqual(result, {"ok": True})
        self.assertNotIn(secret_key, " ".join(str(arg) for arg in captured["args"]))
        self.assertEqual(json.loads(captured["input"]), {"name": "AABBCCDDEEFF", "key": secret_key})
        self.assertNotIn("input", captured["args"])

    def test_atomic_runtime_file_has_private_mode(self) -> None:
        target = Path(self.temp.name) / "request.json"
        backend._atomic(target, {"request_id": "synthetic", "command": "status"})
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text())["command"], "status")
        self.assertEqual(list(Path(self.temp.name).glob("*.tmp")), [])

    def test_request_reads_matching_response_and_cleans_ipc_files(self) -> None:
        def run(args, **kwargs):
            self.assertEqual(args[0], "open")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        def write_response(_interval: float) -> None:
            mailbox = self.home / "runtime" / "request.json"
            request = json.loads(mailbox.read_text())
            response = {"schema_version": 1, "request_id": request["request_id"],
                        "ok": True, "verified": True, "state": {"on": True}}
            (mailbox.parent / f"response-{request['request_id']}.json").write_text(json.dumps(response))

        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", side_effect=run), \
                patch.object(backend.time, "sleep", side_effect=write_response):
            result = backend.request("status", device={"mesh": "AABBCCDDEEFF", "node": 1,
                                                        "mac": "00:11:22:33:44:55"}, timeout=1)

        self.assertTrue(result["verified"])
        runtime = self.home / "runtime"
        self.assertFalse((runtime / "request.json").exists())
        self.assertEqual(list(runtime.glob("response-*.json")), [])
        self.assertEqual(os.stat(runtime).st_mode & 0o777, 0o700)

    def test_second_request_can_finish_while_first_waits_for_fade(self) -> None:
        runtime = self.home / "runtime"
        first_accepted = threading.Event()
        stop = threading.Event()
        errors = []

        def helper():
            first = None
            try:
                until = time.monotonic() + 5
                while not stop.is_set() and time.monotonic() < until:
                    mailbox = runtime / "request.json"
                    try:
                        job = json.loads(mailbox.read_text())
                    except FileNotFoundError:
                        time.sleep(0.005)
                        continue
                    mailbox.unlink()
                    if job["command"] == "fade":
                        first = job
                        first_accepted.set()
                    else:
                        for item in (job, first):
                            backend._atomic(runtime / ("response-" + item["request_id"] + ".json"),
                                            {"schema_version": 1, "request_id": item["request_id"], "ok": True, "verified": True})
                        return
            except Exception as exc:
                errors.append(exc)

        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", side_effect=run):
            thread = threading.Thread(target=helper)
            thread.start()
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fade = pool.submit(backend.request, "fade", timeout=1)
                    self.assertTrue(first_accepted.wait(2))
                    status = pool.submit(backend.request, "status", timeout=1)
                    self.assertTrue(status.result(timeout=3)["verified"])
                    self.assertTrue(fade.result(timeout=3)["verified"])
            finally:
                stop.set()
                thread.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(list(runtime.glob("response-*.json")), [])

    def test_priority_cancel_bypasses_occupied_ordinary_mailbox(self) -> None:
        runtime = self.home / "runtime"
        runtime.mkdir(parents=True)
        ordinary = runtime / "request.json"
        ordinary.write_text('{"request_id":"untouched","command":"set"}')
        def respond(_interval):
            pending = list(runtime.glob("priority-*.json"))
            self.assertEqual(len(pending), 1)
            job = json.loads(pending[0].read_text())
            backend._atomic(runtime / ("response-" + job["request_id"] + ".json"),
                            {"request_id": job["request_id"], "ok": True, "operation": {"status": "cancelled"}})
        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(backend.time, "sleep", side_effect=respond):
            result = backend.request("fade-cancel", timeout=1)
        self.assertEqual(result["operation"]["status"], "cancelled")
        self.assertEqual(json.loads(ordinary.read_text())["request_id"], "untouched")
        self.assertEqual(list(runtime.glob("priority-*.json")), [])

    def test_background_start_wait_is_bounded_independently_of_native_fade_budget(self) -> None:
        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(backend.time, "monotonic", side_effect=[100.0, 139.0]):
            with self.assertRaises(backend.BackendError) as error:
                backend.request("fade", values={"background": True, "duration": 600}, timeout=635)
        self.assertEqual(error.exception.code, "TIMEOUT")
        self.assertFalse((self.home / "runtime/request.json").exists())

    def test_native_error_and_timeout_are_safe_and_remove_mailbox(self) -> None:
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        def write_error(_interval: float) -> None:
            mailbox = self.home / "runtime" / "request.json"
            request = json.loads(mailbox.read_text())
            response = {"schema_version": 1, "request_id": request["request_id"],
                        "ok": False, "verified": False,
                        "error": {"code": "KEY_UNAVAILABLE", "message": "Keychain key unavailable."}}
            (mailbox.parent / f"response-{request['request_id']}.json").write_text(json.dumps(response))

        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", side_effect=run), \
                patch.object(backend.time, "sleep", side_effect=write_error):
            with self.assertRaises(backend.BackendError) as caught:
                backend.request("status", timeout=1)
        self.assertEqual(caught.exception.code, "KEY_UNAVAILABLE")
        self.assertNotIn("secret", str(caught.exception))
        self.assertFalse((self.home / "runtime" / "request.json").exists())
        self.assertEqual(list((self.home / "runtime").glob("response-*.json")), [])

        home_patch, helper_patch = self._patch_runtime()
        with home_patch, helper_patch, patch.object(backend.subprocess, "run", side_effect=run), \
                patch.object(backend.time, "monotonic", side_effect=[100.0, 104.0]):
            with self.assertRaises(backend.BackendError) as timeout_error:
                backend.request("status", timeout=0)
        self.assertEqual(timeout_error.exception.code, "TIMEOUT")
        self.assertNotIn("token", str(timeout_error.exception))
        self.assertFalse((self.home / "runtime" / "request.json").exists())


if __name__ == "__main__":
    unittest.main()
