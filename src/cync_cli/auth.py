"""Interactive Cync account bootstrap and native Keychain operations.

Account credentials and the short-lived cloud token exist only in this module's
call stack. Mesh control keys are handed directly to the native Keychain helper.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
import secrets
import string
import sys
import urllib.parse as urlparse
import urllib.error
import urllib.request
from typing import Any, Callable

from . import backend, config


API_ROOT = "https://api.gelighting.com/v2/"
CORP_ID = "1007d2ad150c4000"
API_TIMEOUT = 20
MAX_RESPONSE_BYTES = 1_000_000


class AuthError(RuntimeError):
    """A safe-to-display authentication or cloud discovery failure."""


def _keychain(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = backend.keychain(action, payload or {})
    except Exception:
        # Backend diagnostics may contain serialized helper input. Keep the
        # public error safe even if a lower layer reports too much detail.
        raise AuthError("Keychain operation failed") from None
    if not isinstance(result, dict) or result.get("ok") is False:
        raise AuthError("Keychain operation failed")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so credentials and Access-Token never follow them."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())
Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, Any]]


def _transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> tuple[int, Any]:
    parsed = urlparse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.gelighting.com" or parsed.port is not None:
        raise AuthError("Refusing an unexpected Cync API destination")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if response.geturl() != url:
                raise AuthError("Cync API redirected the request; sign-in stopped")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AuthError("Cync API response was unexpectedly large")
            try:
                value = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeError, json.JSONDecodeError):
                raise AuthError("Cync API returned an invalid response") from None
            return response.status, value
    except urllib.error.HTTPError as exc:
        # Do not read or echo response bodies: providers can include account data.
        exc.close()
        if 300 <= exc.code < 400:
            raise AuthError("Cync API redirected the request; sign-in stopped") from None
        raise AuthError(f"Cync API request failed (HTTP {exc.code})") from None
    except urllib.error.URLError:
        raise AuthError("Could not reach the Cync API") from None
    except TimeoutError:
        raise AuthError("Cync API request timed out") from None


def _call(method: str, path: str, *, token: str | None = None, body: dict[str, Any] | None = None,
          transport: Transport | None = None) -> Any:
    # Paths are assembled only from quoted server-returned identifiers.
    url = API_ROOT + path
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    if token is not None:
        headers["Access-Token"] = token
    try:
        status, value = (transport or _transport)(method, url, headers, payload, API_TIMEOUT)
    except AuthError:
        raise
    except Exception:
        raise AuthError("Cync API request failed") from None
    if not 200 <= status < 300:
        raise AuthError(f"Cync API request failed (HTTP {status})")
    return value


def _string(value: Any) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        result = str(value).strip()
        return result or None
    return None


def normalize_mac(value: Any) -> str | None:
    """Return a conventional uppercase colon-delimited MAC, if valid."""
    if not isinstance(value, str):
        return None
    # Permit only common MAC separators. Filtering arbitrary characters could
    # turn malformed input (for example, "G001122334455") into a valid MAC.
    if any(ch not in string.hexdigits + ":-." for ch in value):
        return None
    raw = value.translate(str.maketrans("", "", ":-."))
    if len(raw) != 12:
        return None
    raw = raw.upper()
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def _node(device_id: Any) -> int | None:
    value = _string(device_id)
    if not value:
        return None
    match = re.search(r"([0-9]{1,3})$", value)
    if not match:
        return None
    node = int(match.group(1), 10)
    return node if 1 <= node <= 255 else None


def _device_id(mesh_mac: str, node: int, bulb_mac: str) -> str:
    material = f"{mesh_mac}|{node}|{bulb_mac}".encode("ascii")
    return hashlib.sha256(material).hexdigest()[:16]


def _device_records(meshes: Any, token: str, transport: Transport | None) -> list[dict[str, Any]]:
    if not isinstance(meshes, list):
        raise AuthError("Cync did not return a valid mesh list")
    found: list[dict[str, Any]] = []
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        mesh_mac = normalize_mac(mesh.get("mac"))
        key = _string(mesh.get("access_key"))
        product_id = _string(mesh.get("product_id"))
        cloud_id = _string(mesh.get("id"))
        if not all((mesh_mac, key, product_id, cloud_id)):
            continue
        # `name` is the stable Keychain account, matching the native helper's
        # mesh credential convention. It contains no key or login credential.
        mesh_account = mesh_mac.replace(":", "")
        _keychain("store", {"name": mesh_account, "key": key})
        path = "product/{}/device/{}/property".format(
            urlparse.quote(product_id, safe=""), urlparse.quote(cloud_id, safe="")
        )
        properties = _call("GET", path, token=token, transport=transport)
        bulbs = properties.get("bulbsArray") if isinstance(properties, dict) else None
        if not isinstance(bulbs, list):
            continue
        for bulb in bulbs:
            if not isinstance(bulb, dict):
                continue
            node = _node(bulb.get("deviceID"))
            mac = normalize_mac(bulb.get("mac"))
            if node is None or mac is None:
                continue
            name = _string(bulb.get("displayName")) or "Cync bulb"
            kind = bulb.get("deviceType", 21)
            if not isinstance(kind, int) or isinstance(kind, bool):
                kind = 21
            record = {
                "id": _device_id(mesh_account, node, mac),
                "name": name,
                "mesh": mesh_account,
                "node": node,
                "mac": mac,
                "peripheral_id": None,
                "type": kind,
            }
            found.append(record)
    return found


def _interactive_email(email: str | None, prompt: bool) -> str:
    if prompt:
        if not email:
            print("Cync email: ", end="", file=sys.stderr, flush=True)
            email = input().strip()
    if not email or "\n" in email or "\r" in email:
        raise AuthError("A valid Cync email is required")
    return email


def _interactive_password_code(password: str | None, code: str | None, prompt: bool) -> tuple[str, str]:
    if not prompt and (password is None or code is None):
        raise AuthError("Password and emailed code are required")
    if prompt:
        if password is None:
            password = getpass.getpass("Cync password: ")
        if code is None:
            code = getpass.getpass("Cync emailed code: ").strip()
    if not password or not code:
        raise AuthError("Cync password and emailed code are required")
    return password, code


def login(*, email: str | None = None, password: str | None = None, code: str | None = None,
          prompt: bool = True, transport: Transport | None = None) -> list[dict[str, Any]]:
    """Interactively bootstrap mesh keys and bulb metadata.

    The keyword credential/transport arguments exist for isolated unit tests;
    the CLI calls this with no arguments, so secrets never enter argv.
    """
    email = _interactive_email(email, prompt)
    _call("POST", "two_factor/email/verifycode", body={
        "corp_id": CORP_ID, "email": email, "local_lang": "en-us",
    }, transport=transport)
    password, code = _interactive_password_code(password, code, prompt)
    # The verification-code prompt happens before asking for the code in the
    # normal interactive path; if caller supplied one, this is a test flow.
    auth = _call("POST", "user_auth/two_factor", body={
        "corp_id": CORP_ID,
        "email": email,
        "password": password,
        "two_factor": code,
        "resource": secrets.token_hex(8),
    }, transport=transport)
    if not isinstance(auth, dict) or not isinstance(auth.get("access_token"), str) or not auth["access_token"]:
        raise AuthError("Cync sign-in failed; check the account and emailed code")
    token = auth["access_token"]
    user_id = _string(auth.get("user_id"))
    if user_id is None or any(ch not in string.ascii_letters + string.digits + "-_" for ch in user_id):
        raise AuthError("Cync sign-in returned an invalid account response")
    encoded_user = urlparse.quote(user_id, safe="")
    meshes = _call("GET", f"user/{encoded_user}/subscribe/devices", token=token, transport=transport)
    try:
        records = _device_records(meshes, token, transport)
    finally:
        token = ""
        password = ""
    if not records:
        raise AuthError("Cync returned no supported bulbs for this account")
    data = config.load_config()
    merged = {item["id"]: item for item in data["devices"]}
    for record in records:
        merged[record["id"]] = record
    data["devices"] = list(merged.values())
    if data["default"] not in merged:
        data["default"] = records[0]["id"]
    config.save_config(data)
    return records


def status(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return safe account-key capability information, without secret values."""
    data = data or config.load_config()
    names = sorted({item["mesh"] for item in data.get("devices", [])})
    available = 0
    try:
        for name in names:
            result = _keychain("status", {"name": name})
            if isinstance(result, dict) and result.get("available") is True:
                available += 1
    except Exception:
        return {"configured_devices": len(data.get("devices", [])), "meshes": len(names),
                "keychain_available": False, "stored_mesh_keys": None}
    return {"configured_devices": len(data.get("devices", [])), "meshes": len(names),
            "keychain_available": True, "stored_mesh_keys": available}


def import_legacy() -> list[dict[str, Any]]:
    """Ask the native helper to migrate its legacy account without naming it."""
    result = _keychain("import-legacy", {})
    devices = result.get("devices") if isinstance(result, dict) else None
    if not isinstance(devices, list):
        raise AuthError("Legacy Keychain import did not return device metadata")
    data = config.load_config()
    merged = {item["id"]: item for item in data["devices"]}
    safe_records: list[dict[str, Any]] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        mac = normalize_mac(item.get("mac"))
        mesh = _string(item.get("mesh"))
        name = _string(item.get("name")) or "Cync bulb"
        node = item.get("node")
        if (not mac or not mesh or len(mesh.encode("utf-8", errors="ignore")) > 16
                or any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in mesh)
                or not isinstance(node, int) or isinstance(node, bool) or not 1 <= node <= 255):
            continue
        kind = item.get("type", 21)
        if not isinstance(kind, int) or isinstance(kind, bool):
            kind = 21
        record = {"id": _device_id(mesh, node, mac), "name": name, "mesh": mesh,
                  "node": node, "mac": mac, "peripheral_id": None,
                  "type": kind}
        merged[record["id"]] = record
        safe_records.append(record)
    if safe_records:
        data["devices"] = list(merged.values())
        if data["default"] not in merged:
            data["default"] = safe_records[0]["id"]
        config.save_config(data)
    return safe_records


def forget(mesh: str | None = None) -> dict[str, Any]:
    """Remove mesh keys from Keychain; with no mesh, remove all configured keys."""
    data = config.load_config()
    names = [mesh] if mesh is not None else sorted({item["mesh"] for item in data["devices"]})
    forgotten = 0
    for name in names:
        if not isinstance(name, str) or not name:
            raise AuthError("Invalid mesh selection")
        _keychain("forget", {"name": name})
        forgotten += 1
    return {"forgotten_mesh_keys": forgotten}
