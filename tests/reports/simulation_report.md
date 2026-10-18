# HookPilot SIMULATION metrics report (auto-generated)

> **SIMULATION ONLY.** Every number below was computed by `tools/gen_report.py` from runs of the Python reference engine on synthetic `hookpilot-synth-v1` signals. The signal distributions were chosen by us (docs/synthetic-signals.md) and the classifier thresholds are provisional placeholders. These numbers say how the rules behave on our synthetic set; they say **nothing** about real fish, real water or real hardware. Do not quote them as field performance.

- Generated: 2026-10-18T11:57:17Z · Python 3.12.15 · runtime 56.7 s
- Classifier: `deterministic-baseline-v1` (python-port-of-firmware-cpp@2026-10-18), generator `hookpilot-synth-v1`
- Seeds per scenario: 300 (1..300); 2700 event records total
- Reproduce: `python tools/gen_report.py --seeds 300 --sweep-seeds 150`

## Headline (provisional thresholds, synthetic data)

| Metric | Value | 95% Wilson CI |
|---|---|---|
| Recall, `bite` scenario | 85.7% (257/300) | [81.2%, 89.2%] |
| Recall, all bite-bearing scenarios | 96.4% (1157/1200) | [95.2%, 97.3%] |
| False-trigger rate, all negatives | 0.0% (0/1500) | [0.0%, 0.3%] |
| False-trigger rate, `idle` | 0.0% (0/300) | [0.0%, 1.3%] |
| False-trigger rate, `noise_current` | 0.0% (0/300) | [0.0%, 1.3%] |
| False-trigger rate, `rod_vibration` | 0.0% (0/300) | [0.0%, 1.3%] |
| False-trigger rate, `manual_handling` | 0.0% (0/300) | [0.0%, 1.3%] |
| False-trigger rate, `sensor_failure` | 0.0% (0/300) | [0.0%, 1.3%] |

## Latency of confirmed bites (ms, simulated clock, 20 ms ticks)

| Interval | n | mean | p50 | p95 | min | max |
|---|---|---|---|---|---|---|
| trigger_to_decision | 257 | 580.0 | 580.0 | 580.0 | 580.0 | 580.0 |
| true_onset_to_decision | 257 | 663.191 | 660.0 | 720.0 | 620.0 | 760.0 |

The trigger→decision interval is dominated by the configured `decision_window_ms` (600 ms); it is a design parameter, not a measured hardware latency.

## Per-scenario classification counts

| scenario | n | bite_candidate_confirmed | no_event | rejected_manual_handling | rejected_noise | rejected_sensor_fault | rejected_transient | rejected_vibration | class acc. | test pass |
|---|---|---|---|---|---|---|---|---|---|---|
| idle | 300 | 0 | 300 | 0 | 0 | 0 | 0 | 0 | 100.0% | 300/300 |
| bite | 300 | 257 | 23 | 0 | 5 | 0 | 15 | 0 | 85.7% | 257/300 |
| noise_current | 300 | 0 | 74 | 0 | 226 | 0 | 0 | 0 | 100.0% | 300/300 |
| rod_vibration | 300 | 0 | 50 | 0 | 1 | 0 | 1 | 248 | 82.7% | 300/300 |
| manual_handling | 300 | 0 | 0 | 300 | 0 | 0 | 0 | 0 | 100.0% | 300/300 |
| sensor_failure | 300 | 0 | 0 | 0 | 0 | 300 | 0 | 0 | 100.0% | 300/300 |
| excessive_tension | 300 | 300 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 300/300 |
| motor_stall | 300 | 300 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 300/300 |
| comm_loss | 300 | 300 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 300/300 |

`test pass` = positives confirmed (and, armed, the expected safety fault latched); negatives produced no actuation and no unexpected fault. `class acc.` = exact expected label.

## Bite recall by ground-truth parameters

| bucket | confirmed / n | recall |
|---|---|---|
| amp1.0-1.5N | 67/79 | 84.8% |
| amp<1.0N | 0/31 | 0.0% |
| amp>=1.5N | 190/190 | 100.0% |
| hold400-800ms | 77/86 | 89.5% |
| hold<400ms | 47/62 | 75.8% |
| hold>=800ms | 133/152 | 87.5% |

Misses concentrate where the synthetic pull is below `trigger_delta_n` (1.0 N) or shorter than the confirm window — by construction of the rules.

## Safety scenarios (armed)

| scenario | expected fault latched | ended in SAFE_STOP | detection latency after condition onset (ms) |
|---|---|---|---|
| sensor_failure | 300/300 | 300 | n/a (latched at classification) |
| excessive_tension | 300/300 | 300 | mean 20.0, p95 20.0, max 20.0 (n=300) |
| motor_stall | 300/300 | 300 | mean 180.0, p95 180.0, max 180.0 (n=300) |
| comm_loss | 300/300 | 300 | mean 300.0, p95 300.0, max 300.0 (n=300) |

Latencies are on the simulated 20 ms tick and reflect the configured debounce/timeout values (over_tension_samples, stall_window_ms, comm_timeout_ms). Real e-stop / fault response must be measured on hardware.

## Evidence invariants (all records)

- Schema-valid: 2700/2700 validated
- Record hash + chain link verified: 2700/2700
- Deterministic replay OK: 2700/2700
- Hook-set/retrieval telemetry overlap: 0 records
- Actuation requested while disarmed: 0 records
- fish_landed values: {'unknown': 2700} (never inferred)

## Threshold sensitivity (classifier only, 150 seeds per scenario)

| trigger_delta_n (N) | decision_window_ms | recall (bite) | false-trigger rate (negatives) |
|---|---|---|---|
| 0.6 | 400 | 100.0% (150/150) | 2.1% (16/750) |
| 0.6 | 600 | 100.0% (150/150) | 0.0% (0/750) |
| 0.6 | 800 | 100.0% (150/150) | 0.0% (0/750) |
| 0.8 | 400 | 93.3% (140/150) | 2.0% (15/750) |
| 0.8 | 600 | 93.3% (140/150) | 0.0% (0/750) |
| 0.8 | 800 | 93.3% (140/150) | 0.0% (0/750) |
| 1.0 | 400 | 86.0% (129/150) | 1.7% (13/750) |
| 1.0 | 600 | 86.0% (129/150) | 0.0% (0/750) |
| 1.0 | 800 | 86.0% (129/150) | 0.0% (0/750) |
| 1.2 | 400 | 71.3% (107/150) | 0.8% (6/750) |
| 1.2 | 600 | 71.3% (107/150) | 0.0% (0/750) |
| 1.2 | 800 | 71.3% (107/150) | 0.0% (0/750) |

The sweep shows the recall / false-trigger trade-off on synthetic data only; choosing real thresholds requires calibrated bench data and labeled field recordings (docs/calibration.md).

## Implementation variants on the same synthetic signals

Two ports of `deterministic-baseline-v1` exist in the monorepo with different defaults (firmware C++ vs web-demo TS). Same signals, each with its own defaults:

| variant | recall (bite) | false-trigger rate |
|---|---|---|
| fw (python-port-of-firmware-cpp@2026-10-18) | 86.0% | 0.0% |
| web (python-port-of-web-demo-ts@2026-10-18) | 84.0% | 20.0% |

The web demo's own generator (`hookpilot-web-synth-v1`) differs from `hookpilot-synth-v1`, so these web-variant numbers are NOT the web demo's behaviour on its own scenarios. Unifying the two variants is an open item (docs/testing.md).

