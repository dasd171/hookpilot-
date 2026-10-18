# Reviewer guide — verifying HookPilot evidence yourself

Audience: a reviewer (Konnex builder program, validator, collaborator) who wants to check HookPilot
event records without trusting the author. Needs Python ≥ 3.10 and `pip install jsonschema`.

## 1. Thirty-second check
```bash
cd services/evidence-service
python -m evidence_service verify <events.jsonl> --replay
```
Each record line shows `schema`, `hash`, `chain`, `raw`; the summary must say
`PASS: N/N records verified, N/N replays matched`. Exit code 0 = pass, 1 = a check failed.

## 2. What each check means
| Check | Question it answers | How |
|---|---|---|
| schema | Is the record well-formed per `hookpilot-event.schema.json` 1.0.0? | jsonschema Draft 2020-12 + RFC 3339 date-time |
| hash | Was the record changed after sealing? | sha256 of canonical JSON with `record_sha256 = ""` (docs/canonical-hashing.md) |
| chain | Were records removed, inserted or reordered? | `prev_record_sha256` must equal the previous record's hash; first = null (or `--genesis-prev`) |
| raw | Does the raw data file match what the record claims? | sha256 of the file named by `sensor_data.raw_data_ref` (resolved against `--raw-dir`) |
| replay: signal_match | (SIMULATION only) Are the samples exactly what (scenario, seed) generates? | `hookpilot-synth-v1` regeneration; only samples before the first actuation are compared (rigs add actuator load later); tolerance follows recorded precision |
| replay: classification_match | Does the classifier reach the recorded result with the recorded thresholds? | Python port of deterministic-baseline-v1 (the one firmware rule set; web-demo records use the same rules) |
| replay: rules_consistent | Do the recorded features + thresholds imply the recorded result? | pure rule evaluation; authoritative for partial (pre-roll) windows |

## 3. Deeper checks
```bash
python -m evidence_service replay one_event.json             # full JSON report incl. feature diffs and notes
python -m evidence_service export events.jsonl --out e.csv   # one row per record for spreadsheets
python ../../packages/event-schema/validate.py events.jsonl --hash --chain   # independent 150-line validator
cd ../konnex-adapter && python -m konnex_adapter verify-bundle powp.zip       # 11 criteria on a bundle
```

## 4. Red flags to look for
- `source.mode` is `bench`/`field` but `actuation.simulated` is true (or vice versa).
- `fish_landed` other than `unknown` without `outcome.observer_attestation`.
- `calibration_version` like `sim-*` or `uncalibrated` on a non-simulation record.
- `timestamp.clock_quality` = `rtc_unsynced` for evidence that depends on timing.
- `integrity.signature` = null: expected in this prototype; it means **no hardware attestation**.
- Thresholds outside the task's `allowed_trigger_params`.
- Replay notes saying the generator is unknown: signal regeneration was not possible.

## 5. What a pass does NOT tell you
A pass proves internal consistency, integrity since sealing, determinism of the decision logic, and
compliance with the declared safety limits **in the recorded telemetry**. It does not prove that a
fish, a rod or a sensor existed: whoever controls the device before sealing can fabricate consistent
records (simulation records are exactly that and are labelled so). Physical trust needs signed sensor
data, published calibration, device identity and independent observation (docs/konnex-integration.md §6).

## 6. Known limitations of the tool
- The classifier port is a snapshot of the firmware C++ from 2026-10-18; if that changes, replay can disagree
  until the port is updated (tests/integration/test_cross_impl.py and apps/web-demo/tests/cross_impl.test.ts
  catch this).
- Web-demo records since 2026-10-18 name `source.generator = "hookpilot-synth-v1"` and replay fully.
  Legacy records from the retired `hookpilot-web-synth-v1` get a skipped signal check, with a note.
- Firmware records written before 2026-10-18 lack `continuous_above_ms`; rules_consistent then uses
  `duration_above_ms` for R5. Current firmware records include it.
- Only 50 Hz records are supported by the classifier ports.
