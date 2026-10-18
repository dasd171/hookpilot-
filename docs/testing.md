# Testing and metrics

## 1. How to run

```bash
pip install jsonschema pytest
cd tests && python -m pytest -q            # unit + integration + simulation (≈ 30 s)
HOOKPILOT_SLOW=1 python -m pytest -q -m slow   # re-computes the committed report exactly
python tools/gen_report.py --seeds 300 --sweep-seeds 150   # regenerate tests/reports/ (≈ 1 min)
```

Result on 2026-10-18: **125 passed, 1 skipped** (the opt-in slow reproduction test). The
cross-implementation tests ran against the C++ firmware host sim (24 cases) and the TS web demo (3 cases)
and passed; they skip automatically when those builds are absent.

| Suite | What it covers |
|---|---|
| `tests/unit` | mulberry32 vs Node vectors; canonical JSON vs `JSON.stringify`; schema (valid + invalid examples); tamper/reorder/delete/re-seal detection; raw-file sha256; replay (match + tamper); safety-controller rules; engine invariants; every verifier criterion can fail; Konnex CLI guard; edge bridge |
| `tests/integration` | engine → chain → bundle → mock submit/attach/execute/record/prove/verify; bundle tamper; all three CLIs as subprocesses; firmware/TS cross-checks |
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
| Cross-implementation agreement | firmware sim records reproduced by Python replay | 100 % | yes | 32/32 firmware records: signal, classification and rule-trace text identical |

## 3. Interpretation rules
- Every simulation number comes from `tools/gen_report.py` output; nothing is typed by hand.
- Recall/false-trigger on synthetic data are **properties of the generator distributions we chose**
  (docs/synthetic-signals.md). Bites with amplitude below the trigger (1.0 N) are always missed
  (0/31 in the 300-seed run): that is the rule, not a bug.
- The threshold sweep shows trade-offs (e.g. a 400 ms window lets 1.7–2.1 % negatives through) —
  useful for choosing what to measure, not for choosing thresholds.
- Variant comparison: the web-demo defaults on the same synthetic signals give 20 % false triggers,
  all from manual handling (its manual threshold 12 N exceeds most synthetic handling pulls).
  In the same run the web variant labels 83/150 `sensor_failure` (out-of-range, 49.99 N saturation)
  windows as manual handling, because its out-of-range bound is `> 50 N` while firmware uses `>= 49.9 N`.
  The variants must be unified before shared metrics are published.

## 4. Test plan for hardware (summary)
1. Sensor rig: calibration sweeps, noise floor, drift, wet vs dry (docs/calibration.md).
2. Pull library: scripted mechanical pulls (bite-like ramps, slow drift, vibration, hand pulls) via a
   linear stage + reference gauge, recorded as `bench` events with labels → recall/false-trigger on real
   signals, repeatability.
3. Actuator rig (no hook): hook-set travel/time, stops; retrieval with programmable drag/snag; stall.
4. Fault injection for every fault-table row; e-stop timing with a scope.
5. Pond trials with an independent observer (labels, `fish_landed` confirmation by observer only).
