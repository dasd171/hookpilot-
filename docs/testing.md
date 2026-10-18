# Testing and metrics

## 1. How to run

```bash
pip install jsonschema pytest
cd tests && python -m pytest -q            # unit + integration + simulation (≈ 30 s)
HOOKPILOT_SLOW=1 python -m pytest -q -m slow   # re-computes the committed report exactly
python tools/gen_report.py --seeds 300 --sweep-seeds 150   # regenerate tests/reports/ (≈ 1 min)
```

Result on 2026-10-18: **150 passed, 1 skipped** (the opt-in slow reproduction test). The
cross-implementation tests ran against the C++ firmware host sim (24 cases) and the TS web demo (27 cases:
8 scenarios × seeds 1/7/42 armed, plus 3 disarmed) and passed. They skip automatically when those builds
are absent. `test_web_fixtures.py` fails if the web demo's Python fixtures are stale.

Other suites, same day: `cd apps/web-demo && npm test` gives **68/68**, including
`tests/cross_impl.test.ts`. `cd firmware && make test` gives **46/46 tests, 413 checks**.

| Suite | What it covers |
|---|---|
| `tests/unit` | mulberry32 vs Node vectors; canonical JSON vs `JSON.stringify`; schema (valid + invalid examples); tamper/reorder/delete/re-seal detection; raw-file sha256; replay (match + tamper); safety-controller rules; engine invariants; every verifier criterion can fail; Konnex CLI guard; edge bridge |
| `tests/integration` | engine → chain → bundle → mock submit/attach/execute/record/prove/verify; bundle tamper; all three CLIs as subprocesses; firmware/TS cross-checks (verify + replay with `signal_match` + same result/reason/rule trace as Python); web fixture freshness |
| `apps/web-demo/tests/cross_impl.test.ts` | TS `hookpilot-synth-v1` vs Python samples/params (27 waveforms, ≤ 1e-9); TS classifier vs Python on 360 windows (9 scenarios × seeds 1–40); TS engine first record vs Python on 320 runs: result, reason, rule trace, features |
| `tests/simulation` | labeled scenario set (60 seeds × 9 scenarios): metric consistency, safety-fault coverage, evidence invariants, determinism, threshold monotonicity |

## 2. Metrics

TARGETS are provisional engineering goals, **not** results. "Now" = measurable today in simulation;
"HW" = needs hardware.

| Metric | Definition | TARGET (provisional) | Now? | Current value (source) |
|---|---|---|---|---|
| Tension error vs reference | HookPilot − reference force gauge, static steps 0.5–20 N and dynamic pulls | ≤ ±0.1 N or ±3 % | HW | — |
| Recall (bite) | confirmed / true bites | ≥ 90 % on labeled bench pulls; field TBD | sim: yes | **85.7 % (257/300), 95 % CI 81.2–89.2 %** on synthetic `bite` (tests/reports) |
| False-trigger rate | confirmed / non-bite windows | ≤ 1 % per labeled negative window on bench | sim: yes | **0.0 % (0/1500), CI upper 0.3 %** on synthetic negatives — synthetic negatives are easy; not predictive |
| Repeatability | std. dev. of decision time / peak features for identical mechanical pulls (rig) | decision time σ ≤ 20 ms | sim: trivially 0 (deterministic) | HW needed for a meaningful value |
| Detection latency | true pull onset → decision | ≤ 700 ms (design: decision_window 600 ms) | sim: yes | mean 663 ms, p95 720 ms (synthetic onset); trigger→decision = 580 ms by design |
| Hook-set timing | decision → hook-set complete | ≤ 300 ms | sim: model only | 200 ms in the kinematic model (not physics) |
| Retrieval-force control | fraction of retrieval time with tension ≤ force limit; peak overshoot | ≥ 99 %; overshoot ≤ 10 % | HW (sim checks rule compliance) | verifier: 0 violations in all simulated records |
| E-stop response | press → actuator rails 0 V and motion stopped | < 100 ms | HW | — |
| Fault coverage | fraction of fault-table rows injected and handled (SAFE_STOP + correct fault string) | 100 % of rows | sim: 4 scenarios (sensor, over-tension, stall, comm loss) | 300/300 each in simulation; remaining rows (E_STOP, UNDERVOLTAGE, OVER_TEMP, WATCHDOG, TIMEOUT, TRAVEL) need firmware/hardware tests |
| Fault detection latency | condition onset → fault latched | over-tension ≤ 60 ms, stall ≤ 300 ms, comm loss ≤ 350 ms | sim: yes | 20 / 180 / 300 ms on the 20 ms simulated tick (reflects configured debounce) |
| Evidence completeness | records passing all verifier criteria / all records | 100 % | sim: yes | 2700/2700 schema-valid, hash+chain ok, replay ok; mock bundles score 11/11 |
| Cross-implementation agreement | firmware / web-demo records reproduced by Python replay; TS vs Python decisions | 100 % | yes | 32/32 firmware records: signal, classification and rule-trace text identical. Web demo: 48/48 records (8 scenarios × seeds 1/7/42, armed + disarmed) replay with `signal_match`. TS vs Python on seeds 1–300: 2700/2700 classifier windows and 2400/2400 engine first records with identical result, reason and rule trace (one-off check, 2026-10-18; the committed tests cover seeds 1–40) |

## 3. Interpretation rules
- Every simulation number comes from `tools/gen_report.py` output; nothing is typed by hand.
- Recall/false-trigger on synthetic data are **properties of the generator distributions we chose**
  (docs/synthetic-signals.md). Bites with amplitude below the trigger (1.0 N) are always missed
  (0/31 in the 300-seed run): that is the rule, not a bug.
- The threshold sweep shows trade-offs (e.g. a 400 ms window lets 1.7–2.1 % negatives through) —
  useful for choosing what to measure, not for choosing thresholds.
- One classifier: since 2026-10-18 the web demo uses the firmware rules and defaults and the shared generator,
  so the report's numbers describe all three implementations' classifier on `hookpilot-synth-v1`. The
  former web-variant comparison (20 % false triggers with its 12 N manual threshold) was removed with that
  variant. The report's metrics did not change when it was regenerated.
  Still not shared: the actuation/safety models (each implementation has its own), so the safety-scenario
  rows come from the Python engine only. In a one-off check (seeds 1–300) the web engine also ended every
  confirmed `excessive_tension` / `motor_stall` / `comm_loss` run and every `sensor_failure` run in SAFE_STOP
  with the expected fault (300/300 each). No negative-scenario record requested actuation (0 in 900 runs).

## 4. Test plan for hardware (summary)
1. Sensor rig: calibration sweeps, noise floor, drift, wet vs dry (docs/calibration.md).
2. Pull library: scripted mechanical pulls (bite-like ramps, slow drift, vibration, hand pulls) via a
   linear stage + reference gauge, recorded as `bench` events with labels → recall/false-trigger on real
   signals, repeatability.
3. Actuator rig (no hook): hook-set travel/time, stops; retrieval with programmable drag/snag; stall.
4. Fault injection for every fault-table row; e-stop timing with a scope.
5. Pond trials with an independent observer (labels, `fish_landed` confirmation by observer only).
