# Firmware (C++) simulation notes — for the other HookPilot modules

All of the following is SIMULATION. The host build uses mocks and controls no hardware.

## How to get firmware event lines
```sh
make -C firmware sim SCENARIO=<scenario> SEED=<int>        # JSON lines on stdout
firmware/build/hookpilot_sim --scenario bite --seed 42 --quiet
make -C firmware verify SEED=42                            # schema + sha256 + chain + evidence-service cross-check
```
Scenarios: `idle bite noise_current rod_vibration manual_handling sensor_failure excessive_tension motor_stall comm_loss`.

## Compatibility facts (checked on the build host, 2026-10-18)
* mulberry32 is bit-identical to `services/evidence-service/evidence_service/prng.py`. The reference
  vectors are in `firmware/test_host/test_core_util.cpp`.
* `hookpilot-synth-v1` is ported from `evidence_service/synth.py`. Seed-42 samples match Python within 1e-9.
  The draw order follows the Python port; the firmware adds nothing to it.
* Records are canonical JSON (JS number formatting). `record_sha256` agrees with
  `evidence_service.canonical.record_hash`. `firmware/tools/verify_events.py` checked 32 records
  (seeds 1, 7, 42, 1337) with 0 failures.
* `tests/integration/test_cross_impl.py -k firmware`: 24 passed. That covers verify, replay
  `signal_match`, and the Python engine reaching the same classification for seeds 1/7/42.
* Fault injection timing: `motor_stall` / `comm_loss` faults start `params.fault_after_retrieval_start_ms`
  after the controller enters RETRIEVE.
* `idle` emits no records. A record is only written per candidate cycle, per fault while monitoring,
  and per watchdog-reset boot.

## Firmware-specific provisional thresholds (differ from apps/web-demo config.ts)
`min_rate_n_per_s` 2.0, `manual_handling_delta_n` 5.0, `baseline_alpha` 0.002, `decision_window_ms` 600,
`sensor_max_n` 49.9 (≥ is out of range, so the synth's 49.99 N saturation is flagged `SENSOR_OUT_OF_RANGE`).
These are all placeholders. Every record carries the thresholds it used in `classification.thresholds`.
