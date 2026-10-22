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
  target. This initial review suppressed repeated rounded levels; the cadence
  follow-up below restores those writes at the user's request.
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
hardware-tested. The October 20 build had no native cancellation/resume mechanism;
an accepted fade can continue after its Python caller exits. In that build other commands waited
behind the serialized client lock. The October 22 follow-up below replaces this lifecycle. These limits are documented in the
[command reference](commands.md) and [development guide](development.md).

## Cadence and previous-brightness follow-up

The user requested the original steps through five minutes and `fade on` to
restore the previous brightness. The 400-step cap stretched a two-minute fade's
interval to 300 ms and a five-minute fade's to 750 ms. Suppressing repeated
rounded values also reduced actual writes even on shorter fades.

The current implementation restores every scheduled brightness write at roughly
150 ms through five minutes. Beyond five minutes it caps the ramp at 2000 steps,
gradually increasing the interval to 300 ms at ten minutes. Elapsed-time timing,
off-at-completion, final readback, and sleep protection remain in place.

`fade on` now restores the last confirmed positive brightness per profile, with
100% used only if no level is known. Ordinary off captures fresh brightness before
sending off; fade-off remembers its starting baseline. Intermediate ramp reports
cannot overwrite the restore target. Levels persist privately across restarts in
`runtime/brightness-levels.json`; `fade off` is an alias for target 0.

59 Python tests and native regressions pass. New cadence tests fail against the
previous `c834249` implementation at 120, 299, 300, and 600 seconds and for repeated
levels, then pass with this change. Native tests also cover per-profile memory,
restoration after restart, fade-down preservation, ordinary-off baseline capture,
unknown-level fallback, mode 0600, and aborting off if persistence fails. Production
Swift compilation, crypto self-test, and privacy audit pass.

Installed hardware verification of the final restore behavior:

- Set 37%, ordinary off, then helper shutdown. `cync fade on 1m` returned
  `verified: true` at **37%**, taking **69.15 seconds** end to end including helper
  launch, connection, ramp, and final readback. Independent status confirmed 37%.
- `cync fade off 3s` returned verified off in **4.38 seconds**; independent status
  confirmed off. After another helper restart, `cync fade on 3s` again verified
  37%, proving fade-down had not replaced the stored restore level.
- Restored the initial reported off state in `finally`. Installed Python and
  Swift sources matched the checkout byte-for-byte.

Cadence boundaries at five and ten minutes are covered by native tests; these
longer hardware ramps were not repeated for this follow-up. Whole-percent bulb
levels and possible power-on restoration flashes still limit visual smoothness.


## Responsive commands follow-up — 2026-10-22

The old client lock covered the entire fade. A new concurrency regression fails
against the original backend because a status caller cannot finish while a fade
caller waits; it passes after limiting the lock to ordinary mailbox publication.
Waiting publishers sleep outside the lock. Urgent fade status/cancel and doctor
requests use unique priority mailboxes consumed before queued ordinary commands.

Fades now return an unverified start acknowledgement after fresh baseline/start.
`--wait` retains verified foreground completion. The helper tracks the ramp
separately from read-only observers and explicit white/RGB-only controls, so those
operations can verify while brightness continues changing. Brightness, power, or
another fade replaces the ramp. `fade status` reports its latest result and
`fade cancel` stops it. Every command and nested action accepts trailing `help`,
including aliases and options before help.

Startup is capped at 35 seconds independently of ramp duration; foreground
requests retain duration+35. Final readback, fresh per-ramp gates, restore memory,
monotonic timing, original cadence, and cleanup remain enforced. Persistence
failure cannot turn into completed status; restart marks an interrupted running
operation failed. Explicit white/color while off fails promptly with `LIGHT_OFF`
and preserves the healthy connection.

Validation of this follow-up:

- 65 Python tests passed. Native tests passed against production callbacks,
  including acceptance/completion separation, same-device status and independent
  tone verification during foreground fades, cancellation/supersession, ordinary
  queuing, priority cancellation, startup bounds, successive final-readback gates,
  persistence failure, restart recovery, physical device matching, and cleanup.
- The production helper compiled targeting macOS 12 and its crypto self-test
  returned `{"ok":true}`. Privacy audit and whitespace checks passed.
- Installed CLI help and doctor worked. Installed Python CLI/backend and Swift
  sources matched the checkout byte-for-byte. Doctor reported Bluetooth powered
  on and authorization allowed.
- With a warm connection, `fade 20 6s` acknowledged start in **0.575s**. Status
  during the ramp returned fresh verified brightness in **0.662s**. `white 0`
  verified tone 0 in **1.194s**, and the fade remained running. A brightness 12
  control verified in **1.258s** and marked that fade cancelled.
- `fade 15 0.6s --wait` verified 15% in **2.024s**; its operation record and an
  independent status confirmed the endpoint. `fade off 0.6s --wait` verified off
  in **1.989s**. Explicit cancellation returned in **0.240s**.
- On the final installed source, `fade 20 4s` acknowledged in **0.754s**.
  `color red` verified in **0.858s** while the ramp continued; a separate status
  observed changing brightness in RGB mode. The background operation completed
  with `verified: true` at 20%, and independent status confirmed 20% and red.
- While off, `white 0` returned `LIGHT_OFF` in **0.668s**. The following status
  verified off in **0.961s**, confirming that validation did not force a reconnect.
- Both live runs restored the initial reported on state, brightness 6%, and white
  tone 59 in `finally`, with independent verified status. No `Cync fade` process
  activity assertion remained after completion/cancellation.

Cold Bluetooth startup still varies: the original installed build took 4.83s for
`white 0` after idle; the first rebuilt fade start took 11.53s, and a later cold
status took 8.76s. An initial five-second startup assertion failed, and restoration
succeeded; subsequent timing checks warmed the connection before measuring ramp
responsiveness. Backgrounding removes ramp/queue waiting, not connection or
fresh-readback latency. The helper still releases connections after 90 idle seconds.

Hardware coverage here is short ramps and bounded interruption/control tests.
Five-/ten-minute cadence remains synthetic coverage; no new full ten-minute radio
run or visual smoothness measurement was performed. Whole-percent firmware levels
can remain visible. CI status is separate from these local and installed checks.
