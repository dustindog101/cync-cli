# cync-cli

**Your lights. Your Mac. Local Bluetooth.**

A small macOS CLI for Cync / C by GE Bluetooth bulbs. Sign in once to retrieve the mesh key, then control your lights locally, without a cloud connection or another login.

## Get started

Requires macOS 12+, Python 3.10+, Bluetooth, and Apple’s Command Line Tools (`xcode-select --install`). Your bulb must already be set up in a Cync account.

```sh
git clone https://github.com/dustindog101/cync-cli.git
cd cync-cli
./install.sh
cync auth login
cync set --on --color red --brightness 100
```

The installer puts `cync` in `~/.local/bin`; add that directory to your PATH if needed. The Swift Bluetooth helper builds on first use. Allow its Bluetooth and Keychain prompts in macOS. There are no third-party Python dependencies.

Sign-in asks for your email, hidden password and emailed verification code. Mesh keys are kept in **macOS Keychain**; passwords and cloud tokens are never saved. Device profiles and scenes stay in local files. First use of a control or status command can also prompt for setup. An existing key survives restarts; resetting or re-pairing the bulb may require setup again.

## Make it yours

```sh
cync on                         # power, off, toggle
cync brightness 35              # 0–100; zero turns off
cync color blue                 # named colors, '#ff8800', or '255,128,0'
cync white 50                   # bulb's white-temperature scale, 0–100
cync fade 70 --duration 8        # smooth brightness transition
cync status --json              # fresh, verified readback
cync scene save evening
cync scene apply evening
```

| Command | What it does |
| --- | --- |
| `devices list / select / rename / remove` | Manage saved light profiles; use `--device NAME` for one command |
| `scene list / show / delete` | Manage your locally saved scenes |
| `scan`, `info` | Discover nearby Telink devices; read bulb firmware and hardware information |
| `watch --count 5` | Observe state changes; omit the count to keep watching |
| `doctor`, `test` | Check permissions; verify a brightness change and restore the starting state |
| `disconnect`, `shutdown` | Release the Bluetooth connection; stop the background helper |
| `auth status / forget / import-legacy` | Check saved keys, remove a mesh key, or migrate the earlier GE Bulb Control helper |

Run `cync --help` or `cync COMMAND --help` for options. `--json` provides structured output and errors; watch emits one JSON object per change. Commands fail if the bulb does not confirm the requested state. RGB reports are quantized by the bulb, so reported colors may differ slightly from requested values.

Tested with a first-generation full-color A19 Made for Google bulb (type 21). Other Telink mesh bulbs may work; features depend on hardware. Wi-Fi/Matter control, new-device provisioning, and firmware updates are outside this CLI’s scope. Close other apps connected to the bulb if connection fails.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/privacy_audit.py .
```

Configuration and scenes live in `~/Library/Application Support/cync-cli`, separately from the repository. `CYNC_HOME` overrides that directory. Private runtime files and credentials belong outside Git.

Apache-2.0. Built on protocol work from [python-dimond](https://github.com/google/python-dimond) and [python-laurel](https://github.com/google/python-laurel); see [LICENSE](LICENSE) and [NOTICE](NOTICE). Independent of GE Lighting and Cync.
