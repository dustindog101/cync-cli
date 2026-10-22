from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from cync_cli import cli, colors, scenes


class ColorTests(unittest.TestCase):
    def test_named_hex_and_channel_colors(self):
        self.assertEqual(colors.parse_rgb("blue"), (0, 0, 255))
        self.assertEqual(colors.parse_rgb("#a1B2c3"), (161, 178, 195))
        self.assertEqual(colors.parse_rgb("1, 2, 3"), (1, 2, 3))
        self.assertEqual(colors.format_rgb((161, 178, 195)), "#A1B2C3")

    def test_rejects_out_of_range_or_malformed_colors(self):
        for value in ("#123", "not-a-color", "1,2,256", "-1,0,0", "1,2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                colors.parse_rgb(value)
        with self.assertRaises(ValueError):
            colors.parse_percent("101", name="brightness")


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_scene_roundtrip_and_private_file_modes(self):
        state = {"on": True, "brightness": 47, "color_mode": "rgb", "rgb": [36, 144, 219], "observed_at": 1.25}
        saved = scenes.save_scene("Reading", "device-a", state, self.home)
        self.assertEqual(saved["device_id"], "device-a")
        self.assertEqual(scenes.show_scene("Reading", self.home)["state"], state)
        self.assertEqual(scenes.list_scenes(self.home)[0]["name"], "Reading")
        if os.name == "posix":
            self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.home / "scenes.json").stat().st_mode & 0o777, 0o600)
        scenes.delete_scene("Reading", self.home)
        self.assertEqual(scenes.list_scenes(self.home), [])

    def test_rejects_bad_scene_names_and_states(self):
        with self.assertRaises(ValueError): scenes.validate_scene_name("../unsafe")
        with self.assertRaises(ValueError): scenes.save_scene("bad", "a", {"rgb": [1, 2, 300]}, self.home)

    def test_failed_atomic_replace_preserves_old_scene(self):
        scenes.save_scene("Desk", "device-a", {"on": True, "brightness": 25}, self.home)
        with patch("cync_cli.scenes.os.replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                scenes.save_scene("Desk", "device-a", {"on": True, "brightness": 90}, self.home)
        self.assertEqual(scenes.show_scene("Desk", self.home)["state"]["brightness"], 25)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["scenes.json"])


DEVICE = {"id": "light-1", "name": "Desk", "mesh": "mesh-1", "node": 7, "mac": "AA:BB:CC:DD:EE:FF"}
BASE_STATE = {"on": True, "brightness": 61, "color_mode": "rgb", "rgb": [73, 146, 219], "observed_at": 10.0}


class CLITests(unittest.TestCase):
    def setUp(self):
        self.config_patcher = patch.object(cli.config, "load_config", return_value={"schema_version": 1, "default": DEVICE["id"], "devices": [DEVICE]})
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)
        self.resolve_patcher = patch.object(cli.config, "get_default_device", return_value=DEVICE)
        self.resolve_patcher.start()
        self.addCleanup(self.resolve_patcher.stop)

    def test_parser_limits_and_set_conflict(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit): parser.parse_args(["fade", "25", "--duration", "601"])
        with self.assertRaises(SystemExit): parser.parse_args(["set", "--on", "--off"])
        args = parser.parse_args(["set", "--on", "--brightness", "0"])
        with self.assertRaises(cli.CLIError): cli._control_args(args)

    def test_global_options_work_before_or_after_the_command(self):
        parser = cli.build_parser()
        for argv in (["--json", "status"], ["status", "--json"]):
            with self.subTest(argv=argv):
                self.assertTrue(parser.parse_args(argv).json)
        for argv in (["--device", "Desk", "status"], ["status", "--device", "Desk"]):
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).device, "Desk")

    def test_no_arguments_with_no_devices_shows_onboarding_without_network_or_login(self):
        output = StringIO()
        empty = {"schema_version": 1, "default": None, "devices": []}
        with patch.object(cli.config, "load_config", return_value=empty), patch.object(cli.backend, "request") as request, patch.object(cli.auth, "login") as login, redirect_stdout(output):
            code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn("No paired lights yet", output.getvalue())
        request.assert_not_called()
        login.assert_not_called()

    def test_no_arguments_with_configured_device_returns_status(self):
        output = StringIO()
        response = {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
        with patch.object(cli.config, "load_config", return_value={"schema_version": 1, "default": DEVICE["id"], "devices": [DEVICE]}), patch.object(cli.backend, "request", return_value=response) as request, redirect_stdout(output):
            self.assertEqual(cli.main([]), 0)
        request.assert_called_once()
        self.assertIn("Desk: on", output.getvalue())

    def test_root_help_is_short_but_command_help_keeps_advanced_flags(self):
        root = cli.build_parser().format_help()
        child = cli.build_parser()._subparsers._group_actions[0].choices["fade"].format_help()
        self.assertIn("cync red [BRIGHTNESS]", root)
        self.assertIn("cync use NAME|NUMBER", root)
        self.assertIn("disconnect, shutdown", root)
        self.assertNotIn("{status,info", root)
        self.assertIn("--duration", child)
        self.assertIn("TIME", child)

    def test_shortcuts_match_full_command_forms(self):
        cases = (
            (["red"], ["set", "--on", "--color", "red"], ("set", {"rgb": [255, 0, 0], "on": True})),
            (["red", "100"], ["set", "--color", "red", "--brightness", "100"], ("set", {"rgb": [255, 0, 0], "brightness": 100})),
            (["#0000FF"], ["set", "--on", "--color", "#0000FF"], ("set", {"rgb": [0, 0, 255], "on": True})),
            (["255,0,0"], ["set", "--on", "--color", "255,0,0"], ("set", {"rgb": [255, 0, 0], "on": True})),
            (["color", "blue", "35"], ["color", "blue", "35"], ("set", {"rgb": [0, 0, 255], "brightness": 35})),
            (["set", "red", "100"], ["set", "red", "100"], ("set", {"rgb": [255, 0, 0], "brightness": 100})),
            (["35"], ["brightness", "35"], ("set", {"brightness": 35})),
            (["0"], ["brightness", "0"], ("set", {"brightness": 0})),
            (["123456"], ["brightness", "123456"], None),
        )
        parser = cli.build_parser()
        for short, expanded, expected in cases:
            with self.subTest(short=short):
                short_args = parser.parse_args(cli._normalize_shortcuts(short))
                full_args = parser.parse_args(expanded)
                if expected is None:
                    with self.assertRaises(ValueError): cli._control_args(short_args)
                else:
                    self.assertEqual(cli._control_args(short_args), expected)
                    self.assertEqual(cli._control_args(full_args), expected)

    def test_friendly_aliases_expand_to_existing_commands(self):
        cases = {
            ("lights",): ("devices", "list"),
            ("use", "Bedroom"): ("devices", "use", "Bedroom"),
            ("scenes",): ("scene", "list"),
            ("save", "Evening"): ("scene", "save", "Evening"),
            ("scene", "Evening"): ("scene", "apply", "Evening"),
            ("login",): ("auth", "login"),
        }
        for short, expanded in cases.items():
            with self.subTest(short=short):
                self.assertEqual(tuple(cli._normalize_shortcuts(list(short))), expanded)

    def test_white_and_fade_short_forms_and_compatibility(self):
        parser = cli.build_parser()
        self.assertEqual(cli._control_args(parser.parse_args(["white"])), ("set", {"white": 50, "on": True}))
        self.assertEqual(cli._control_args(parser.parse_args(["white", "23"])), ("set", {"white": 23}))
        self.assertEqual(cli._control_args(parser.parse_args(["fade", "70"])), ("fade", {"brightness": 70, "duration": 2.0}))
        self.assertEqual(cli._control_args(parser.parse_args(["fade", "70", "8"])), ("fade", {"brightness": 70, "duration": 8.0}))
        self.assertEqual(cli._control_args(parser.parse_args(["fade", "70", "--duration", "8"])), ("fade", {"brightness": 70, "duration": 8.0}))
        both = parser.parse_args(["fade", "70", "8", "--duration", "9"])
        with self.assertRaises(cli.CLIError): cli._control_args(both)

    def test_invalid_color_and_conflict_do_not_call_backend(self):
        for argv in (["color", "ultraviolet", "--json"], ["fade", "70", "8", "--duration", "9", "--json"], ["123456", "--json"]):
            with self.subTest(argv=argv), patch.object(cli.backend, "request") as request:
                output = StringIO()
                with redirect_stdout(output):
                    code = cli.main(list(argv))
                self.assertEqual(code, 2)
                result = json.loads(output.getvalue())
                self.assertFalse(result["ok"])
                request.assert_not_called()

    def test_unknown_root_color_has_one_short_hint(self):
        error = StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["gren"])
        self.assertIn("try `cync red`", error.getvalue())
        self.assertNotIn("usage:", error.getvalue())
        self.assertNotIn("{status,info", error.getvalue())

    def test_use_number_selects_exact_list_entry_and_rejects_invalid_index_before_write(self):
        second = dict(DEVICE, id="light-2", name="Desk")
        data = {"schema_version": 1, "default": DEVICE["id"], "devices": [DEVICE, second]}
        with patch.object(cli.config, "load_config", return_value=data), patch.object(cli.config, "select_default") as select:
            result = cli.dispatch(cli.build_parser().parse_args(cli._normalize_shortcuts(["use", "2"])))
        select.assert_called_once_with(second["id"])
        self.assertEqual(result["name"], "Desk")
        with patch.object(cli.config, "load_config", return_value=data), patch.object(cli.config, "select_default") as select, self.assertRaises(cli.CLIError):
            cli.dispatch(cli.build_parser().parse_args(cli._normalize_shortcuts(["use", "3"])))
        select.assert_not_called()

    def test_human_lists_use_names_numbers_and_exact_color_names_without_ids(self):
        devices = [DEVICE, dict(DEVICE, id="light-2", name="Desk")]
        output = StringIO()
        with patch.object(cli.config, "load_config", return_value={"devices": devices}), redirect_stdout(output):
            cli._print_human({"command": "devices list", "devices": devices, "default": "light-2"})
        self.assertIn("1. Desk", output.getvalue())
        self.assertIn("* 2. Desk", output.getvalue())
        self.assertNotIn("light-2", output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            cli._print_human({"command": "status", "device": {"name": "Desk"}, "state": dict(BASE_STATE, rgb=[0, 0, 255])})
        self.assertIn("blue", output.getvalue())
        self.assertNotIn("#0000FF", output.getvalue())

    def test_scene_list_human_output_uses_friendly_light_name(self):
        output = StringIO()
        with patch.object(cli.config, "load_config", return_value={"devices": [DEVICE]}), redirect_stdout(output):
            cli._print_human({"command": "scene list", "scenes": [{"name": "Evening", "device_id": DEVICE["id"]}]})
        self.assertIn("Evening  ·  Desk", output.getvalue())
        self.assertNotIn(DEVICE["id"], output.getvalue())

    def test_json_input_errors_are_enveloped_for_flags_on_either_side(self):
        for argv in (["--json", "brightness", "101"], ["brightness", "101", "--json"]):
            with self.subTest(argv=argv):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.main(argv), 2)
                result = json.loads(output.getvalue())
                self.assertEqual((result["schema_version"], result["ok"], result["command"]), (1, False, "brightness"))
                self.assertEqual(result["error"]["code"], "invalid_input")

    def test_watch_count_stops_after_bounded_number_of_polls(self):
        args = cli.build_parser().parse_args(["watch", "--count", "2", "--interval", "0.5"])
        first = {"command": "status", "device": {"name": "Desk"}, "state": BASE_STATE}
        second = {"command": "status", "device": {"name": "Desk"}, "state": dict(BASE_STATE, brightness=62)}
        output = StringIO()
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli, "_status", side_effect=[first, second]) as status, patch.object(cli.time, "sleep") as sleep, redirect_stdout(output):
            result = cli.dispatch(args)
        self.assertEqual(status.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertEqual(result["polls"], 2)
        self.assertEqual(output.getvalue().count("Desk: on"), 2)

    def test_argparse_errors_are_json_when_requested_after_command(self):
        for argv in (["fade", "25", "--duration", "601", "--json"], ["--json", "fade", "25", "--duration", "601"]):
            with self.subTest(argv=argv):
                output = StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                    cli.build_parser().parse_args(argv)
                self.assertEqual(raised.exception.code, 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result["command"], "fade")
                self.assertFalse(result["ok"])

    def test_request_requires_fresh_verified_state_and_redacts_backend_error(self):
        with patch.object(cli.backend, "request", return_value={"schema_version": 1, "ok": True, "verified": False}):
            with self.assertRaisesRegex(cli.CLIError, "did not confirm"):
                cli._request("status", DEVICE)
        failure = type("Failure", (Exception,), {"code": "timeout", "message": "No fresh bulb report"})()
        with patch.object(cli.backend, "request", side_effect=failure):
            with self.assertRaises(cli.CLIError) as raised: cli._request("set", DEVICE, {"on": True})
        self.assertEqual(raised.exception.code, "timeout")
        self.assertNotIn("mesh", raised.exception.message)

    def test_control_response_json_has_stable_envelope(self):
        response = {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
        with patch.object(cli.backend, "request", return_value=response):
            result = cli.dispatch(cli.build_parser().parse_args(["brightness", "40"]))
        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["requested"], {"brightness": 40})
        self.assertEqual(result["state"], BASE_STATE)

    def test_fade_duration_accepts_units_and_ten_minute_ceiling(self):
        parser = cli.build_parser()
        for value, expected in (("0.2", 0.2), ("8", 8.0), ("8s", 8.0), ("1m", 60.0), ("5m", 300.0), ("1.5m", 90.0), ("0.1h", 360.0), ("600", 600.0), ("10m", 600.0), ("30S", 30.0)):
            with self.subTest(value=value):
                args = parser.parse_args(["fade", "70", value])
                self.assertEqual(cli._control_args(args), ("fade", {"brightness": 70, "duration": expected}))
        for value in ("601", "11m", "0.1", "abc", "5x", "nan", "inf", "-inf", "1e309", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                parser.parse_args(["fade", "70", value])

    def test_fade_duration_flag_also_accepts_units(self):
        args = cli.build_parser().parse_args(["fade", "70", "--duration", "2m"])
        self.assertEqual(cli._control_args(args), ("fade", {"brightness": 70, "duration": 120.0}))

    def test_fade_on_and_off_aliases_request_restore_or_zero(self):
        for target, values in (("on", {"restore_brightness": True}), ("off", {"brightness": 0}), ("ON", {"restore_brightness": True})):
            with self.subTest(target=target):
                args = cli.build_parser().parse_args(["fade", target, "1m"])
                self.assertEqual(cli._control_args(args), ("fade", {**values, "duration": 60.0}))
        args = cli.build_parser().parse_args(["fade", "on"])
        self.assertEqual(cli._control_args(args), ("fade", {"restore_brightness": True, "duration": 2.0}))

    def test_help_command_shows_root_and_command_help(self):
        result = cli.dispatch(cli.build_parser().parse_args(["help"]))
        self.assertEqual(result["command"], "help")
        self.assertIn("cync fade", result["help"])
        result = cli.dispatch(cli.build_parser().parse_args(["help", "fade"]))
        self.assertIn("--duration", result["help"])
        with self.assertRaises(cli.CLIError):
            cli.dispatch(cli.build_parser().parse_args(["help", "nope"]))
        # An everyday alias names a real command, so help resolves it.
        result = cli.dispatch(cli.build_parser().parse_args(["help", "lights"]))
        self.assertIn("devices", result["help"])

    def test_help_aliases_describe_the_specific_action(self):
        for alias, usage in (("lights", "devices list"), ("use", "devices use"),
                             ("login", "auth login"), ("save", "scene save"), ("scenes", "scene list")):
            with self.subTest(alias=alias):
                self.assertIn(f"usage: cync {usage}", cli._help_text(alias))
        fade = cli._help_text("fade")
        self.assertIn("1m", fade)
        self.assertIn("600", fade)
        self.assertIn("default: 2 seconds", " ".join(fade.split()))

    def test_fade_timeout_includes_requested_fade_duration(self):
        calls = []
        def request(command, device=None, values=None, timeout=35):
            calls.append((command, values, timeout))
            return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli.backend, "request", side_effect=request):
            cli.dispatch(cli.build_parser().parse_args(["fade", "30", "--duration", "20", "--wait"]))
        self.assertEqual(calls, [("fade", {"brightness": 30, "duration": 20.0}, 55.0)])
        calls.clear()
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli.backend, "request", side_effect=request):
            cli.dispatch(cli.build_parser().parse_args(["fade", "30", "10m", "--wait"]))
        self.assertEqual(calls, [("fade", {"brightness": 30, "duration": 600.0}, 635.0)])

    def test_background_fade_requires_start_ack_without_claiming_verified_completion(self):
        ack = {"schema_version": 1, "ok": True, "verified": False,
               "accepted": True, "operation_id": "synthetic-operation"}
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli.backend, "request", return_value=ack) as request:
            result = cli.dispatch(cli.build_parser().parse_args(["fade", "30", "20"]))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["verified"])
        self.assertNotIn("state", result)
        self.assertEqual(request.call_args.kwargs["values"], {"brightness": 30, "duration": 20.0, "background": True})
        self.assertEqual(request.call_args.kwargs["timeout"], 55)
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli.backend, "request", return_value={"schema_version": 1, "ok": True}):
            with self.assertRaises(cli.CLIError):
                cli.dispatch(cli.build_parser().parse_args(["fade", "30"]))

    def test_fade_status_and_cancel_do_not_require_device_or_verified_endpoint(self):
        for action in ("status", "cancel"):
            with self.subTest(action=action), patch.object(cli, "_device", side_effect=AssertionError("device lookup")), patch.object(cli.backend, "request", return_value={"schema_version": 1, "ok": True, "operation": {"status": "cancelled"}}) as request:
                result = cli.dispatch(cli.build_parser().parse_args(["fade", action]))
                self.assertEqual(result["operation"]["status"], "cancelled")
                self.assertEqual(request.call_args.args, ("fade-" + action,))
        for argv in (["fade", "status", "1m"], ["fade", "cancel", "--wait"]):
            with self.assertRaises(cli.CLIError):
                cli.dispatch(cli.build_parser().parse_args(argv))

    def test_trailing_help_for_every_command_and_nested_action_is_offline(self):
        parser = cli.build_parser()
        paths = []
        def collect(current, prefix):
            paths.append(prefix)
            for action in current._actions:
                if isinstance(action, cli.argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        if name != "help":
                            collect(child, prefix + [name])
        collect(parser, [])
        paths += [[name] for name in cli._ALIASES] + [["red"], ["35"], ["#33AAFF"], ["fade", "status"], ["fade", "cancel"], ["fade", "--json"], ["fade", "--wait"], ["fade", "--duration", "5s"], ["watch", "--count", "10"]]
        for path in paths:
            if not path:
                continue
            with self.subTest(path=path), patch.object(cli.backend, "request", side_effect=AssertionError("must be offline")), patch.object(cli, "_config", side_effect=AssertionError("must not read config")):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.main(path + ["help"]), 0)
                self.assertIn("usage: cync ", output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["--json", "scene", "save", "help"]), 0)
        self.assertIn("scene save", json.loads(output.getvalue())["help"])

    def test_status_and_info_enable_interactive_first_use_login(self):
        response = {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE, "info": {"type": 21}}
        for command in ("status", "info"):
            with self.subTest(command=command), patch.object(cli, "_device", return_value=DEVICE) as select, patch.object(cli, "_request", return_value=response):
                cli.dispatch(cli.build_parser().parse_args([command]))
            select.assert_called_once_with(None, auto_login=True)

    def test_test_command_returns_restored_state_and_separate_check(self):
        changed = dict(BASE_STATE, brightness=23)
        calls = []
        def request(command, device=None, values=None, timeout=35):
            calls.append((command, values))
            if command == "status": return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
            if values == {"brightness": 23}: return {"schema_version": 1, "ok": True, "verified": True, "state": changed}
            return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
        with patch.object(cli.backend, "request", side_effect=request):
            result = cli._run_test(DEVICE, 23)
        self.assertEqual(result["state"], BASE_STATE)
        self.assertEqual(result["test"], {"check": {"ok": True, "requested_brightness": 23, "observed_brightness": 23, "observed_on": True}, "restored": True})
        self.assertEqual(calls[-1][1], {"on": True, "brightness": 61, "rgb": [73, 146, 219]})

    def test_disconnect_and_shutdown_are_helper_only_commands(self):
        for command in ("disconnect", "shutdown"):
            calls = []
            def request(name, device=None, values=None, timeout=35):
                calls.append((name, device, values, timeout))
                return {"schema_version": 1, "ok": True, "verified": True}
            with self.subTest(command=command), patch.object(cli.backend, "request", side_effect=request):
                result = cli.dispatch(cli.build_parser().parse_args([command]))
            self.assertEqual(calls, [(command, None, {}, 35)])
            self.assertTrue(result["ok"])

    def test_auth_forget_disconnects_before_removing_selected_mesh_key(self):
        order = []
        def request(command, device=None, values=None, *, require_verified=True, timeout=35):
            order.append(("disconnect", command, require_verified))
            return {"schema_version": 1, "ok": True, "verified": True}
        def forget(mesh):
            order.append(("forget", mesh))
            return {"forgotten_mesh_keys": 1}
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli, "_request", side_effect=request), patch.object(cli.auth, "forget", side_effect=forget):
            result = cli.dispatch(cli.build_parser().parse_args(["auth", "forget"]))
        self.assertEqual(order, [("disconnect", "disconnect", False), ("forget", DEVICE["mesh"])])
        self.assertEqual(result["result"]["forgotten_mesh_keys"], 1)

    def test_auth_forget_does_not_delete_key_if_disconnect_fails(self):
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli, "_request", side_effect=cli.CLIError("disconnect_failed", "unable to disconnect")), patch.object(cli.auth, "forget") as forget:
            with self.assertRaises(cli.CLIError):
                cli.dispatch(cli.build_parser().parse_args(["auth", "forget"]))
        forget.assert_not_called()

    def test_human_auth_output_reports_counts_and_names_without_key_values(self):
        output = StringIO()
        with redirect_stdout(output):
            cli._print_human({"command": "auth status", "status": {"keychain_available": True, "configured_devices": 3, "meshes": 2, "stored_mesh_keys": 2}})
        self.assertIn("Configured devices: 3 · meshes: 2", output.getvalue())
        self.assertIn("Stored mesh keys: 2", output.getvalue())
        self.assertNotIn(DEVICE["mesh"], output.getvalue())

        output = StringIO()
        with redirect_stdout(output):
            cli._print_human({"command": "auth login", "devices": [{"name": "Bedroom", "mesh": "private-account", "key": "secret"}]})
        self.assertIn("Imported 1 paired bulb(s)", output.getvalue())
        self.assertIn("Bedroom", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_human_info_and_scan_output_show_requested_details_without_mac(self):
        output = StringIO()
        with redirect_stdout(output):
            cli._print_human({"command": "info", "device": {"name": "Desk"}, "state": BASE_STATE, "info": {"firmware": "1.2", "hardware": "rev B", "type": 21, "manufacturer": "GE"}})
        for detail in ("Firmware: 1.2", "Hardware: rev B", "Type: 21", "Manufacturer: GE"):
            self.assertIn(detail, output.getvalue())

        output = StringIO()
        with redirect_stdout(output):
            cli._print_human({"command": "scan", "devices": [{"name": "Nearby", "rssi": -61, "peripheral_id": "peripheral-uuid", "mac": "AA:BB:CC:DD:EE:FF"}]})
        self.assertIn("-61 dBm", output.getvalue())
        self.assertIn("peripheral-uuid", output.getvalue())
        self.assertNotIn("AA:BB:CC:DD:EE:FF", output.getvalue())

    def test_doctor_reads_native_bluetooth_state_without_a_device(self):
        response = {"schema_version": 1, "ok": True, "verified": True, "bluetooth_state": 5, "authorization": 3}
        with patch.object(cli.backend, "ensure_helper"), patch.object(cli, "_request", return_value=response) as request, patch.object(cli.auth, "status", return_value={"keychain_available": True}):
            result = cli.dispatch(cli.build_parser().parse_args(["doctor"]))
        request.assert_called_once_with("doctor", require_verified=False)
        self.assertEqual(result["bluetooth"], {"available": True, "state": 5, "authorization": 3})

    def test_watch_ignores_observation_timestamp_when_detecting_changes(self):
        args = cli.build_parser().parse_args(["watch", "--count", "2", "--interval", "0.5"])
        first = {"command": "status", "device": {"name": "Desk"}, "state": BASE_STATE}
        second_state = dict(BASE_STATE, observed_at=11.0)
        second = {"command": "status", "device": {"name": "Desk"}, "state": second_state}
        output = StringIO()
        with patch.object(cli, "_device", return_value=DEVICE), patch.object(cli, "_status", side_effect=[first, second]), patch.object(cli.time, "sleep"), redirect_stdout(output):
            cli.dispatch(args)
        self.assertEqual(output.getvalue().count("Desk: on"), 1)

    def test_toggle_reads_fresh_state_then_sends_opposite_value(self):
        calls = []
        def request(command, device=None, values=None, timeout=35):
            calls.append((command, values))
            if command == "status": return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
            return {"schema_version": 1, "ok": True, "verified": True, "state": dict(BASE_STATE, on=False, brightness=0)}
        with patch.object(cli.backend, "request", side_effect=request):
            result = cli.dispatch(cli.build_parser().parse_args(["toggle"]))
        self.assertEqual(calls, [("status", {}), ("set", {"on": False})])
        self.assertTrue(result["ok"])

    def test_test_command_restores_prior_reported_rgb_even_if_test_fails(self):
        calls = []
        def request(command, device=None, values=None, timeout=35):
            calls.append((command, values))
            if command == "status": return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
            if values == {"brightness": 23}:
                return {"schema_version": 1, "ok": True, "verified": True, "state": {"on": True, "brightness": 24}}
            return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
        with patch.object(cli.backend, "request", side_effect=request):
            with self.assertRaises(cli.CLIError): cli._run_test(DEVICE, 23)
        self.assertEqual(calls[-1][1], {"on": True, "brightness": 61, "rgb": [73, 146, 219]})

    def test_restore_failure_is_reported(self):
        calls = []
        def request(command, device=None, values=None, timeout=35):
            calls.append(values)
            if len(calls) == 1: return {"schema_version": 1, "ok": True, "verified": True, "state": BASE_STATE}
            if len(calls) == 2: return {"schema_version": 1, "ok": True, "verified": True, "state": {"on": True, "brightness": 23}}
            raise RuntimeError("synthetic restore failure")
        with patch.object(cli.backend, "request", side_effect=request):
            with self.assertRaisesRegex(cli.CLIError, "restoring the starting state failed"):
                cli._run_test(DEVICE, 23)

    def test_restore_comparison_allows_rgb_quantization_but_checks_mode_and_brightness(self):
        quantized = dict(BASE_STATE, rgb=[109, 182, 255], brightness=62)
        self.assertTrue(cli._states_match(BASE_STATE, quantized))
        self.assertFalse(cli._states_match(BASE_STATE, dict(BASE_STATE, color_mode="white")))
        self.assertFalse(cli._states_match(BASE_STATE, dict(BASE_STATE, brightness=64)))
        off = {"on": False, "brightness": 0, "color_mode": "white", "white": 5}
        self.assertTrue(cli._states_match(off, {"on": False, "brightness": 0, "color_mode": "rgb"}))


if __name__ == "__main__":
    unittest.main()
