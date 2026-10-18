# HookPilot web demo (SIMULATION ONLY)

Interactive browser demo of the HookPilot control loop. **Everything is simulated:** the line tension is
synthetic, the actuators are software models, and no hardware is connected. The banner
"SIMULATION — synthetic data, no hardware connected" is always on screen. All thresholds and limits are
**provisional placeholders (derive from calibration + labeled data)**, not validated values.

Stack: React 19 + TypeScript + Vite. The engine is plain TypeScript with no React (`src/engine/`) and is
shared by the UI, the tests, and the headless CLI. Event types live in `../../packages/shared-types/src/index.ts`.

## Commands (Node 22)

```bash
cd hookpilot/apps/web-demo
npm install
npm run dev            # local dev server
npm test               # vitest: engine unit tests + cross-implementation tests vs Python (68 tests)
npm run typecheck      # tsc --noEmit
npm run build          # typecheck + static build -> dist/ (base './', works from any static host path)
npm run preview        # serve dist/ locally

# Headless engine run: prints the hash-chained event records as JSON
npm run -s sim -- --scenario bite --seed 42
npm run -s sim -- --scenario rod_vibration --seed 7 --summary
npm run -s sim -- --scenario bite --seed 42 --disarmed
npm run -s sim -- --scenario bite --seed 42 --threshold 4.0          # trigger above bite amplitude -> no_event
npm run -s sim -- --scenario excessive_tension --seed 42 --force-limit 6
npm run -s sim -- --scenario bite --seed 42 --epoch 2026-05-01T00:00:00Z
```

Use `-s` (silent) so npm's banner does not mix into stdout. Scenarios: `bite, noise_current, rod_vibration,
manual_handling, excessive_tension, sensor_failure, comm_loss, motor_stall`. The CLI arms the engine by default
and uses a fixed clock origin (`2026-01-01T00:00:00Z`), so the same `(scenario, seed)` gives byte-identical
output and identical `record_sha256`. The browser uses the real clock as its origin, so its timestamps and
hashes differ from the CLI even when the signal and decision are the same.

## Engine (src/engine)

| File | Role |
|---|---|
| `prng.ts` | mulberry32 (same algorithm as Python/C++); `uniform` = 1 draw, `gauss` = Box-Muller cosine, 2 draws |
| `signals.ts` | TS port of the shared generator `hookpilot-synth-v1` (evidence_service/synth.py = firmware scenarios.cpp), 50 Hz, 8000 ms, reproducible from (scenario, seed) |
| `processor.ts` | SignalProcessor, a port of firmware BaselineSignalProcessor: EMA baseline (frozen while a candidate or cycle is open), delta, smoothed rate, rolling variance, detrended signal, health (disconnected / out_of_range `>= 49.9 N` / stuck) |
| `detector.ts` | BiteDetector `deterministic-baseline-v1`, a port of firmware DeterministicBaselineV1: trigger, 600 ms decision window, ordered rules R0–R5, result + reason + rule_trace (printf-identical text) |
| `window.ts` | `classifyWindow()`: one window through processor + detector, same semantics as Python `classify()` |
| `fmt.ts` | C-printf / Python-compatible number formatting for rule traces |
| `safety.ts` | SafetyController, independent of the classifier: permit/deny, interlocks, latched faults, and SAFE_STOP on any fault |
| `actuators.ts` | Simulated hook-set (mm, travel limit) and retrieval (m) models with position and current feedback, plus jam injection |
| `engine.ts` | State machine IDLE/MONITOR/CANDIDATE/CONFIRM/HOOK_SET/RETRIEVE/VERIFY/SAFE_STOP, plus `runScenario()` |
| `logger.ts`, `canonical.ts`, `sha256.ts` | Schema 1.0.0 records, canonical JSON, pure-TS sha256, hash chain, `verifyChain()` |
| `export.ts` | JSON / CSV export |

The classifier rules are the firmware's (same order, same default thresholds: trigger 1.0 N, decision window
600 ms, min rate 2 N/s, manual handling 5 N, max osc 4, confirm 300 ms, baseline_alpha 0.002, out of range
`>= 49.9 N`; all provisional). The first rule that fails decides the result:
R0 sensor health ok → R1 peak delta ≤ manual_handling_delta_n → R2 duration ≤ max_duration_ms →
R3 oscillations ≤ max_osc → R4 peak rate ≥ min_rate (otherwise `rejected_noise`) → R5 continuous duration ≥
confirm_min_ms (otherwise `rejected_transient`) → `bite_candidate_confirmed`.

Each scenario run resets the signal processor and detector. All times inside a record (samples, decision,
actuation) are ms since the scenario start, so Python's `evidence_service.replay` can regenerate the signal
from `(scenario, seed)` and re-run the classifier (`source.generator = "hookpilot-synth-v1"`).
`timestamp.monotonic_ms` keeps engine time. `motor_stall` / `comm_loss` faults are injected
`fault_after_retrieval_start_ms` after retrieval starts, as in engine.py (the comm outage lasts 2 s).
`tests/cross_impl.test.ts` checks generator, classifier and engine against Python fixtures. Regenerate the
fixtures with `python tools/gen_web_fixtures.py` from the repo root.

Safety rules:
- The classifier can only *request* actuation; the safety controller decides.
- Hook-set and retrieval are never commanded at the same time.
- Every fault (OVER_TENSION, SENSOR_*, COMM_LOSS, MOTOR_STALL, ACTUATOR_TIMEOUT, TRAVEL_LIMIT, UNDERVOLTAGE, E_STOP) triggers SAFE_STOP and latches.
- Reset works only once no fault is live, and it leaves the engine in IDLE, disarmed.
- The engine boots IDLE and disarmed.
- `fish_landed` is always `"unknown"`.

### Expected outcomes (design intent; verified by tests over 5 fixed seeds)

The shared generator draws bite amplitudes of 0.7–2.6 N, so some seeds produce `no_event` (amplitude below
the 1.0 N trigger) or `rejected_transient`. That is the rule working, not a bug: the Python report shows
257/300 bites confirmed. For any seed the cross-implementation test checks that the result is the same as
Python's.

| Scenario | Armed result | Safety | Actuator command |
|---|---|---|---|
| bite | bite_candidate_confirmed | permit, full cycle → MONITOR | hook_set → retrieval |
| noise_current | rejected_noise | deny | none |
| rod_vibration | rejected_vibration | deny | none |
| manual_handling | rejected_manual_handling | deny | none |
| excessive_tension | bite_candidate_confirmed | safe_stop (OVER_TENSION during retrieval) | stopped |
| sensor_failure | rejected_sensor_fault | safe_stop (SENSOR_STUCK or SENSOR_OUT_OF_RANGE, by drawn mode) | none |
| comm_loss | bite_candidate_confirmed | safe_stop (COMM_LOSS) | stopped |
| motor_stall | bite_candidate_confirmed | safe_stop (MOTOR_STALL) | stopped |

When the engine is disarmed, a confirmed bite is **denied** and no actuator moves.

## Record notes
- `safety` carries two extra fields, `reason` and `checks` (the permit trace). The schema allows additional properties.
- Samples, features and thresholds are written at full double precision. Python's canonicaliser reproduces `JSON.stringify` number text (shortest round-trip), so the hashes verify in Python. Actuator feedback values stay rounded to 3 decimals.
- `features` carries the same keys as firmware and Python records, including `continuous_above_ms`. `classification` adds `implementation`, `decision_t_ms` and `trigger_t_ms`.
- Sensor samples taken while the sensor is disconnected (NaN) are left out of `sensor_data.samples`, because JSON has no NaN.

## Limitations
- The synthetic distributions are invented to exercise every rule, not fitted to real bites. The thresholds are placeholders. **No classification accuracy is claimed.**
- Only the signal and the classifier are shared with firmware/Python. The state machine, safety and actuator models are the demo's own: dwell times, simulated line load during actuation, and a 1-sample over-tension check versus firmware's 2. Records therefore differ outside `classification`/`features`.
- Rule-trace framing: like Python, the trace starts with a `t=… TRIGGER …` line, which firmware does not write.
- The browser loop runs on `requestAnimationFrame`, so it pauses in background tabs. Simulated time is the engine's tick clock, not wall time.
- `dist/` must be served over HTTP. Browsers block ES-module scripts loaded from `file://`.
- There is no Konnex integration, no signature (`integrity.signature = null`), and no persistence: the event log lives in memory until you export it.
