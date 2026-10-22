"""Native CoreBluetooth bridge; credentials travel over stdin, never argv or disk."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import platform
import plistlib
import shutil
import subprocess
import sys
import time
import uuid


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def data_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CYNC_HOME", "~/Library/Application Support/cync-cli")).expanduser()


def _private_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextlib.contextmanager
def _lock(path: pathlib.Path):
    import fcntl
    _private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _native_source() -> pathlib.Path:
    options = [
        pathlib.Path(__file__).resolve().parents[2] / "native" / "main.swift",
        data_home() / "share" / "cync-cli" / "native" / "main.swift",
        pathlib.Path(sys.prefix) / "share" / "cync-cli" / "native" / "main.swift",
    ]
    for path in options:
        if path.is_file():
            return path
    raise BackendError("SOURCE_MISSING", "Native helper source is missing. Reinstall cync-cli from its complete repository or package.")


def ensure_helper() -> pathlib.Path:
    if sys.platform != "darwin":
        raise BackendError("MACOS_REQUIRED", "Bluetooth control requires macOS. Help and offline tests work on other systems.")
    source = _native_source()
    signature = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()
    root = data_home() / "native"
    app = root / "Cync Bluetooth Helper.app"
    executable = app / "Contents" / "MacOS" / "CyncBluetoothHelper"
    stamp = root / "source.sha256"
    with _lock(root / "build.lock"):
        if executable.is_file() and stamp.is_file() and stamp.read_text().strip() == signature:
            return executable
        if not shutil.which("swiftc") or not shutil.which("codesign"):
            raise BackendError("BUILD_TOOLS_REQUIRED", "Install Apple's Command Line Tools with xcode-select --install, then try again.")
        _private_dir(executable.parent)
        info = {
            "CFBundleIdentifier": "io.cync-cli.bluetooth-helper",
            "CFBundleName": "Cync Bluetooth Helper",
            "CFBundleExecutable": executable.name,
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "LSUIElement": True,
            "NSBluetoothAlwaysUsageDescription": "Discover and control your paired Cync Bluetooth lights locally.",
            "NSBluetoothPeripheralUsageDescription": "Control your own paired Cync lightbulbs over Bluetooth.",
        }
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        temp = executable.with_name(executable.name + ".new")
        arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
        try:
            build = subprocess.run(
                ["swiftc", "-target", f"{arch}-apple-macosx12.0", str(source), "-o", str(temp),
                 "-framework", "Cocoa", "-framework", "CoreBluetooth", "-framework", "Security"],
                capture_output=True, timeout=120,
            )
            if build.returncode:
                # Compiler output contains source paths but no runtime credentials.
                detail = build.stderr.decode(errors="replace")[-4000:]
                raise BackendError("BUILD_FAILED", "Could not build the native helper.\n" + detail)
            temp.replace(executable)
            signed = subprocess.run(["codesign", "--force", "--sign", "-", str(app)], capture_output=True, timeout=30)
            if signed.returncode:
                raise BackendError("SIGN_FAILED", "Could not sign the native Bluetooth helper.")
            stamp.write_text(signature + "\n")
            stamp.chmod(0o600)
        except subprocess.TimeoutExpired:
            raise BackendError("BUILD_TIMEOUT", "Native helper build timed out; check Apple's Command Line Tools.") from None
        finally:
            temp.unlink(missing_ok=True)
    return executable


def keychain(action: str, payload: dict | None = None) -> dict:
    if action not in ("store", "status", "forget", "import-legacy"):
        raise BackendError("INVALID_OPERATION", "Unknown secure-storage operation.")
    executable = ensure_helper()
    try:
        proc = subprocess.run([str(executable), "--keychain-" + action],
                              input=json.dumps(payload or {}).encode(), capture_output=True, timeout=120)
        result = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise BackendError("KEYCHAIN_FAILED", "Keychain operation failed. Unlock Keychain and allow the helper access.") from None
    if proc.returncode or not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error", {}) if isinstance(result, dict) else {}
        raise BackendError(error.get("code", "KEYCHAIN_FAILED"), error.get("message", "Keychain operation failed."))
    return result


def _atomic(path: pathlib.Path, data: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def request(command: str, device: dict | None = None, values: dict | None = None, timeout: float = 35) -> dict:
    executable = ensure_helper()
    runtime = data_home() / "runtime"
    _private_dir(runtime)
    request_id = str(uuid.uuid4())
    response = runtime / ("response-" + request_id + ".json")
    priority = command in {"fade-status", "fade-cancel", "doctor"}
    mailbox = runtime / (f"priority-{request_id}.json" if priority else "request.json")
    response_budget = min(timeout, 35) if command == "fade" and (values or {}).get("background") else timeout
    deadline = time.monotonic() + response_budget + 3
    try:
        launched = subprocess.run(["open", "-g", "-j", str(executable.parents[2]), "--args", "--runtime", str(runtime)],
                                  capture_output=True, timeout=10)
        if launched.returncode:
            raise BackendError("LAUNCH_FAILED", "Could not open the Bluetooth helper. Reinstall cync-cli and check macOS permissions.")
        payload = {"request_id": request_id, "command": command, "device": device,
                   "values": values or {}, "timeout": timeout}
        if priority:
            # Unique urgent publications cannot be blocked by a queued ordinary command.
            _atomic(mailbox, payload)
        else:
            while True:
                # Never sleep with the publication lock held.
                with _lock(runtime / "client.lock"):
                    if not mailbox.exists():
                        _atomic(mailbox, payload)
                        break
                if time.monotonic() >= deadline:
                    raise BackendError("BUSY", "The helper has not accepted the previous request. Run cync doctor.")
                time.sleep(0.1)
        while time.monotonic() < deadline:
            try:
                result = json.loads(response.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.1)
                continue
            if result.get("request_id") != request_id:
                raise BackendError("PROTOCOL_ERROR", "Helper returned an unrelated response.")
            if not result.get("ok"):
                error = result.get("error", {})
                raise BackendError(error.get("code", "BLUETOOTH_FAILED"), error.get("message", "Bluetooth request failed."))
            return result
        raise BackendError("TIMEOUT", "Bluetooth helper did not respond. Allow macOS Bluetooth access and run cync doctor.")
    finally:
        response.unlink(missing_ok=True)
        # The lock prevents a stale caller from removing a newer publication.
        with _lock(runtime / "client.lock"):
            try:
                if json.loads(mailbox.read_text()).get("request_id") == request_id:
                    mailbox.unlink()
            except (OSError, ValueError):
                pass
