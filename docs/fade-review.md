# Fade review — 2026-10-20

Reviewed the duration/help change at `f3c19d1` against the prior release
`e262818`. A one-minute fade is `cync fade TARGET 1m` (or `60` / `60s`); the
feature accepts 0.2–600 seconds. Tests used the existing configured first-generation
type-21 full-color Bluetooth bulb on macOS. No device identities or credentials
are included here.

## Findings and corrections

- The installed CLI was still the earlier release: `cync help fade` was rejected.
  Reinstalled this reviewed checkout and restarted the idle helper. Documented
  copied-source installation and running-helper refresh requirements.
- The native ramp counted timer callbacks. A blocked run loop skipped callbacks,
  stretching the fade and delaying final verification. A native regression failed
  before the correction. The ramp now uses monotonic elapsed time and catches up
  when callbacks resume; native request deadlines also use monotonic uptime.
- An off-to-low fade rounded intermediate brightness to zero and sent power-off
  after power-on. A native regression reproduced that sequence. Positive ramps
  now hold at least protocol level 1 and send off only when completing a zero
  target. Repeated rounded levels are suppressed.
- Alias help stopped at the parent command and fade help did not explain units,
  limits, or defaults. Help now follows the precise alias action and documents
  the time arguments.
- The original PR description overstated timing precision and the old timeout
  cutoff. Scheduling is best effort. A former 120-second helper cap reduced
  headroom once a ramp exceeded 85 seconds; it did not guarantee every such ramp
  failed at 85 seconds. A full 120-second ramp cannot fit along with overhead.

## Reproducible automated checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/test_native.py
python3 scripts/privacy_audit.py .
```

- 58 Python tests pass: CLI forms, duration boundaries/units, invalid/non-finite
  input, scaled timeouts, aliases, JSON contracts, scenes, configuration, auth,
  backend transport, and packaging.
- Native regressions pass against actual helper scheduling/status code using
  synthetic writes and encrypted notifications. Coverage includes off-to-low
  power ordering, zero endpoint, matching-state gating, delayed callbacks,
  ten-minute callback/deadline configuration, and success/failure/disconnect cleanup.
  Before fixes, the intermediate-off and delayed-verification checks failed.
- The production Swift helper compiles targeting macOS 12 and its crypto self-test
  returns `{"ok":true}`. Privacy audit and whitespace checks pass.

## Installed CLI and hardware evidence

- Installed `cync fade 20 1m`: **61.45 seconds**, `verified: true`, on at 20%.
  A separate `cync status` confirmed on at 20%.
- macOS `pmset -g assertions` showed `PreventUserIdleSystemSleep` named
  `Cync fade` owned by the running helper during the ramp.
- Installed `cync fade 50 2m`: **121.40 seconds**, `verified: true`, on at 50%.
  A separate status confirmed 50%. This exercised a full ramp longer than the
  former 120-second native job cap, including connection/readback overhead.
- Installed `cync fade 0 2s`: **3.47 seconds**, `verified: true`, off at 0%.
  A separate status confirmed off. The starting reported off state was restored
  in `finally`; white mode/level also matched the initial report.
- After completion, `pmset -g assertions` no longer listed `Cync fade`, confirming
  release of the activity assertion. Installed Python and Swift sources matched
  the reviewed checkout byte-for-byte during these tests.

An initial off-to-1% test timed out both before and after the native fixes.
Independent direct controls explained why: requests for 1% and 2% each reported
6% on this bulb and failed matching-state verification; 5% and 10% controls
verified at their requested levels. This is a firmware/device observation, not
evidence that the native timing test failed or a universal minimum brightness.
Each failed hardware probe restored the initial reported off state.

## Limits

Synthetic coverage establishes ten-minute scheduling and timeout configuration;
a full ten-minute hardware fade has not been exercised. Endpoint readback does
not prove every intermediate physical level or visual smoothness. Forced sleep,
lid closure, deliberate mid-fade radio loss, and Ctrl-C cancellation were not
hardware-tested. The CLI currently has no native cancellation/resume mechanism;
an accepted fade can continue after its Python caller exits. Other commands wait
behind the serialized client lock. These limits are documented in the
[command reference](commands.md) and [development guide](development.md).
