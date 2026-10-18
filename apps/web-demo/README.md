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
npm test               # vitest: engine unit tests (63 tests)
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
| `signals.ts` | `hookpilot-web-synth-v1` generators per scenario, 50 Hz, reproducible from (scenario, seed) |
| `processor.ts` | SignalProcessor: EMA baseline (frozen while a candidate or cycle is open), delta, smoothed rate, rolling variance, detrended signal, health (disconnected / out_of_range / stuck) |
| `detector.ts` | BiteDetector `deterministic-baseline-v1`: trigger, a 300 ms observation window, ordered rules R1–R6, result + reason + rule_trace |
| `safety.ts` | SafetyController, independent of the classifier: permit/deny, interlocks, latched faults, and SAFE_STOP on any fault |
| `actuators.ts` | Simulated hook-set (mm, travel limit) and retrieval (m) models with position and current feedback, plus jam injection |
| `engine.ts` | State machine IDLE/MONITOR/CANDIDATE/CONFIRM/HOOK_SET/RETRIEVE/VERIFY/SAFE_STOP, plus `runScenario()` |
| `logger.ts`, `canonical.ts`, `sha256.ts` | Schema 1.0.0 records, canonical JSON, pure-TS sha256, hash chain, `verifyChain()` |
| `export.ts` | JSON / CSV export |

The classifier rules are applied in this order, and the first rule that fails decides the result:
R1 sensor health ok → R2 peak delta ≤ manual_handling_delta → R3 duration ≤ max_duration →
R4 oscillations ≤ max_osc → R5 peak rate ≥ min_rate (otherwise `rejected_noise`) → R6 continuous duration ≥ confirm_min
(otherwise `rejected_transient`) → `bite_candidate_confirmed`.

Safety rules:
- The classifier can only *request* actuation; the safety controller decides.
- Hook-set and retrieval are never commanded at the same time.
- Every fault (OVER_TENSION, SENSOR_*, COMM_LOSS, MOTOR_STALL, ACTUATOR_TIMEOUT, TRAVEL_LIMIT, UNDERVOLTAGE, E_STOP) triggers SAFE_STOP and latches.
- Reset works only once no fault is live, and it leaves the engine in IDLE, disarmed.
- The engine boots IDLE and disarmed.
- `fish_landed` is always `"unknown"`.

### Expected outcomes (verified by tests over 5 fixed seeds; a sweep over seeds 0–299 gave the same results)

| Scenario | Armed result | Safety | Actuator command |
|---|---|---|---|
| bite | bite_candidate_confirmed | permit, full cycle → MONITOR | hook_set → retrieval |
| noise_current | rejected_noise | deny | none |
| rod_vibration | rejected_vibration | deny | none |
| manual_handling | rejected_manual_handling | deny | none |
| excessive_tension | bite_candidate_confirmed | safe_stop (OVER_TENSION during retrieval) | stopped |
| sensor_failure | rejected_sensor_fault | safe_stop (SENSOR_STUCK) | none |
| comm_loss | bite_candidate_confirmed | safe_stop (COMM_LOSS) | stopped |
| motor_stall | bite_candidate_confirmed | safe_stop (MOTOR_STALL) | stopped |

When the engine is disarmed, a confirmed bite is **denied** and no actuator moves.

## Record notes
- `safety` carries two extra fields, `reason` and `checks` (the permit trace). The schema allows additional properties.
- Numbers are rounded to 3 decimals so the canonical number text is identical in TS (`JSON.stringify`) and in the Python/C++ canonicalisers.
- Sensor samples taken while the sensor is disconnected (NaN) are left out of `sensor_data.samples`, because JSON has no NaN.

## Limitations
- The synthetic signals are hand-designed so that each scenario lands clearly in its class. The thresholds are placeholders. **No classification accuracy is claimed.**
- The signal generator `hookpilot-web-synth-v1` is specific to this demo. The PRNG and the hashing match the shared contract, but the Python and C++ signal generators are not guaranteed to produce the same waveforms unless they implement this exact spec.
- The browser loop runs on `requestAnimationFrame`, so it pauses in background tabs. Simulated time is the engine's tick clock, not wall time.
- `dist/` must be served over HTTP. Browsers block ES-module scripts loaded from `file://`.
- There is no Konnex integration, no signature (`integrity.signature = null`), and no persistence: the event log lives in memory until you export it.
