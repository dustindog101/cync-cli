# Development, architecture, and troubleshooting

## Source and installed copies

The repository has no Python runtime dependencies. Python tests need Python
3.10+; Swift builds and hardware control need macOS 12+, Bluetooth, and Apple's
Command Line Tools (`xcode-select --install`).

```sh
PYTHONPATH=src python3 -m cync_cli help fade
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/privacy_audit.py .
python3 scripts/test_native.py       # macOS; no Bluetooth or Keychain access
```

`install.sh` creates a venv without pip and copies Python source to
`~/Library/Application Support/cync-cli/lib/cync_cli`, Swift source to
`share/cync-cli/native/main.swift`, and a launcher to `~/.local/bin/cync`.
`CYNC_HOME` overrides Application Support for the installer and CLI;
`CYNC_CLI_BIN_DIR` changes the installer launcher directory; `PYTHON` selects the
installer interpreter. `CYNC_HOME` isolates local files, not Keychain or hardware.
Do not run concurrent helpers with different homes against the same bulb.

Updates need both the installed Python copy and the running native process refreshed:

```sh
cync shutdown                     # while idle, before changing native source
./install.sh
cync help fade
cync doctor                       # compiles/signs the helper if its source changed
cync status
```

The helper build cache hashes Swift source plus architecture. Recompilation alone
does not replace a running helper: Launch Services reuses the app process.
`bin/cync` can use the installed venv, so use `PYTHONPATH=src python3 -m cync_cli`
when intending to run checkout Python. A checkout Bluetooth request still shares
the default helper/runtime unless `CYNC_HOME` is changed.

## Source map

- `cli.py`: parsing, everyday normalization, dispatch, fresh-response checks,
  human output and schema-version-1 JSON. `colors.py`: color/percent validation.
- `config.py`: validated device metadata, default selection, private atomic writes.
- `scenes.py`: profile-bound saved states and private atomic persistence.
- `auth.py`: Cync bootstrap/login; store only mesh keys through the helper.
- `backend.py`: helper source lookup/build/signing, Keychain stdin bridge, private
  mailbox transport and serialized client access.
- `native/main.swift`: AES/Telink protocol, Keychain operations, Cocoa/CoreBluetooth
  lifecycle, connection/authentication, command scheduling and status verification.
- `tests/test_*.py`: offline Python contracts. `tests/native_fade.swift`: production
  native callbacks with synthetic packet writes and encrypted status notifications.
  `scripts/test_native.py` concatenates the helper and tests in a temporary build,
  disabling only the app entry point with `NATIVE_TESTS`.
- `.github/workflows/ci.yml`: Python tests/privacy audit on Linux; production Swift
  compile, crypto vectors, and native fade tests on macOS.

## Request and verification lifecycle

Python obtains a file lock on `runtime/client.lock`, launches/reuses the helper,
and atomically writes `runtime/request.json`. Each request has a UUID, command,
device metadata, values, and timeout; it contains no mesh key. The native helper
polls every 100 ms, connects/authenticates using Keychain, executes, and writes
`response-UUID.json`. Python validates the matching response and cleans up its
mailbox/response. Configuration and runtime directories are private (0700), files
0600. Keychain payloads travel over stdin rather than command-line arguments.

The native helper caches an active connection and disconnects after 90 idle
seconds. Disconnect callbacks can arrive asynchronously; queued reconnects wait
for cancellation completion. Mesh/session credentials stay in Keychain/memory.

Core commands are power `0xD0`, brightness `0xD2`, RGB/white `0xE2`, and query
`0xDA`; notifications decode `0xDC` status. Color state is quantized by the bulb.
Native matching allows brightness/white tolerance 1 and RGB tolerances
37/37/85. Python rejects successful control responses unless `verified` is true.
Verification is gated until after final writes; a matching earlier notification
does not finish a fade. This establishes final reported state, not a recording
of every physical light level during the ramp.

## Fade invariants

The CLI accepts finite durations 0.2–600 seconds and sends timeout `duration+35`.
Python waits another three seconds for mailbox response delivery. Native request
deadlines use monotonic uptime and clamp requested timeouts to 1–700 seconds,
so a maximum fade retains its full 635-second budget for connection and readback.

After fresh baseline status, the helper holds a `.userInitiated` process activity
through completion/failure. Callback count is
`max(2, min(400, Int(duration / 0.15)))`, at interval `duration / count`.
Each callback interpolates from **elapsed monotonic time**, catching up if a
callback is delayed. It suppresses identical rounded brightness writes. Positive
intermediate levels are at least 1; off is sent only at a zero target's completion.
The final target is explicitly sent, then a 400 ms settling delay precedes a fresh
status query. No success is returned until that state matches. Cleanup releases
activity/timers on failure, disconnect, success, and process termination.

Keep the CLI and native limits/timeouts synchronized. Extending the helper cap
is necessary for long fades; a 120-second native cap truncates a 120-second ramp
once connection/readback overhead is included. Shorter ramps can fit under that
cap; do not infer a universal 85-second cutoff from the old overhead budget.

## Native production checks

Run separately from the synthetic native test binary:

```sh
build_dir=$(mktemp -d)
swiftc -target "$(uname -m)-apple-macosx12.0" native/main.swift \
  -o "$build_dir/cync-helper" \
  -framework Cocoa -framework CoreBluetooth -framework Security
"$build_dir/cync-helper" --crypto-self-test
rm -rf "$build_dir"
```

The crypto self-test uses published/synthetic vectors. Native fade tests cover
power ordering, low-brightness/off endpoints, delayed callbacks, final status
gating, ten-minute timer/deadline configuration, and cleanup. They never connect
to a peripheral or read stored keys. Passing tests do not establish range,
compatibility with a different bulb, full-duration ten-minute radio reliability,
or the physical effect of forced sleep.

## Live verification and evidence

When authorized, capture fresh starting status in memory, refresh the installed
helper, measure `cync fade TARGET 1m`, require verified endpoint plus independent
`cync status`, and restore in `finally`. Cover positive starts, off-to-low starts,
and fade-to-zero. Test a ramp longer than the former native cap when validating
extended duration. Keep any raw JSON outside the repository; publish only sanitized
state, elapsed time, command, source revision, and limitations. Compare restoration
with reported RGB quantization; off reports cannot reveal the bulb's remembered
hidden settings. Report restoration failures explicitly.

## Troubleshooting

- Old help or rejected `1m`: inspect `command -v cync`; reinstall the intended
  checkout. Source edits do not change installed Python.
- New Python but old native behavior: stop the idle helper with `cync shutdown`,
  then retry so the current binary starts. Avoid duplicate app instances.
- `BUILD_TOOLS_REQUIRED` / `BUILD_FAILED`: check Command Line Tools; helper source
  lookup checks the checkout, Application Support share directory, then package prefix.
- `BLUETOOTH_PERMISSION` / `BLUETOOTH_OFF`: allow Cync Bluetooth Helper in System
  Settings → Privacy & Security → Bluetooth, and enable Bluetooth.
- `KEY_UNAVAILABLE` / `AUTH_FAILED`: unlock Keychain and allow helper access. Check
  `auth status` and existing migrated credentials before assuming login is needed.
  Reset/re-pair may change mesh credentials; scanning alone cannot recover a key.
- `TIMEOUT` / `DISCONNECTED` / `CONNECTION_FAILED`: check power/range and competing
  phone/controllers; release connections with `disconnect`, then inspect fresh status.
  Do not assume a failed command left the light unchanged.
- A command appears stuck behind a fade: the client lock serializes operations.
  Wait for it to finish. Ctrl-C does not cancel an accepted fade in the native app.

See [the fade review](fade-review.md) for dated evidence. Privacy audit, unit tests,
native simulation, installed CLI, real bulb readback, and remote CI are distinct
verification layers; identify which actually ran.
