# cync-cli

**Your lights. Your Mac. Local Bluetooth.**

Control Cync and C by GE Bluetooth bulbs from your terminal. Sign in once to get the mesh key; everyday control then stays on your Mac.

## Install

Requires macOS 12+, Python 3.10+, Bluetooth, and Apple’s Command Line Tools. Your bulb must already be set up in a Cync account.

```sh
git clone https://github.com/dustindog101/cync-cli.git
cd cync-cli
./install.sh
cync login
```

The installer puts `cync` in `~/.local/bin`; add it to PATH if needed. The Swift Bluetooth helper builds on first use. Allow its Bluetooth and Keychain prompts in macOS. Sign-in prompts for your email, hidden password, and emailed code. Mesh keys stay in macOS Keychain; passwords and cloud tokens are not saved. An already configured profile works without signing in again; resetting or re-pairing a bulb may require setup again.

## Everyday commands

```sh
cync red 100           # red at full brightness; color shortcuts turn the light on
cync 35                # set brightness; 0 turns the light off
cync off
cync fade 70 8         # fade to 70% over 8 seconds (default: 2 seconds)
cync fade 70 5m        # fade to 70% over 5 minutes (up to 10 minutes)
cync white             # tunable white, default level 50
cync save evening
cync scene evening
cync lights            # list saved light profiles
cync use 1             # choose a light by number or name
```

`cync help` shows the command list, and `cync help fade` explains one command. Fade durations accept plain seconds (`8`), seconds (`30s`), or minutes (`5m`, `1.5m`).

Commands use the last-selected light profile. `cync` with no arguments shows its status when a profile is configured, or a setup hint with a link to help when no light is paired. Use `cync login` for a fresh setup; control commands can prompt for setup when no profile is configured.

## More options

`cync --help` shows the commands; `cync COMMAND --help` explains their options. Existing explicit commands and flags still work. `cync set ...` combines settings, `--device NAME` targets a profile for one command, and `--json` returns structured output for scripts. `cync scan`, `info`, `watch`, `doctor`, and `test` help discover and diagnose lights; `test` restores the starting state when it finishes.

Color shortcuts turn the light on; explicit `color` and `white LEVEL` commands preserve power and can require the bulb already on for readback. Commands that change a light require a fresh device readback. RGB values are quantized by the bulb, so reported colors may differ slightly. Features vary by bulb. Tested with a first-generation full-color A19 Made for Google bulb (type 21). Wi-Fi/Matter control, provisioning, and firmware updates are outside this CLI’s scope.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/privacy_audit.py .
```

Configuration and scenes live in `~/Library/Application Support/cync-cli`; `CYNC_HOME` overrides that location.

Apache-2.0. Protocol attribution and license details are in [NOTICE](NOTICE) and [LICENSE](LICENSE). Independent of GE Lighting and Cync.
