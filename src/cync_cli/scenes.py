"""Private, atomic persistence for named light scenes."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"^[\w][\w .-]{0,47}$", re.UNICODE)


def validate_scene_name(name: str) -> str:
    cleaned = name.strip()
    if not _NAME_RE.fullmatch(cleaned) or cleaned in {".", ".."}:
        raise ValueError("Scene name must be 1–48 letters, numbers, spaces, dots, dashes, or underscores")
    return cleaned


def _path(home: Path | str) -> Path:
    return Path(home) / "scenes.json"


def _read(home: Path | str) -> dict[str, Any]:
    path = _path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": SCHEMA_VERSION, "scenes": {}}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read scenes: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("scenes"), dict):
        raise ValueError("Scene file has an unsupported or invalid format")
    return data


def list_scenes(home: Path | str) -> list[dict[str, Any]]:
    scenes = _read(home)["scenes"]
    return [dict(value, name=name) for name, value in sorted(scenes.items(), key=lambda item: item[0].casefold())]


def show_scene(name: str, home: Path | str) -> dict[str, Any]:
    name = validate_scene_name(name)
    try:
        scene = _read(home)["scenes"][name]
    except KeyError as exc:
        raise ValueError(f"No scene named {name!r}") from exc
    return dict(scene, name=name)


def save_scene(name: str, device_id: str, state: dict[str, Any], home: Path | str) -> dict[str, Any]:
    name = validate_scene_name(name)
    _validate_state(state)
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("A device id is required to save a scene")
    root = Path(home)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    data = _read(root)
    entry = {"device_id": device_id, "state": dict(state)}
    data["scenes"][name] = entry
    fd, temp_name = tempfile.mkstemp(prefix=".scenes-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, _path(root))
        try:
            _path(root).chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return dict(entry, name=name)


def delete_scene(name: str, home: Path | str) -> None:
    name = validate_scene_name(name)
    data = _read(home)
    if name not in data["scenes"]:
        raise ValueError(f"No scene named {name!r}")
    del data["scenes"][name]
    root = Path(home)
    fd, temp_name = tempfile.mkstemp(prefix=".scenes-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, _path(root))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _validate_state(state: dict[str, Any]) -> None:
    if not isinstance(state, dict):
        raise ValueError("Scene state must be an object")
    allowed = {"on", "brightness", "color_mode", "rgb", "white", "observed_at"}
    if set(state) - allowed:
        raise ValueError("Scene state contains unsupported fields")
    if "on" in state and not isinstance(state["on"], bool):
        raise ValueError("Scene on state must be true or false")
    if "color_mode" in state and state["color_mode"] not in {"rgb", "white"}:
        raise ValueError("Scene color mode must be rgb or white")
    if "observed_at" in state and type(state["observed_at"]) not in {int, float}:
        raise ValueError("Scene observation time must be numeric")
    for key in ("brightness", "white"):
        if key in state and (type(state[key]) is not int or not 0 <= state[key] <= 100):
            raise ValueError(f"Scene {key} must be an integer from 0 to 100")
    if "rgb" in state and (not isinstance(state["rgb"], list) or len(state["rgb"]) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in state["rgb"])):
        raise ValueError("Scene RGB must contain three integers from 0 to 255")
    if "rgb" in state and "white" in state:
        raise ValueError("Scene cannot contain both RGB and white values")
