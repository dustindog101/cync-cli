# Working on cync-cli

Read [README.md](README.md), [the command reference](docs/commands.md), and
[the development guide](docs/development.md) before changing behavior. The
[fade review](docs/fade-review.md) records actual validation and remaining limits.

## Start here

- Inspect `git status --short`, the current branch, recent commits, and open PRs.
  Preserve unrelated edits; use a separate worktree for repairs.
- This is a Python 3.10+ CLI plus a native macOS 12+ Swift/CoreBluetooth helper.
  There are no Python runtime dependencies. Help and Python tests work offline.
- Read `src/cync_cli/cli.py` for command normalization, argument validation,
  JSON output, and verified-response requirements; `backend.py` for helper
  building and mailbox transport; `native/main.swift` for actual bulb behavior.
- `./install.sh` copies source into Application Support. Editing a checkout
  does **not** update the installed `cync`. Use `PYTHONPATH=src python3 -m cync_cli`
  for checkout commands. `bin/cync` can prefer the installed environment.
- Before testing a changed native helper or reinstalling, run `cync shutdown`
  while the helper is idle. Rebuilding an executable does not replace an already
  running process. Do not stop another active fade to refresh the helper.

## Contracts to preserve

- Keep everyday commands short; advanced options belong in command help/docs.
  Preserve explicit commands, aliases, and the schema-version-1 JSON envelope.
- Brightness zero means off. Color shortcuts power on first; explicit color and
  white-level operations preserve power. Power-on must precede brightness writes
  because the bulb can restore its old brightness when turned on.
- A transmitted packet is not success. Control results require a fresh matching
  notification after the final write; keep RGB quantization tolerances.
- Fade durations are 0.2–600 seconds (default 2). Maintain CLI validation,
  `duration + 35` request timeout, native deadline headroom, monotonic elapsed-time
  interpolation, callback cap, and activity/timer cleanup together.
- Do not send off for a rounded intermediate zero during a positive fade.
  Fade-to-zero sends off at completion. Keep success blocked until final readback.
- Keep disconnect/reconnect serialized through CoreBluetooth's callback; do not
  remove the `disconnecting`/`queuedPeer` protection.
- Mesh keys belong in macOS Keychain. Never print secrets, place them in argv,
  or copy real profiles, mailbox files, captures, or device IDs into the repo.
  `--json` includes device metadata; sanitize it before publishing evidence.
- Normal control stays local. Do not introduce login/network calls for already
  configured lights or implement provisioning/reset/firmware as a side effect.

## Verify and deliver

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/privacy_audit.py .
# macOS with Apple's Command Line Tools:
python3 scripts/test_native.py
```

Also compile the production helper and run `--crypto-self-test` as described in
the development guide. Native fade tests exercise production callbacks with
synthetic writes/status; they do not prove radio or physical-bulb behavior.

For an authorized live test, read the initial state, make one bounded change,
require verified completion plus independent status, and restore in `finally`.
Record elapsed times and limits without identities or credentials. Do not
claim ten-minute hardware coverage based only on a one-minute test.

Update the relevant docs with behavior changes, stage only task-owned files,
commit/push the review branch, and report local tests, installed smoke, hardware
readback, and CI separately. Avoid claiming checks passed before observing them.
