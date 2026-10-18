"""Labeled SIMULATION scenario set: computes metrics from actual runs and checks the invariants.
Recall / false-trigger values are asserted only for internal consistency, never against invented targets.
The full report (more seeds) is produced by `python tools/gen_report.py`."""
import json
import os

import pytest

from evidence_service import simmetrics
from evidence_service.synth import EXPECTED_FAULT, NEGATIVE_SCENARIOS

SEEDS = range(1, 61)


@pytest.fixture(scope="module")
def metrics():
    recs = simmetrics.build_records(SEEDS)
    return simmetrics.compute_metrics(recs, validate_every=3), recs


def test_metrics_internally_consistent(metrics):
    m, recs = metrics
    rb = m["recall_bite"]
    assert rb["n"] == len(SEEDS) and 0 <= rb["recall"] <= 1
    assert abs(rb["recall"] - rb["confirmed"] / rb["n"]) < 1e-4
    lo, hi = rb["wilson95"]
    assert lo <= rb["recall"] <= hi
    ft = m["false_trigger"]
    assert ft["n"] == len(SEEDS) * len(NEGATIVE_SCENARIOS)
    assert ft["false_triggers"] == sum(d["false_triggers"] for d in ft["by_scenario"].values())


def test_safety_scenarios_always_latch_expected_fault(metrics):
    m, _ = metrics
    for sc in EXPECTED_FAULT:
        s = m["safety_scenarios"][sc]
        assert s["expected_fault_latched"] == s["n"] == s["ended_in_SAFE_STOP"], (sc, s)


def test_evidence_invariants(metrics):
    m, recs = metrics
    inv = m["invariants"]
    assert inv["schema_valid"] == inv["schema_validated"]
    assert inv["hash_and_chain_ok"] == inv["records"] == len(recs)
    assert inv["replay_ok"] == inv["replayed"]
    assert inv["simultaneous_actuation"] == 0 and inv["actuation_while_disarmed"] == 0
    assert inv["fish_landed"] == {"unknown": len(recs)}


def test_deterministic(metrics):
    m, _ = metrics
    again = simmetrics.compute_metrics(simmetrics.build_records(SEEDS), validate_every=3)
    assert again["per_scenario"] == m["per_scenario"] and again["latency_ms"] == m["latency_ms"]


def test_sweep_monotone_in_trigger():
    rows = simmetrics.sweep(range(1, 41), triggers=(0.6, 1.0, 1.4), windows=(600.0,))
    rec = [r["recall"] for r in rows]
    assert rec == sorted(rec, reverse=True)  # higher trigger can only lose bites on this set


def test_committed_report_is_consistent(repo_root):
    p = os.path.join(repo_root, "tests", "reports", "simulation_metrics.json")
    if not os.path.exists(p):
        pytest.skip("run `python tools/gen_report.py` first")
    d = json.load(open(p))
    assert d["provenance"]["label"].startswith("SIMULATION")
    rb = d["metrics"]["recall_bite"]
    assert abs(rb["recall"] - rb["confirmed"] / rb["n"]) < 1e-4
    n = d["provenance"]["seeds_per_scenario"]
    # spot-check reproducibility of the committed report on its first 30 seeds is cheap; full check is slow
    sub = simmetrics.compute_metrics(simmetrics.build_records(range(1, 31), scenarios=("bite",)), validate_every=10)
    assert sub["recall_bite"]["n"] == 30 and n >= 30


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("HOOKPILOT_SLOW") != "1", reason="set HOOKPILOT_SLOW=1 to recompute the full report")
def test_committed_report_reproduces_exactly(repo_root):
    d = json.load(open(os.path.join(repo_root, "tests", "reports", "simulation_metrics.json")))
    n = d["provenance"]["seeds_per_scenario"]
    m = simmetrics.compute_metrics(simmetrics.build_records(range(1, n + 1)), validate_every=10)
    assert m["per_scenario"] == d["metrics"]["per_scenario"]
