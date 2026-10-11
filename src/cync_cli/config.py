"""Private, non-secret device metadata for the Cync CLI."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from . import backend


SCHEMA_VERSION = 1
CONFIG_FILENAME = "config.json"
_DEVICE_FIELDS = {"id", "name", "mesh", "node", "mac", "peripheral_id", "type"}


class ConfigError(ValueError):
    """Configuration is missing, malformed, or unsafe to use."""


def config_path() -> Path:
    return backend.data_home() / CONFIG_FILENAME


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise ConfigError("Could not secure the private data directory") from exc


def _validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError("Unsupported or invalid configuration")
    default = data.get("default")
    devices = data.get("devices")
    if default is not None and not isinstance(default, str):
        raise ConfigError("Invalid default device selection")
    if not isinstance(devices, list):
        raise ConfigError("Invalid device list")
    cleaned: list[dict[str, Any]] = []
    ids: set[str] = set()
    for entry in devices:
        if not isinstance(entry, dict) or not _DEVICE_FIELDS.issubset(entry):
            raise ConfigError("Invalid device entry")
        device = {key: entry[key] for key in _DEVICE_FIELDS}
        if not all(isinstance(device[key], str) and device[key] for key in ("id", "name", "mesh", "mac")):
            raise ConfigError("Invalid device identity")
        if device["id"] in ids:
            raise ConfigError("Duplicate device identifier")
        ids.add(device["id"])
        if not isinstance(device["node"], int) or isinstance(device["node"], bool) or not 1 <= device["node"] <= 255:
            raise ConfigError("Invalid device node")
        if not isinstance(device["type"], int) or isinstance(device["type"], bool):
            raise ConfigError("Invalid device type")
        if device["peripheral_id"] is not None and not isinstance(device["peripheral_id"], str):
            raise ConfigError("Invalid peripheral identifier")
        cleaned.append(device)
    if default is not None and default not in ids:
        raise ConfigError("Default device is not configured")
    return {"schema_version": SCHEMA_VERSION, "default": default, "devices": cleaned}


def load_config() -> dict[str, Any]:
    """Read config.json, returning an empty config when setup has not run."""
    path = config_path()
    try:
        if path.exists() or path.is_symlink():
            _secure_directory(path.parent)
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("Configuration path is not a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                os.chmod(path, 0o600)
        raw = path.read_text(encoding="utf-8")
    except ConfigError:
        raise
    except FileNotFoundError:
        return {"schema_version": SCHEMA_VERSION, "default": None, "devices": []}
    except OSError as exc:
        raise ConfigError("Could not read configuration") from exc
    try:
        return _validate(json.loads(raw))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ConfigError("Configuration file is malformed") from exc


def save_config(data: dict[str, Any]) -> None:
    """Atomically write validated metadata with private directory/file modes."""
    config = _validate(data)
    path = config_path()
    _secure_directory(path.parent)
    fd = -1
    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfigError("Could not save configuration") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def ensure_config() -> dict[str, Any]:
    config = load_config()
    if not config_path().exists():
        save_config(config)
    return config


def devices(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list((config or load_config())["devices"])


def get_default_device(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    config = config or load_config()
    selected = config.get("default")
    return next((item for item in config["devices"] if item["id"] == selected), None)


def resolve_device(config: dict[str, Any], selector: str | None = None) -> dict[str, Any]:
    if selector is None:
        selected = get_default_device(config)
        if selected is None:
            raise ConfigError("No Cync device is configured")
        return selected
    matches = [item for item in config["devices"] if item["id"] == selector or item["name"].casefold() == selector.casefold()]
    if not matches:
        raise ConfigError("No configured device matches that selection")
    if len(matches) > 1:
        raise ConfigError("Device name is ambiguous; use its device ID")
    return matches[0]


def get_device(device_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return resolve_device(config or load_config(), device_id)


def add_device(device: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    entries = [item for item in config["devices"] if item["id"] != device.get("id")]
    entries.append(device)
    config["devices"] = entries
    if config["default"] is None:
        config["default"] = device["id"]
    config = _validate(config)
    save_config(config)
    return config


def update_device(device_id: str, **changes: Any) -> dict[str, Any]:
    config = load_config()
    for item in config["devices"]:
        if item["id"] == device_id:
            item.update(changes)
            break
    else:
        raise ConfigError("No configured device matches that ID")
    config = _validate(config)
    save_config(config)
    return config


def remove_device(device_id: str) -> dict[str, Any]:
    config = load_config()
    config["devices"] = [item for item in config["devices"] if item["id"] != device_id]
    if config["default"] == device_id:
        config["default"] = config["devices"][0]["id"] if config["devices"] else None
    config = _validate(config)
    save_config(config)
    return config


def select_default(device_id: str) -> dict[str, Any]:
    config = load_config()
    if not any(item["id"] == device_id for item in config["devices"]):
        raise ConfigError("No configured device matches that ID")
    config["default"] = device_id
    save_config(config)
    return config
