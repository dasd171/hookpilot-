#!/usr/bin/env python3
"""Generate the SIMULATION metrics report from actual runs (never hand-edited).

    python tools/gen_report.py [--seeds 300] [--sweep-seeds 150] [--out tests/reports]

Writes:
    tests/reports/simulation_metrics.json   all computed numbers + provenance
    tests/reports/simulation_report.md      human-readable summary of the same numbers
    tests/reports/per_scenario.csv          per-scenario counts
    tests/reports/threshold_sweep.csv       trigger x window sensitivity (classifier only)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "services", "evidence-service"))

from evidence_service import simmetrics  # noqa: E402
from evidence_service.classifier import CLASSIFIER_ID, IMPLEMENTATION, Thresholds  # noqa: E402
from evidence_service.safety import SafetyLimits  # noqa: E402
from evidence_service.synth import GENERATOR_ID, NEGATIVE_SCENARIOS, POSITIVE_SCENARIOS, SCENARIOS  # noqa: E402


def pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def ci(w):
    return f"[{100 * w[0]:.1f}%, {100 * w[1]:.1f}%]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300, help="seeds per scenario (seeds 1..N)")
    ap.add_argument("--sweep-seeds", type=int, default=150)
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "reports"))
    ap.add_argument("--validate-every", type=int, default=1, help="schema-validate every k-th record")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    seeds = range(1, a.seeds + 1)
    records = simmetrics.build_records(seeds)
    m = simmetrics.compute_metrics(records, validate_every=a.validate_every)
    sw = simmetrics.sweep(range(1, a.sweep_seeds + 1))
    vc = simmetrics.variant_comparison(range(1, a.sweep_seeds + 1))
    elapsed = time.time() - t0
    th = Thresholds.defaults("fw")
    prov = {
        "label": "SIMULATION – synthetic signals, Python reference engine; not field data",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(), "platform": platform.platform(),
        "generator": GENERATOR_ID, "classifier": CLASSIFIER_ID, "implementation": IMPLEMENTATION["fw"],
        "thresholds": th.to_record(), "safety_limits": SafetyLimits().to_record(),
        "seeds_per_scenario": a.seeds, "seed_range": [1, a.seeds], "scenarios": list(SCENARIOS),
        "positive_scenarios": list(POSITIVE_SCENARIOS), "negative_scenarios": list(NEGATIVE_SCENARIOS),
        "sweep_seeds_per_scenario": a.sweep_seeds, "runtime_s": round(elapsed, 1),
        "command": "python tools/gen_report.py " + " ".join(sys.argv[1:]),
    }
    out = {"provenance": prov, "metrics": m, "threshold_sweep": sw, "variant_comparison": vc}
    with open(os.path.join(a.out, "simulation_metrics.json"), "w") as f:
        json.dump(out, f, indent=2)
    with open(os.path.join(a.out, "per_scenario.csv"), "w", newline="") as f:
        w = csv.writer(f)
        results = sorted({k for v in m["per_scenario"].values() for k in v["results"]})
        w.writerow(["scenario", "n", "class_accuracy", "test_pass"] + results)
        for sc in SCENARIOS:
            v = m["per_scenario"][sc]
            w.writerow([sc, v["n"], v["class_accuracy"], v["test_pass"]] + [v["results"].get(r, 0) for r in results])
    with open(os.path.join(a.out, "threshold_sweep.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sw[0].keys()))
        w.writeheader()
        w.writerows(sw)

    rb, ft, lat, inv = m["recall_bite"], m["false_trigger"], m["latency_ms"], m["invariants"]
    L = []
    L.append("# HookPilot SIMULATION metrics report (auto-generated)\n")
    L.append("> **SIMULATION ONLY.** Every number below was computed by `tools/gen_report.py` from runs of the Python "
             "reference engine on synthetic `hookpilot-synth-v1` signals. The signal distributions were chosen by us "
             "(docs/synthetic-signals.md) and the classifier thresholds are provisional placeholders. These numbers "
             "say how the rules behave on our synthetic set; they say **nothing** about real fish, real water or real "
             "hardware. Do not quote them as field performance.\n")
    L.append(f"- Generated: {prov['generated_utc']} · Python {prov['python']} · runtime {prov['runtime_s']} s")
    L.append(f"- Classifier: `{CLASSIFIER_ID}` ({IMPLEMENTATION['fw']}), generator `{GENERATOR_ID}`")
    L.append(f"- Seeds per scenario: {a.seeds} (1..{a.seeds}); {len(records)} event records total")
    L.append(f"- Reproduce: `{prov['command']}`\n")
    L.append("## Headline (provisional thresholds, synthetic data)\n")
    L.append("| Metric | Value | 95% Wilson CI |\n|---|---|---|")
    L.append(f"| Recall, `bite` scenario | {pct(rb['recall'])} ({rb['confirmed']}/{rb['n']}) | {ci(rb['wilson95'])} |")
    ra = m["recall_all_positive_scenarios"]
    L.append(f"| Recall, all bite-bearing scenarios | {pct(ra['recall'])} ({ra['confirmed']}/{ra['n']}) | {ci(ra['wilson95'])} |")
    L.append(f"| False-trigger rate, all negatives | {pct(ft['rate'])} ({ft['false_triggers']}/{ft['n']}) | {ci(ft['wilson95'])} |")
    for s, d in ft["by_scenario"].items():
        L.append(f"| False-trigger rate, `{s}` | {pct(d['rate'])} ({d['false_triggers']}/{d['n']}) | {ci(d['wilson95'])} |")
    L.append("")
    L.append("## Latency of confirmed bites (ms, simulated clock, 20 ms ticks)\n")
    L.append("| Interval | n | mean | p50 | p95 | min | max |\n|---|---|---|---|---|---|---|")
    for k, v in lat.items():
        if v["n"]:
            L.append(f"| {k} | {v['n']} | {v['mean']} | {v['p50']} | {v['p95']} | {v['min']} | {v['max']} |")
    L.append("\nThe trigger→decision interval is dominated by the configured `decision_window_ms` "
             f"({th.decision_window_ms:.0f} ms); it is a design parameter, not a measured hardware latency.\n")
    L.append("## Per-scenario classification counts\n")
    results = sorted({k for v in m["per_scenario"].values() for k in v["results"]})
    L.append("| scenario | n | " + " | ".join(results) + " | class acc. | test pass |")
    L.append("|---|---|" + "---|" * len(results) + "---|---|")
    for sc in SCENARIOS:
        v = m["per_scenario"][sc]
        L.append(f"| {sc} | {v['n']} | " + " | ".join(str(v["results"].get(r, 0)) for r in results)
                 + f" | {pct(v['class_accuracy'])} | {v['test_pass']}/{v['n']} |")
    L.append("\n`test pass` = positives confirmed (and, armed, the expected safety fault latched); negatives produced "
             "no actuation and no unexpected fault. `class acc.` = exact expected label.\n")
    L.append("## Bite recall by ground-truth parameters\n")
    L.append("| bucket | confirmed / n | recall |\n|---|---|---|")
    for k, v in m["bite_recall_by_ground_truth"].items():
        L.append(f"| {k} | {v['confirmed']}/{v['n']} | {pct(v['recall'])} |")
    L.append(f"\nMisses concentrate where the synthetic pull is below `trigger_delta_n` ({th.trigger_delta_n} N) "
             "or shorter than the confirm window — by construction of the rules.\n")
    L.append("## Safety scenarios (armed)\n")
    L.append("| scenario | expected fault latched | ended in SAFE_STOP | detection latency after condition onset (ms) |\n|---|---|---|---|")
    for sc, v in m["safety_scenarios"].items():
        d = v["detection_latency_ms_after_condition_onset"]
        ds = f"mean {d['mean']}, p95 {d['p95']}, max {d['max']} (n={d['n']})" if d.get("n") else "n/a (latched at classification)"
        L.append(f"| {sc} | {v['expected_fault_latched']}/{v['n']} | {v['ended_in_SAFE_STOP']} | {ds} |")
    L.append("\nLatencies are on the simulated 20 ms tick and reflect the configured debounce/timeout values "
             "(over_tension_samples, stall_window_ms, comm_timeout_ms). Real e-stop / fault response must be measured on hardware.\n")
    L.append("## Evidence invariants (all records)\n")
    L.append(f"- Schema-valid: {inv['schema_valid']}/{inv['schema_validated']} validated")
    L.append(f"- Record hash + chain link verified: {inv['hash_and_chain_ok']}/{inv['records']}")
    L.append(f"- Deterministic replay OK: {inv['replay_ok']}/{inv['replayed']}")
    L.append(f"- Hook-set/retrieval telemetry overlap: {inv['simultaneous_actuation']} records")
    L.append(f"- Actuation requested while disarmed: {inv['actuation_while_disarmed']} records")
    L.append(f"- fish_landed values: {inv['fish_landed']} (never inferred)\n")
    L.append("## Threshold sensitivity (classifier only, "
             f"{a.sweep_seeds} seeds per scenario)\n")
    L.append("| trigger_delta_n (N) | decision_window_ms | recall (bite) | false-trigger rate (negatives) |\n|---|---|---|---|")
    for r in sw:
        L.append(f"| {r['trigger_delta_n']} | {r['window_ms']:.0f} | {pct(r['recall'])} ({r['tp']}/{r['n_pos']}) | "
                 f"{pct(r['false_trigger_rate'])} ({r['fp']}/{r['n_neg']}) |")
    L.append("\nThe sweep shows the recall / false-trigger trade-off on synthetic data only; choosing real thresholds "
             "requires calibrated bench data and labeled field recordings (docs/calibration.md).\n")
    L.append("## Implementation variants on the same synthetic signals\n")
    L.append("Two ports of `deterministic-baseline-v1` exist in the monorepo with different defaults "
             "(firmware C++ vs web-demo TS). Same signals, each with its own defaults:\n")
    L.append("| variant | recall (bite) | false-trigger rate |\n|---|---|---|")
    for v, d in vc.items():
        L.append(f"| {v} ({IMPLEMENTATION[v]}) | {pct(d['recall_bite'])} | {pct(d['false_trigger_rate'])} |")
    L.append("\nThe web demo's own generator (`hookpilot-web-synth-v1`) differs from `hookpilot-synth-v1`, so these "
             "web-variant numbers are NOT the web demo's behaviour on its own scenarios. Unifying the two variants is "
             "an open item (docs/testing.md).\n")
    with open(os.path.join(a.out, "simulation_report.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"SIMULATION report written to {a.out} in {elapsed:.1f}s")
    print(f"recall(bite)={rb['recall']} ({rb['confirmed']}/{rb['n']})  false_trigger={ft['rate']} ({ft['false_triggers']}/{ft['n']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
