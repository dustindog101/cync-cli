# Command reference

`cync help` / `cync --help` lists commands. `cync help fade` or
`cync fade --help` explains a command. Alias help follows the underlying action:
`cync help save` describes `scene save`, including its required name.

## Light selection and output

- `cync` shows fresh status for the selected light, or a setup hint if none exists.
- `cync lights` / `cync devices list` lists paired profiles.
- `cync use 1` or `cync use "Desk"` selects a profile by one-based list position
  or name. `devices select ID_OR_NAME` is the explicit selection form.
- `cync devices rename ID_OR_NAME "New name"` changes the local display name.
  `cync devices remove ID_OR_NAME` removes local metadata, without resetting the bulb.
- `--device ID_OR_NAME` targets one command without changing the selection.
  It accepts a name or ID, not a list position. Put it before the command or
  after the final subcommand.
- `--json` returns `schema_version: 1`, `ok`, and command-specific fields. Control
  success includes `verified: true` and fresh `state`; failures include `error`
  with a code and message and a nonzero exit status. JSON may contain device
  identities. `watch --json` emits a stream of JSON lines.

## Power, brightness, and color

```sh
cync status
cync info                         # fresh status plus firmware/GATT information
cync on
cync off
cync toggle
cync 35                           # same as cync brightness 35
cync red 100                      # power on, color, then brightness
cync '#33AAFF'                    # power on and apply color
cync color blue                   # explicit color; preserves power
cync color blue 40                # explicit color plus brightness; turns on
cync rgb 255 0 0                  # explicit RGB channels, 0–255
cync white                        # power on, tunable-white level 50
cync white 30                     # white level only; preserves power
cync set --on --brightness 40 --white 60
cync set --color red --brightness 40
```

Brightness and white level are integers 0–100. White level is the bulb's relative
warmth control, not Kelvin. Brightness 0 turns off; combining it with `--on` is
invalid. RGB and white are mutually exclusive. Explicit color-only/white-level
commands may require the bulb already on to obtain matching readback. The bulb
quantizes reported RGB; small differences from requested channels are expected.
Supported brightness levels can also depend on firmware: the bulb used in the
2026-10-20 review reported 6% after requests for 1% or 2%, while 5% and 10%
verified. Such a mismatch fails verification; the CLI does not silently declare
the requested level reached. This observation is not a universal minimum.

## Fades

```sh
cync fade 70                      # 2 seconds
cync fade 70 60                   # one minute
cync fade 70 1m                   # also one minute
cync fade 70 --duration 1m
cync fade 70 1.5m                 # 90 seconds
cync fade 0 10m                   # fade down, then turn off at the end
```

Use either positional TIME or `--duration`, once. Durations accept plain seconds
or case-insensitive `s`, `m`, `h` suffixes (for example, `0.1h` is six minutes).
The total must be 0.2–600 seconds; `1h` exceeds the ten-minute limit. Non-finite
and out-of-range values fail before device access.

Fades change brightness only and power on when the target is positive. They run
on the Mac over Bluetooth, interpolating from a fresh baseline using monotonic
elapsed time. They use whole-percent steps, suppress repeated levels, and never
use off as an intermediate dim level. A positive fade from off starts at the
bulb's minimum visible brightness; power-on may briefly restore a remembered level.

Leave the Mac in Bluetooth range. The helper asks macOS to defer App Nap and
idle sleep during the operation; closing the lid, forced sleep, radio loss, or
another controller can still interrupt it. Timer scheduling and final readback
add overhead, so command runtime is longer than the requested ramp. This is not
a hard real-time or bulb-resident schedule. Steps can be visible on long fades.

Commands to this helper are serialized. Another command waits for the fade to
finish. Ctrl-C stops the Python caller but does not cancel an already accepted
native fade; the helper may continue to the target. There is no pause, resume,
or cancellation command. A connection failure reports an error and leaves the
light at its last applied level. Check status after an interrupted operation.

## Scenes

```sh
cync save evening                 # same as scene save evening; reads the bulb
cync scenes                       # same as scene list
cync scene evening                # same as scene apply evening
cync scene show evening
cync scene delete evening
```

Scenes belong to the profile that saved them. Applying one to another profile
fails. Names are 1–48 letters, numbers, spaces, dots, dashes, or underscores;
quote names containing spaces. Saving an existing name replaces it. Saved RGB
comes from the bulb's quantized report. An off scene does not capture a hidden
remembered brightness or color.

## Setup and diagnostics

- `cync login` / `auth login`: interactive Cync account sign-in and device import.
  Hidden password and emailed-code prompts; mesh keys are saved to Keychain.
- `cync auth status`: local capability and saved-key counts, without key values.
- `cync auth import-legacy`: migrate credentials from the previous local app's
  Keychain entries. This does not discover a cloud account's ownership.
- `cync auth forget --device NAME`: disconnect and remove the saved mesh key.
  A key can serve several profiles on one mesh; those profiles then need setup.
- `cync scan --duration 8`: read-only discovery (1–30 seconds); does not pair.
- `cync doctor`: inspect helper, Bluetooth authorization/state, profiles, and
  secure storage. Read nested results: a successful diagnostic command can
  still report that a capability is unavailable.
- `cync watch --interval 2 --count 3`: poll fresh status, printing only changes.
  Omit count to keep watching; count measures polls, not printed changes.
- `cync test --brightness 23`: change brightness, verify, and restore the starting
  reported state, even on failure. This really changes the light.
- `cync disconnect`: release the active Bluetooth connection.
- `cync shutdown`: disconnect and stop the background helper. The next request
  launches it again. Use while idle before updating native code.

The CLI supports configured Telink Bluetooth bulbs. Wi-Fi/Matter control,
provisioning, factory reset, firmware updates, and autonomous schedules are outside
its scope. See [development and troubleshooting](development.md).
