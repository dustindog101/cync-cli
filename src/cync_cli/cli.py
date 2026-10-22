"""Human-friendly command line interface for local Cync BLE control."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from . import auth, backend, config
from .colors import NAMED_COLORS, format_rgb, parse_percent, parse_rgb
from . import scenes as scene_store


class CLIError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CLIArgumentParser(argparse.ArgumentParser):
    """Emit machine-readable parse errors when a caller requested JSON."""

    _json_requested_global = False

    def parse_args(self, args=None, namespace=None):
        values = list(sys.argv[1:] if args is None else args)
        self._json_requested = "--json" in values
        CLIArgumentParser._json_requested_global = self._json_requested
        return super().parse_args(_normalize_help(values), namespace)

    def error(self, message):
        if getattr(self, "_json_requested", False) or CLIArgumentParser._json_requested_global:
            command = self.prog.removeprefix("cync ")
            print(json.dumps({"schema_version": 1, "ok": False, "command": command,
                              "error": {"code": "invalid_input", "message": message}}, sort_keys=True))
            self.exit(2)
        if self.prog == "cync" and "invalid choice" in message:
            token = message.split("invalid choice:", 1)[1].split("(", 1)[0].strip()
            self.exit(2, f"cync: unknown command or color {token}; try `cync red`, `cync 35`, or `cync --help`\n")
        super().error(message)

    def format_help(self):
        if getattr(self, "_concise_root_help", False):
            return (
                "usage: cync [COMMAND [ARGS...]] [--json] [--device NAME]\n\n"
                "Control Cync lights with short commands.\n\n"
                "Everyday commands:\n"
                "  cync red [BRIGHTNESS]    Set a named or hex color\n"
                "  cync 35                  Set brightness; 0 turns the light off\n"
                "  cync on | off | status   Power and inspect the selected light\n"
                "  cync fade 70|on|off [TIME] Fade to brightness, on, or off\n"
                "  cync white [LEVEL]       Set white level (default 50)\n"
                "  cync save NAME           Save the current scene\n"
                "  cync scene NAME          Apply a saved scene\n"
                "  cync lights              List paired lights\n"
                "  cync use NAME|NUMBER     Select a light from its name or numbered list\n"
                "  cync login               Import paired lights from your account\n\n"
                "Examples: cync red 100 · cync '#33AAFF' · cync 35 · cync fade 70 8 · cync save Evening\n"
                "More: toggle, scenes, brightness, color, rgb, set, info, scan, watch, doctor, test, disconnect, shutdown, devices, auth\n"
                "Run `cync help` or `cync COMMAND help` for advanced options.\n"
            )
        return super().format_help()


def build_parser() -> argparse.ArgumentParser:
    parser = CLIArgumentParser(
        prog="cync",
        description="Control paired Cync Bluetooth lights locally from your Mac.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="print a stable JSON result")
    parser.add_argument("--device", help="device id or configured name (defaults to selected device)")
    parser._concise_root_help = True
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=CLIArgumentParser)

    for name in ("status", "info", "on", "off", "toggle", "doctor", "disconnect", "shutdown"):
        _add_output_options(sub.add_parser(name, help={"status": "show the fresh light state", "info": "show device identity and fresh state", "on": "turn the light on", "off": "turn the light off", "toggle": "toggle the light", "doctor": "check local setup, Bluetooth, and helper availability", "disconnect": "release the active light connection for another controller", "shutdown": "stop the background Bluetooth helper"}[name]))
    p = sub.add_parser("scan", help="discover nearby Cync Bluetooth devices")
    p.add_argument("--duration", type=_duration, default=8, metavar="SECONDS")
    _add_output_options(p)
    p = sub.add_parser("brightness", help="set brightness from 0 to 100 percent")
    p.add_argument("percent")
    _add_output_options(p)
    p = sub.add_parser("color", help="set a named or hexadecimal RGB color")
    p.add_argument("value", help="name, #RRGGBB, or R,G,B")
    p.add_argument("brightness", nargs="?", help="optional brightness from 0 to 100")
    _add_output_options(p)
    p = sub.add_parser("rgb", help="set red, green, and blue channels")
    p.add_argument("red"); p.add_argument("green"); p.add_argument("blue")
    _add_output_options(p)
    p = sub.add_parser("white", help="set tunable-white level from 0 to 100")
    p.add_argument("percent", nargs="?")
    _add_output_options(p)
    p = sub.add_parser("set", help="change multiple light settings in one command")
    p.add_argument("color_value", nargs="?", help="optional named or hexadecimal color")
    p.add_argument("brightness_value", nargs="?", help="optional brightness from 0 to 100")
    power = p.add_mutually_exclusive_group()
    power.add_argument("--on", dest="on", action="store_true", default=None)
    power.add_argument("--off", dest="off", action="store_true")
    p.add_argument("--brightness")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--color")
    group.add_argument("--white")
    _add_output_options(p)
    p = sub.add_parser("fade", help="fade to a brightness over time")
    p.add_argument("brightness", metavar="TARGET", help="brightness 0–100, on (previous brightness), off (0), status, or cancel")
    p.add_argument("--wait", action="store_true", help="wait for verified completion (default: return after the fade starts)")
    p.add_argument("duration_pos", nargs="?", type=_fade_duration, metavar="TIME",
                   help="0.2–600 seconds; accepts 60, 60s, or 1m (default: 2 seconds)")
    p.add_argument("--duration", dest="duration_flag", type=_fade_duration, metavar="TIME",
                   help="same duration as TIME; specify only one")
    _add_output_options(p)
    p = sub.add_parser("watch", help="print fresh state changes until interrupted")
    p.add_argument("--interval", type=_watch_interval, default=2.0, metavar="SECONDS")
    p.add_argument("--count", type=_positive_count, metavar="POLL_COUNT", help="stop after this many polls (default: run until interrupted)")
    _add_output_options(p)
    p = sub.add_parser("help", help="show help for cync commands")
    p.add_argument("help_command", nargs="*", metavar="COMMAND", help="command path to describe, such as scene save")
    _add_output_options(p)
    p = sub.add_parser("test", help="run a bounded control check and restore the starting state")
    p.add_argument("--brightness", type=parse_percent, default=23)
    _add_output_options(p)

    scenes = sub.add_parser("scene", help="save and recall named light setups")
    scene_sub = scenes.add_subparsers(dest="scene_command", required=True, parser_class=CLIArgumentParser)
    _add_output_options(scene_sub.add_parser("list", help="list saved scenes"))
    p = scene_sub.add_parser("save", help="save the currently reported light state")
    p.add_argument("name")
    _add_output_options(p)
    p = scene_sub.add_parser("show", help="show a saved scene")
    p.add_argument("name")
    _add_output_options(p)
    p = scene_sub.add_parser("apply", help="apply a saved scene and verify the result")
    p.add_argument("name")
    _add_output_options(p)
    p = scene_sub.add_parser("delete", help="delete a saved scene")
    p.add_argument("name")
    _add_output_options(p)

    devices = sub.add_parser("devices", help="manage configured light profiles")
    device_sub = devices.add_subparsers(dest="devices_command", required=True, parser_class=CLIArgumentParser)
    _add_output_options(device_sub.add_parser("list", help="list paired devices"))
    p = device_sub.add_parser("select", help="choose the default device")
    p.add_argument("selector")
    _add_output_options(p)
    p = device_sub.add_parser("use", help="select a light by name or its numbered list position")
    p.add_argument("selector")
    _add_output_options(p)
    p = device_sub.add_parser("rename", help="rename a device profile")
    p.add_argument("selector"); p.add_argument("name")
    _add_output_options(p)
    p = device_sub.add_parser("remove", help="remove a device profile")
    p.add_argument("selector")
    _add_output_options(p)

    authentication = sub.add_parser("auth", help="manage cloud pairing and saved device access")
    auth_sub = authentication.add_subparsers(dest="auth_command", required=True, parser_class=CLIArgumentParser)
    for name, help_text in (("login", "sign in and import paired bulb records"), ("status", "show safe authentication capability status"), ("import-legacy", "import saved credentials from the previous app"), ("forget", "remove the selected device's saved key")):
        _add_output_options(auth_sub.add_parser(name, help=help_text))
    return parser


def _numeric_token(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


_ALIASES = {
    "lights": ["devices", "list"],
    "scenes": ["scene", "list"],
    "login": ["auth", "login"],
    "save": ["scene", "save"],
    "use": ["devices", "use"],
}


def _normalize_help(argv: list[str]) -> list[str]:
    """Accept COMMAND help at every parser depth without touching argument data."""
    args = list(argv)
    index = 0
    while index < len(args):
        if args[index] == "--device":
            index += 2
        elif args[index] == "--json" or args[index].startswith("--device="):
            index += 1
        else:
            break
    if index >= len(args) or args[index] == "help":
        return args
    # Walk commands, including aliases, before interpreting a bare help word.
    parser = build_parser()
    path = []
    output_options = []
    cursor = index
    while cursor < len(args):
        token = args[cursor]
        if token == "--device" and cursor + 1 < len(args):
            output_options += args[cursor:cursor + 2]
            cursor += 2
            continue
        if token == "--json" or token.startswith("--device="):
            output_options.append(token)
            cursor += 1
            continue
        if token.startswith("-"):
            option_name = token.split("=", 1)[0]
            action = parser._option_string_actions.get(option_name)
            if action is not None:
                cursor += 1
                if action.nargs != 0 and "=" not in token:
                    cursor += 1
                continue
        if token == "help":
            return args[:index] + output_options + ["help"] + path + args[cursor + 1:]
        if path == ["fade"] and token in {"status", "cancel"}:
            path.append(token)
            cursor += 1
            continue
        action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        choices = getattr(action, "choices", {}) or {}
        names = _ALIASES.get(token, [token]) if cursor == index else [token]
        for name in names:
            if name not in choices:
                if cursor == index and cursor + 1 < len(args) and args[cursor + 1] == "help":
                    if _numeric_token(token):
                        return args[:index] + ["help", "brightness"] + args[cursor + 2:]
                    try:
                        parse_rgb(token)
                    except ValueError:
                        return args
                    return args[:index] + ["help", "color"] + args[cursor + 2:]
                return args
            parser = choices[name]
            path.append(name)
            action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
            choices = getattr(action, "choices", {}) or {}
        cursor += 1
    return args


def _normalize_shortcuts(argv: list[str]) -> list[str]:
    """Expand short everyday words before argparse validates the full CLI."""
    args = list(argv)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--device":
            index += 2
            continue
        if token.startswith("--device=") or token == "--json" or token == "-h" or token == "--help":
            index += 1
            continue
        break
    if index >= len(args):
        return args
    token = args[index]
    if token in _ALIASES:
        expanded = _ALIASES[token]
        return args[:index] + expanded + args[index + 1 :]
    if token == "scene" and index + 1 < len(args) and args[index + 1] not in {"list", "save", "show", "apply", "delete", "help", "--help", "-h"}:
        return args[:index] + ["scene", "apply"] + args[index + 1 :]
    known = {"status", "info", "on", "off", "toggle", "doctor", "disconnect", "shutdown", "scan", "brightness", "color", "rgb", "white", "set", "fade", "watch", "test", "help", "scene", "devices", "auth"}
    if token in known:
        return args
    # A numeric token is always a brightness shortcut, even when six digits
    # could also be interpreted as a raw hexadecimal color.
    if _numeric_token(token):
        return args[:index] + ["brightness", token] + args[index + 1 :]
    try:
        parse_rgb(token)
    except ValueError:
        return args
    tail = args[index + 1 :]
    brightness_index = None
    cursor = 0
    while cursor < len(tail):
        if tail[cursor] == "--device":
            cursor += 2
            continue
        if tail[cursor] == "--json" or tail[cursor].startswith("--device="):
            cursor += 1
            continue
        if _numeric_token(tail[cursor]):
            brightness_index = cursor
        break
    if brightness_index is None:
        expanded = ["set", "--on", "--color", token] + tail
    else:
        brightness = tail[brightness_index]
        rest = tail[:brightness_index] + tail[brightness_index + 1 :]
        expanded = ["set", "--color", token, "--brightness", brightness] + rest
    return args[:index] + expanded


def _help_text(topic: str | list[str] | None = None) -> str:
    """Return the root help, or the help for one named command."""
    parser = build_parser()
    if not topic:
        return parser.format_help()
    # An alias such as "lights" points at a real command; describe that one.
    topics = [topic] if isinstance(topic, str) else list(topic)
    path = _ALIASES.get(topics[0], [topics[0]]) + topics[1:]
    if len(path) == 2 and path[0] == "fade" and path[1] in {"status", "cancel"}:
        description = ("Show the latest fade operation and its verified endpoint or error."
                       if path[1] == "status" else "Stop further fade writes and leave the last applied level.")
        return f"usage: cync fade {path[1]} [--json]\n\n{description}\n"
    for name in path:
        action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        choices = getattr(action, "choices", {}) or {}
        if name not in choices:
            raise CLIError("invalid_command", f"Unknown command {topic!r}. Run `cync help` to list the commands.")
        parser = choices[name]
    return parser.format_help()


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS preserves values supplied before the subcommand.
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print a stable JSON result")
    parser.add_argument("--device", default=argparse.SUPPRESS, help="device id or configured name")


def _duration(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("duration must be from 1 to 30 seconds") from exc
    if not 1 <= result <= 30:
        raise argparse.ArgumentTypeError("duration must be from 1 to 30 seconds")
    return result


_DURATION_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0}


def _fade_duration(value: str) -> float:
    """Read a fade duration in seconds. Accepts 8, 30s, 5m, or 1.5m."""
    text = value.strip().lower()
    multiplier = 1.0
    if text and text[-1] in _DURATION_UNITS:
        multiplier = _DURATION_UNITS[text[-1]]
        text = text[:-1].strip()
    try:
        result = float(text) * multiplier
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fade duration must be a number of seconds, or a value with s, m, or h, such as 8, 30s, 5m, or 1.5m") from exc
    if not 0.2 <= result <= 600:
        raise argparse.ArgumentTypeError("fade duration must be from 0.2 to 600 seconds (10 minutes)")
    return result


def _watch_interval(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("watch interval must be from 0.5 to 60 seconds") from exc
    if not 0.5 <= result <= 60:
        raise argparse.ArgumentTypeError("watch interval must be from 0.5 to 60 seconds")
    return result


def _positive_count(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("count must be an integer from 1 to 10000") from exc
    if not 1 <= result <= 10000:
        raise argparse.ArgumentTypeError("count must be from 1 to 10000")
    return result


def _config() -> dict[str, Any]:
    try:
        return config.load_config()
    except Exception as exc:
        raise CLIError("config_error", str(exc)) from exc


def _device(selector: str | None, *, auto_login: bool = False) -> dict[str, Any]:
    data = _config()
    if auto_login and not data.get("devices") and sys.stdin.isatty():
        try:
            auth.login()
            data = config.load_config()
        except Exception as exc:
            raise CLIError("auth_required", str(exc)) from exc
    try:
        device = config.resolve_device(data, selector) if selector else config.get_default_device(data)
    except (KeyError, ValueError, LookupError) as exc:
        raise CLIError("device_not_found", str(exc)) from exc
    if not device:
        raise CLIError("no_device", "No paired device is selected. Run `cync auth login` or `cync devices select` first.")
    return device


def _request(command: str, device: dict[str, Any] | None = None, values: dict[str, Any] | None = None, *, require_verified: bool = True, timeout: float = 35) -> dict[str, Any]:
    try:
        response = backend.request(command, device=device, values=values or {}, timeout=timeout)
    except Exception as exc:
        code = getattr(exc, "code", "request_failed")
        message = getattr(exc, "message", str(exc))
        raise CLIError(str(code), str(message)) from exc
    if not isinstance(response, dict) or response.get("schema_version") != 1:
        raise CLIError("invalid_response", "The Bluetooth helper returned an invalid response")
    if response.get("ok") is not True:
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        raise CLIError(str(error.get("code", "request_failed")), str(error.get("message", "The request failed")))
    if require_verified and response.get("verified") is not True:
        raise CLIError("unverified_state", "The helper did not confirm the light's current state")
    return response


def _home():
    return backend.data_home()


def _result(command: str, *, device: dict[str, Any] | None = None, response: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": 1, "ok": True, "command": command}
    if device:
        result["device"] = {key: device[key] for key in ("id", "name", "mesh", "node", "mac") if key in device}
    if response:
        for key in ("verified", "state", "info", "devices", "bluetooth_state", "authorization", "accepted", "operation_id", "operation"):
            if key in response:
                result[key] = response[key]
    result.update(extra)
    return result


def _control_args(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    command = args.command
    if command in {"on", "off", "toggle"}:
        if command == "on": return "set", {"on": True}
        if command == "off": return "set", {"on": False}
        return "set", {"on": not bool(_status(args.device, auto_login=True).get("state", {}).get("on"))}
    if command == "brightness": return "set", {"brightness": parse_percent(args.percent, name="brightness")}
    if command == "color":
        rgb = list(_parse_cli_color(args.value))
        if args.brightness is None:
            return "set", {"rgb": rgb}
        brightness = parse_percent(args.brightness, name="brightness")
        return "set", {"rgb": rgb, "brightness": brightness}
    if command == "rgb": return "set", {"rgb": list(parse_rgb(f"{args.red},{args.green},{args.blue}"))}
    if command == "white":
        if args.percent is None:
            return "set", {"white": 50, "on": True}
        return "set", {"white": parse_percent(args.percent, name="white")}
    if command == "set":
        values: dict[str, Any] = {}
        positional_color = getattr(args, "color_value", None)
        positional_brightness = getattr(args, "brightness_value", None)
        if positional_color is not None or positional_brightness is not None:
            if args.color is not None or args.white is not None or args.brightness is not None or args.on is not None or args.off:
                raise CLIError("invalid_input", "Use positional color/brightness or the --color/--white/--brightness flags, not both")
            if positional_color is None:
                raise CLIError("invalid_input", "A positional brightness shortcut needs a value; try `cync set 35`")
            if _numeric_token(positional_color):
                if positional_brightness is not None:
                    raise CLIError("invalid_input", "A numeric value is brightness; add a color name before it")
                values["brightness"] = parse_percent(positional_color, name="brightness")
            else:
                values["rgb"] = list(_parse_cli_color(positional_color))
                if positional_brightness is not None:
                    values["brightness"] = parse_percent(positional_brightness, name="brightness")
            return "set", values
        if args.on is not None: values["on"] = True
        if args.off: values["on"] = False
        if args.brightness is not None: values["brightness"] = parse_percent(args.brightness, name="brightness")
        if args.color is not None: values["rgb"] = list(_parse_cli_color(args.color))
        if args.white is not None: values["white"] = parse_percent(args.white, name="white")
        if not values: raise CLIError("invalid_input", "Choose at least one of --on, --off, --brightness, --color, or --white")
        if values.get("brightness") == 0 and values.get("on") is True:
            raise CLIError("invalid_input", "Brightness 0 turns the light off; remove --on")
        return "set", values
    if command == "fade":
        positional = getattr(args, "duration_pos", None)
        flag = getattr(args, "duration_flag", None)
        if positional is not None and flag is not None:
            raise CLIError("invalid_input", "Specify fade duration once: use a positional value or --duration")
        duration = positional if positional is not None else flag if flag is not None else 2.0
        target = args.brightness.lower()
        if target == "on":
            return "fade", {"restore_brightness": True, "duration": duration}
        brightness = 0 if target == "off" else parse_percent(args.brightness, name="brightness")
        return "fade", {"brightness": brightness, "duration": duration}
    raise CLIError("invalid_command", f"Unsupported command {command}")


def _parse_cli_color(value: str) -> tuple[int, int, int]:
    if _numeric_token(value):
        raise CLIError("invalid_input", "Numeric values are brightness; prefix hexadecimal colors with #")
    try:
        return parse_rgb(value)
    except ValueError as exc:
        raise CLIError("invalid_color", f"Unknown or invalid color {value!r}; try `cync red`, `cync '#33AAFF'`, or `cync 35`.") from exc


def _status(selector: str | None, *, auto_login: bool = False) -> dict[str, Any]:
    device = _device(selector, auto_login=auto_login)
    response = _request("status", device)
    return _result("status", device=device, response=response)


def _run_test(device: dict[str, Any], brightness: int) -> dict[str, Any]:
    before_response = _request("status", device)
    before = before_response.get("state")
    if not isinstance(before, dict):
        raise CLIError("invalid_response", "Status did not include the reported light state")
    test_check: dict[str, Any]
    try:
        # Once dispatched, a timeout may still mean the light changed. Always
        # attempt restoration after sending the test command.
        check = _request("set", device, {"brightness": brightness})
        after = check.get("state")
        if not isinstance(after, dict) or after.get("brightness") != brightness or after.get("on") is not (brightness > 0):
            raise CLIError("verification_failed", "The test level did not match the requested brightness")
        test_check = {"ok": True, "requested_brightness": brightness, "observed_brightness": after.get("brightness"), "observed_on": after.get("on")}
    finally:
        # Restore best-effort after the test has made a change. RGB state is
        # quantized by the bulb; the prior reported values are the target.
        try:
            restore = {"on": bool(before.get("on"))}
            if before.get("on"):
                restore["brightness"] = max(1, min(100, int(before.get("brightness", 100))))
                if before.get("color_mode") == "rgb" and isinstance(before.get("rgb"), list):
                    restore["rgb"] = before["rgb"]
                elif before.get("color_mode") == "white" and type(before.get("white")) is int:
                    restore["white"] = before["white"]
            restored = _request("set", device, restore)
            if not _states_match(before, restored.get("state")):
                raise CLIError("restore_mismatch", "The restored state differs from the starting reported state")
        except Exception as exc:
            raise CLIError("restore_failed", f"Test completed but restoring the starting state failed: {exc}") from exc
    return _result("test", device=device, response=restored, test={"check": test_check, "restored": True})


def _states_match(expected: Any, actual: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    if bool(expected.get("on")) != bool(actual.get("on")):
        return False
    if not expected.get("on"):
        return True  # Off reports may discard the previous brightness/color.
    if abs(int(expected.get("brightness", -1000)) - int(actual.get("brightness", -2000))) > 1:
        return False
    mode = expected.get("color_mode")
    if mode != actual.get("color_mode"):
        return False
    if mode == "rgb":
        left, right = expected.get("rgb"), actual.get("rgb")
        return (isinstance(left, list) and isinstance(right, list) and len(left) == len(right) == 3
                and all(abs(a - b) <= tolerance for a, b, tolerance in zip(left, right, (37, 37, 85))))
    if mode == "white":
        try:
            return abs(int(expected["white"]) - int(actual["white"])) <= 1
        except (KeyError, TypeError, ValueError):
            return False
    return True


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command == "help":
        return _result("help", help=_help_text(getattr(args, "help_command", None)))
    if command == "auth":
        if args.auth_command == "login":
            devices = auth.login()
            return _result("auth login", devices=devices)
        if args.auth_command == "status": return _result("auth status", status=auth.status(_config()))
        if args.auth_command == "import-legacy": return _result("auth import-legacy", devices=auth.import_legacy())
        device = _device(args.device)
        _request("disconnect", require_verified=False)
        return _result("auth forget", result=auth.forget(device.get("mesh")))
    if command == "devices":
        data = _config()
        if args.devices_command == "list": return _result("devices list", devices=data.get("devices", []), default=data.get("default"))
        try:
            if args.devices_command == "use" and args.selector.isdecimal():
                index = int(args.selector) - 1
                if index < 0 or index >= len(data.get("devices", [])):
                    raise CLIError("device_not_found", f"Light number must be from 1 to {len(data.get('devices', []))}")
                device = data["devices"][index]
            else:
                device = config.resolve_device(data, args.selector)
            if args.devices_command in {"select", "use"}:
                config.select_default(device["id"])
                return _result(f"devices {args.devices_command}", selected=device["id"], name=device.get("name"))
            if args.devices_command == "rename": config.update_device(device["id"], name=args.name); return _result("devices rename", device_id=device["id"], name=args.name)
            config.remove_device(device["id"]); return _result("devices remove", device_id=device["id"])
        except (KeyError, ValueError, LookupError) as exc: raise CLIError("device_not_found", str(exc)) from exc
    if command == "doctor":
        try:
            backend.ensure_helper()
        except Exception as exc:
            return _result("doctor", config={"devices": len(_config().get("devices", []))}, helper={"available": False, "error": str(exc)})
        try:
            response = _request("doctor", require_verified=False)
        except Exception as exc:
            return _result("doctor", config={"devices": len(_config().get("devices", []))}, helper={"available": True}, bluetooth={"available": False, "error": str(exc)})
        data = _config()
        return _result("doctor", config={"devices": len(data.get("devices", []))}, helper={"available": True}, authentication=auth.status(data), bluetooth={"available": True, "state": response.get("bluetooth_state"), "authorization": response.get("authorization")})
    if command in {"disconnect", "shutdown"}:
        response = _request(command, require_verified=False)
        return _result(command, response=response)
    if command == "scan":
        response = _request("scan", values={"duration": args.duration}, require_verified=False)
        return _result("scan", response=response)
    if command == "scene":
        if args.scene_command == "list": return _result("scene list", scenes=scene_store.list_scenes(_home()))
        if args.scene_command == "show": return _result("scene show", scene=scene_store.show_scene(args.name, _home()))
        if args.scene_command == "delete": scene_store.delete_scene(args.name, _home()); return _result("scene delete", name=args.name)
        device = _device(args.device)
        if args.scene_command == "save":
            response = _request("status", device)
            state = response.get("state")
            if not isinstance(state, dict): raise CLIError("invalid_response", "Status did not include the reported light state")
            scene = scene_store.save_scene(args.name, device["id"], state, _home())
            return _result("scene save", device=device, scene=scene)
        scene = scene_store.show_scene(args.name, _home())
        if scene.get("device_id") != device.get("id"):
            raise CLIError("scene_device_mismatch", f"Scene {args.name!r} belongs to another device profile")
        state = scene["state"]
        values = {key: state[key] for key in ("on", "brightness", "rgb", "white") if key in state}
        response = _request("set", device, values)
        return _result("scene apply", device=device, response=response, scene=args.name)
    if command == "watch":
        device = _device(args.device)
        prior = None
        polls = 0
        try:
            while args.count is None or polls < args.count:
                result = _status(args.device)
                polls += 1
                state = result.get("state")
                snapshot = {key: value for key, value in state.items() if key != "observed_at"} if isinstance(state, dict) else state
                if snapshot != prior:
                    if args.json: print(json.dumps(result, sort_keys=True), flush=True)
                    else: print(_human_state(result), flush=True)
                    prior = snapshot
                if args.count is None or polls < args.count:
                    time.sleep(args.interval)
        except KeyboardInterrupt:
            return _result("watch", device=device, stopped=True)
        return _result("watch", device=device, stopped=True, polls=polls)
    if command == "test":
        return _run_test(_device(args.device, auto_login=True), args.brightness)
    if command in {"status", "info"}:
        device = _device(args.device, auto_login=True)
        response = _request(command, device)
        return _result(command, device=device, response=response)
    if command == "fade" and args.brightness in {"status", "cancel"}:
        if args.duration_pos is not None or args.duration_flag is not None or args.wait:
            raise CLIError("invalid_input", "fade status/cancel does not accept a duration or --wait")
        response = _request("fade-" + args.brightness, require_verified=False)
        return _result("fade " + args.brightness, response=response)
    native_command, values = _control_args(args)
    device = _device(args.device, auto_login=True)
    # Native fade deadlines retain the full ramp plus connection/readback
    # headroom; --wait uses that same budget for its caller. The helper caps its own deadline, so the two must
    # grow together: helper cap 700s = 600s ramp + 35s overhead + 65s slack.
    timeout = values["duration"] + 35 if native_command == "fade" else 35
    background = native_command == "fade" and not args.wait
    if background:
        values["background"] = True
    response = _request(native_command, device, values, timeout=timeout, require_verified=not background)
    if background and (response.get("accepted") is not True or response.get("verified") is not False
                       or not isinstance(response.get("operation_id"), str) or not response["operation_id"]):
        raise CLIError("invalid_response", "The helper did not acknowledge starting the fade. Refresh the installed helper.")
    return _result(command, device=device, response=response, requested=values)


def _human_state(result: dict[str, Any]) -> str:
    state = result.get("state")
    if not isinstance(state, dict): return json.dumps(result, indent=2, sort_keys=True)
    name = result.get("device", {}).get("name", "Light")
    power = "on" if state.get("on") else "off"
    bits = [f"{name}: {power}"]
    if "brightness" in state: bits.append(f"{state['brightness']}%")
    if state.get("color_mode") == "rgb" and isinstance(state.get("rgb"), list):
        rgb = tuple(state["rgb"])
        name = next((name for name, color in NAMED_COLORS.items() if color == rgb), None)
        bits.append(name if name is not None else format_rgb(state["rgb"]))
    elif state.get("color_mode") == "white" and "white" in state: bits.append(f"white (tone {state['white']})")
    return " · ".join(bits)


def _print_human(result: dict[str, Any]) -> None:
    command = result.get("command", "")
    if command == "help":
        print(result.get("help", ""), end="")
        return
    if result.get("accepted"):
        print("Fade started. Use `cync fade status` to check or `cync fade cancel` to stop.")
        return
    if command in {"fade status", "fade cancel"}:
        operation = result.get("operation")
        if not isinstance(operation, dict):
            print("No fade recorded.")
        else:
            print("Fade: " + str(operation.get("status", "unknown")))
            if isinstance(operation.get("state"), dict):
                print(_human_state(operation))
            if isinstance(operation.get("error"), dict):
                print(operation["error"].get("message", ""))
        return
    if "state" in result:
        print(_human_state(result))
        if command == "info" and isinstance(result.get("info"), dict):
            info = result["info"]
            for key, label in (("firmware", "Firmware"), ("hardware", "Hardware"), ("type", "Type"), ("manufacturer", "Manufacturer")):
                value = info.get(key)
                if value not in (None, ""):
                    print(f"{label}: {value}")
        if command == "test":
            check = result.get("test", {}).get("check", {})
            print(f"Test level: {'passed' if check.get('ok') else 'failed'} ({check.get('observed_brightness')}%) · starting state restored")
    elif command == "auth status":
        status = result.get("status", {})
        print("Keychain: " + ("available" if status.get("keychain_available") else "unavailable"))
        print(f"Configured devices: {status.get('configured_devices', 0)} · meshes: {status.get('meshes', 0)}")
        stored = status.get("stored_mesh_keys")
        if isinstance(stored, int):
            print(f"Stored mesh keys: {stored}")
    elif command in {"auth login", "auth import-legacy"}:
        devices = result.get("devices", [])
        print(f"Imported {len(devices)} paired bulb(s).")
        for device in devices:
            print(f"  {device.get('name', 'Unnamed Cync bulb')}")
    elif command == "auth forget":
        count = result.get("result", {}).get("forgotten_mesh_keys", 0)
        print(f"Removed {count} saved mesh key(s).")
    elif command == "devices list":
        devices = result.get("devices", [])
        if not devices: print("No paired Cync devices. Run `cync auth login` to import your paired bulbs.")
        for index, device in enumerate(devices, start=1):
            marker = "*" if device.get("id") == result.get("default") else " "
            print(f"{marker} {index}. {device.get('name', 'Unnamed')}")
    elif command in {"devices select", "devices use"}:
        print(f"Using {result.get('name', 'selected light')}.")
    elif command == "scene save":
        print(f"Saved scene {result.get('scene', {}).get('name', '')!r}.")
    elif command == "scene delete":
        print(f"Deleted scene {result.get('name', '')!r}.")
    elif command == "scene list":
        if not result.get("scenes"): print("No saved scenes.")
        try:
            names = {item["id"]: item["name"] for item in config.load_config().get("devices", [])}
        except Exception:
            names = {}
        for scene in result.get("scenes", []):
            light = names.get(scene.get("device_id"), "Unknown light")
            print(f"{scene['name']}  ·  {light}")
    elif command == "scan":
        for item in result.get("devices", []):
            rssi = item.get("rssi")
            signal = f"{rssi} dBm" if isinstance(rssi, (int, float)) else "signal unknown"
            identifier = item.get("peripheral_id", "unknown")
            print(f"{item.get('name', 'Cync device')}  {signal}  [{identifier}]")
        if not result.get("devices"): print("No devices found nearby.")
    elif command == "doctor":
        helper = result.get("helper", {})
        print("Local helper: " + ("ready" if helper.get("available") else f"unavailable ({helper.get('error', 'unknown error')})"))
        print(f"Configured devices: {result.get('config', {}).get('devices', 0)}")
        bluetooth = result.get("bluetooth", {})
        if bluetooth.get("available"):
            states = {0: "unknown", 1: "resetting", 2: "unsupported", 3: "unauthorized", 4: "powered off", 5: "powered on"}
            access = {0: "not determined", 1: "restricted", 2: "denied", 3: "allowed always"}
            print(f"Bluetooth: {states.get(bluetooth.get('state'), bluetooth.get('state'))} · access: {access.get(bluetooth.get('authorization'), bluetooth.get('authorization'))}")
        elif bluetooth:
            print(f"Bluetooth: unavailable ({bluetooth.get('error', 'unknown error')})")
    elif command == "disconnect":
        print("Bluetooth connection released.")
    elif command == "shutdown":
        print("Bluetooth helper stopped.")
    else:
        print(f"Done: {command}.")


def _command_name(args: argparse.Namespace) -> str:
    for attr in ("scene_command", "devices_command", "auth_command"):
        value = getattr(args, attr, None)
        if value:
            return f"{args.command} {value}"
    return str(getattr(args, "command", "unknown"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        args = parser.parse_args(_normalize_shortcuts(_normalize_help(raw_argv)))
        if not args.command:
            data = _config()
            if not data.get("devices"):
                hint = "No paired lights yet. Run `cync login` to import your paired bulbs, or `cync --help` for command examples."
                result = {"schema_version": 1, "ok": True, "command": "status", "devices": [], "hint": hint}
                if args.json: print(json.dumps(result, sort_keys=True))
                else: print(hint)
                return 0
            args.command = "status"
        result = dispatch(args)
        if args.command != "watch":
            if args.json: print(json.dumps(result, sort_keys=True))
            else: _print_human(result)
        return 0
    except CLIError as exc:
        result = {"schema_version": 1, "ok": False, "command": _command_name(args) if "args" in locals() else "unknown", "error": {"code": exc.code, "message": exc.message}}
        if "args" in locals() and getattr(args, "json", False): print(json.dumps(result, sort_keys=True))
        else: print(f"cync: {exc.message}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        result = {"schema_version": 1, "ok": False, "command": _command_name(args) if "args" in locals() else "unknown", "error": {"code": "invalid_input", "message": str(exc)}}
        if "args" in locals() and getattr(args, "json", False): print(json.dumps(result, sort_keys=True))
        else: print(f"cync: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        message = getattr(exc, "message", str(exc))
        code = getattr(exc, "code", "operation_failed")
        result = {"schema_version": 1, "ok": False, "command": _command_name(args) if "args" in locals() else "unknown", "error": {"code": str(code), "message": str(message)}}
        if "args" in locals() and getattr(args, "json", False): print(json.dumps(result, sort_keys=True))
        else: print(f"cync: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
