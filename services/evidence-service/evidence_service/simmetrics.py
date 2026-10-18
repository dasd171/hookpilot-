"""Labeled SIMULATION scenario set + metric computation for deterministic-baseline-v1.

Everything here is computed from actual runs of the Python reference engine on
hookpilot-synth-v1 signals. The numbers describe behaviour on SYNTHETIC data whose
distributions we chose (docs/synthetic-signals.md); they are NOT field performance.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

from .chain import verify_records
from .classifier import Thresholds, classify
from .engine import EventLog, build_events
from .replay import replay
from .synth import (EXPECTED_CLASSIFICATION, EXPECTED_FAULT, NEGATIVE_SCENARIOS, POSITIVE_SCENARIOS, SCENARIOS,
                    generate)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def pct(p):
        i = min(len(s) - 1, max(0, int(math.ceil(p * len(s))) - 1))
        return s[i]
    return {"n": len(s), "mean": round(statistics.fmean(s), 3), "p50": pct(0.5), "p95": pct(0.95),
            "min": s[0], "max": s[-1]}


def build_records(seeds: range, scenarios=SCENARIOS, armed: bool = True) -> list[dict]:
    """Every record of every run (multi-event runs included), hash-chained in run order."""
    log = EventLog()
    n = 0
    for sc in scenarios:
        for seed in seeds:
            for r in build_events(sc, seed, armed=armed, counter=n + 1):
                n += 1
                log.append(r)
    return log.records


def primary_records(records: list[dict]) -> list[dict]:
    """First record of each (scenario, seed) run: the decision on the first candidate (or the safety stop /
    end-of-window no_event summary). Per-scenario classification metrics are computed on these."""
    seen, out = set(), []
    for r in records:
        k = (r["source"]["scenario"], r["source"]["seed"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _overlap(rec: dict) -> bool:
    """Hook-set moving strictly inside a retrieval active interval (request -> terminal feedback)."""
    req = [q["t_ms"] for q in rec["actuation"]["requested"] if q["actuator"] == "retrieval" and q.get("decision") == "permit"]
    if not req:
        return False
    r0 = req[0]
    fb = rec["actuation"]["feedback"]
    ends = [f["t_ms"] for f in fb if f["actuator"] == "retrieval" and f["t_ms"] >= r0
            and f["status"] in ("at_target", "stopped", "stalled")]
    r1 = ends[0] if ends else float("inf")
    return any(r0 < f["t_ms"] < r1 for f in fb if f["actuator"] == "hook_set" and f["status"] == "moving")


def _fault_latch_time(rec: dict, fault: str) -> float | None:
    t = (rec["safety"].get("fault_latched_t_ms") or {}).get(fault)
    return None if t is None else float(t)


def compute_metrics(records: list[dict], *, validate_every: int = 1, replay_every: int = 1) -> dict:
    by_sc: dict[str, list[dict]] = defaultdict(list)
    for r in primary_records(records):
        by_sc[r["source"]["scenario"]].append(r)

    per = {}
    for sc, rs in by_sc.items():
        c = Counter(r["classification"]["result"] for r in rs)
        conf = sum(1 for r in rs if r["classification"]["result"] == "bite_candidate_confirmed")
        cm = sum(1 for r in rs if r["classification"]["result"] in EXPECTED_CLASSIFICATION[sc])
        per[sc] = {"n": len(rs), "results": dict(sorted(c.items())), "confirmed": conf,
                   "class_match": cm, "class_accuracy": round(cm / len(rs), 4),
                   "test_pass": sum(1 for r in rs if r["outcome"]["test_outcome"] == "pass")}

    # recall
    bite = by_sc.get("bite", [])
    k = sum(1 for r in bite if r["classification"]["result"] == "bite_candidate_confirmed")
    pos = [r for s in POSITIVE_SCENARIOS for r in by_sc.get(s, [])]
    kp = sum(1 for r in pos if r["classification"]["result"] == "bite_candidate_confirmed")
    neg = [r for s in NEGATIVE_SCENARIOS for r in by_sc.get(s, [])]
    fneg = sum(1 for r in neg if r["classification"]["result"] == "bite_candidate_confirmed")
    ft_by = {s: {"n": len(by_sc.get(s, [])), "false_triggers": per.get(s, {}).get("confirmed", 0)} for s in NEGATIVE_SCENARIOS}
    for s, d in ft_by.items():
        d["rate"] = round(d["false_triggers"] / d["n"], 4) if d["n"] else None
        d["wilson95"] = [round(x, 4) for x in wilson(d["false_triggers"], d["n"])]

    # latency (confirmed bites): trigger->decision and true onset->decision
    lat_trig, lat_onset = [], []
    for r in bite:
        cl = r["classification"]
        if cl["result"] == "bite_candidate_confirmed":
            lat_trig.append(cl["decision_t_ms"] - cl["trigger_t_ms"])
            sig = generate("bite", r["source"]["seed"])
            lat_onset.append(cl["decision_t_ms"] - sig.params["onset_ms"])

    # bite miss analysis by ground-truth amplitude / hold
    buckets = defaultdict(lambda: [0, 0])
    for r in bite:
        p = generate("bite", r["source"]["seed"]).params
        a = "amp<1.0N" if p["amp_n"] < 1.0 else ("amp1.0-1.5N" if p["amp_n"] < 1.5 else "amp>=1.5N")
        h = "hold<400ms" if p["hold_ms"] < 400 else ("hold400-800ms" if p["hold_ms"] < 800 else "hold>=800ms")
        for key in (a, h):
            buckets[key][1] += 1
            buckets[key][0] += r["classification"]["result"] == "bite_candidate_confirmed"
    miss = {k: {"confirmed": v[0], "n": v[1], "recall": round(v[0] / v[1], 4) if v[1] else None}
            for k, v in sorted(buckets.items())}

    # safety scenarios
    safety = {}
    for sc, faults in EXPECTED_FAULT.items():
        rs = by_sc.get(sc, [])
        hit = [r for r in rs if any(f in r["safety"]["faults"] for f in faults)]
        safe_state = sum(1 for r in hit if r["outcome"]["final_state"] == "SAFE_STOP")
        lat = []
        for r in hit:
            if sc in ("motor_stall", "comm_loss"):
                # the scenario fault is injected at the end of the first tick with (t - retrieval start) >=
                # fault_after_retrieval_start_ms and acts from the next tick (docs/engine-spec.md)
                p = generate(sc, r["source"]["seed"]).params
                req = [q for q in r["actuation"]["requested"] if q["actuator"] == "retrieval"]
                if req:
                    t_inj = req[0]["t_ms"] + 20.0 * math.ceil(p["fault_after_retrieval_start_ms"] / 20.0)
                    tl = _fault_latch_time(r, faults[0])
                    if tl is not None:
                        lat.append(tl - (t_inj + 20.0))
            elif sc == "excessive_tension":
                lim = r["safety"]["interlocks"]["retrieval_force_limit_n"]
                hard = r["safety"]["interlocks"]["hard_max_tension_n"]
                req = [q["t_ms"] for q in r["actuation"]["requested"] if q["actuator"] == "retrieval"]
                t_r = req[0] if req else float("inf")
                first = next((t for t, x in r["sensor_data"]["samples"] if x > hard or (t > t_r and x > lim)), None)
                tl = _fault_latch_time(r, "OVER_TENSION")
                if first is not None and tl is not None:
                    lat.append(tl - first)
        safety[sc] = {"n": len(rs), "expected_fault_latched": len(hit), "rate": round(len(hit) / len(rs), 4) if rs else None,
                      "ended_in_SAFE_STOP": safe_state,
                      "detection_latency_ms_after_condition_onset": _stats(lat)}

    # invariants on every record
    sim_overlap = sum(1 for r in records if _overlap(r))
    fish = Counter(r["outcome"]["milestones"]["fish_landed"] for r in records)
    act_unarmed = sum(1 for r in records if r["actuation"]["requested"] and not r["safety"]["armed"])
    sub = records[::validate_every]
    checks = verify_records(sub) if validate_every == 1 else None
    if checks is None:  # chain over the full log, schema over a subsample
        full = verify_records(records)
        schema_ok = sum(1 for i, c in enumerate(full) if i % validate_every != 0 or not c.schema_errors)
        checks = full
    else:
        schema_ok = sum(1 for c in checks if not c.schema_errors)
    chain_ok = sum(1 for c in checks if c.hash_ok and c.chain_ok)
    rp = [replay(r) for r in records[::replay_every]]
    inv = {"records": len(records), "schema_validated": len(records[::validate_every]),
           "schema_valid": schema_ok if validate_every == 1 else sum(1 for c in checks[::validate_every] if not c.schema_errors),
           "hash_and_chain_ok": chain_ok, "replayed": len(rp), "replay_ok": sum(1 for x in rp if x["ok"]),
           "simultaneous_actuation": sim_overlap, "actuation_while_disarmed": act_unarmed,
           "fish_landed": dict(fish)}

    return {
        "per_scenario": per,
        "recall_bite": {"confirmed": k, "n": len(bite), "recall": round(k / len(bite), 4) if bite else None,
                        "wilson95": [round(x, 4) for x in wilson(k, len(bite))]},
        "recall_all_positive_scenarios": {"confirmed": kp, "n": len(pos), "recall": round(kp / len(pos), 4) if pos else None,
                                          "wilson95": [round(x, 4) for x in wilson(kp, len(pos))]},
        "false_trigger": {"false_triggers": fneg, "n": len(neg), "rate": round(fneg / len(neg), 4) if neg else None,
                          "wilson95": [round(x, 4) for x in wilson(fneg, len(neg))], "by_scenario": ft_by},
        "latency_ms": {"trigger_to_decision": _stats(lat_trig), "true_onset_to_decision": _stats(lat_onset)},
        "bite_recall_by_ground_truth": miss,
        "safety_scenarios": safety,
        "invariants": inv,
    }


def sweep(seeds: range, triggers=(0.6, 0.8, 1.0, 1.2), windows=(400.0, 600.0, 800.0)) -> list[dict]:
    """Classifier-only sensitivity sweep (no actuation) over trigger_delta_n x decision window."""
    sigs = {(s, seed): generate(s, seed).samples() for s in ("bite",) + NEGATIVE_SCENARIOS for seed in seeds}
    rows = []
    for trg in triggers:
        for w in windows:
            th = Thresholds.defaults("fw")
            th.trigger_delta_n = trg
            th.decision_window_ms = w
            tp = sum(classify(sigs[("bite", sd)], th).result == "bite_candidate_confirmed" for sd in seeds)
            fp = sum(classify(sigs[(s, sd)], th).result == "bite_candidate_confirmed" for s in NEGATIVE_SCENARIOS for sd in seeds)
            nn = len(NEGATIVE_SCENARIOS) * len(seeds)
            rows.append({"trigger_delta_n": trg, "window_ms": w, "recall": round(tp / len(seeds), 4),
                         "false_trigger_rate": round(fp / nn, 4), "tp": tp, "n_pos": len(seeds), "fp": fp, "n_neg": nn})
    return rows
