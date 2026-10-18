"""Schema, hash chain, raw data integrity, replay, export, engine invariants."""
import copy
import csv
import json
import os

import pytest

from evidence_service.chain import verify_records
from evidence_service.engine import EventLog, build_event, run_scenario
from evidence_service.export import to_csv
from evidence_service.replay import replay
from evidence_service.safety import F, SafetyController, SafetyLimits
from evidence_service.schema import validate_record
from evidence_service.synth import SCENARIOS


@pytest.fixture
def chain(tmp_path):
    log = EventLog()
    for i, sc in enumerate(["bite", "rod_vibration", "motor_stall", "sensor_failure"], 1):
        log.append(build_event(sc, 3, counter=i, raw_dir=str(tmp_path / "raw")))
    return log.records, str(tmp_path)


def test_all_scenarios_schema_valid():
    for sc in SCENARIOS:
        for armed in (True, False):
            r = build_event(sc, 2, armed=armed)
            r["integrity"]["record_sha256"] = "x"
            assert validate_record(r) == [], sc


def test_schema_catches_errors():
    r = build_event("bite", 1)
    bad = copy.deepcopy(r)
    bad["outcome"]["milestones"]["fish_landed"] = "yes"
    del bad["device"]
    bad["timestamp"]["start_utc"] = "yesterday"
    errs = validate_record(bad)
    assert any("fish_landed" in e for e in errs) and any("device" in e for e in errs) and any("start_utc" in e for e in errs)


def test_chain_verifies(chain):
    recs, d = chain
    assert all(c.ok for c in verify_records(recs, raw_base_dir=d))
    assert recs[0]["integrity"]["prev_record_sha256"] is None
    assert recs[1]["integrity"]["prev_record_sha256"] == recs[0]["integrity"]["record_sha256"]


def test_tamper_field_breaks_hash(chain):
    recs, d = chain
    recs = copy.deepcopy(recs)
    recs[1]["classification"]["result"] = "bite_candidate_confirmed"
    c = verify_records(recs, raw_base_dir=d)
    assert not c[1].hash_ok and c[0].ok and c[2].chain_ok


def test_reorder_and_delete_break_chain(chain):
    recs, d = chain
    assert not all(c.chain_ok for c in verify_records([recs[1], recs[0]] + recs[2:], raw_base_dir=d))
    assert not verify_records(recs[:1] + recs[2:], raw_base_dir=d)[1].chain_ok


def test_resealing_tampered_record_still_breaks_successor(chain):
    from evidence_service.canonical import seal
    recs, d = chain
    recs = copy.deepcopy(recs)
    recs[1]["classification"]["reason"] = "edited after the fact"
    seal(recs[1], recs[0]["integrity"]["record_sha256"])
    c = verify_records(recs, raw_base_dir=d)
    assert c[1].hash_ok and not c[2].chain_ok


def test_raw_file_tamper_detected(chain):
    recs, d = chain
    p = os.path.join(d, recs[0]["sensor_data"]["raw_data_ref"])
    with open(p, "a") as f:
        f.write("9999,1\n")
    c = verify_records(recs, raw_base_dir=d)
    assert c[0].raw_ok is False and not c[0].ok


def test_replay_matches_and_detects_tamper(chain):
    recs, d = chain
    for r in recs:
        assert replay(r, raw_base_dir=d)["ok"], r["event_id"]
    t = copy.deepcopy(recs[0])
    t["sensor_data"]["samples"][10][1] += 0.5  # pre-actuation sample
    assert replay(t)["signal_match"] is False and not replay(t)["ok"]
    t2 = copy.deepcopy(recs[1])
    t2["classification"]["result"] = "rejected_transient"
    assert replay(t2)["classification_match"] is False
    t3 = copy.deepcopy(recs[0])
    t3["source"]["seed"] = 4
    assert replay(t3)["signal_match"] is False


def test_replay_bench_record_without_seed():
    r = build_event("bite", 8)
    r["source"] = {"mode": "bench", "scenario": None, "ground_truth_label": None}
    rp = replay(r)
    assert rp["signal_match"] is None and rp["classification_match"] is True and rp["ok"]


def test_replay_from_raw_file_when_not_inlined(tmp_path):
    r = build_event("rod_vibration", 4, raw_dir=str(tmp_path / "raw"), inline_samples=False)
    assert "samples" not in r["sensor_data"]
    assert replay(r, raw_base_dir=str(tmp_path))["ok"]


def test_export_csv(chain, tmp_path):
    recs, _ = chain
    out = tmp_path / "x.csv"
    assert to_csv(recs, str(out)) == 4
    rows = list(csv.DictReader(open(out)))
    assert rows[2]["faults"] == "MOTOR_STALL" and rows[0]["fish_landed"] == "unknown"


# ---- engine / safety invariants -------------------------------------------------------------
@pytest.mark.parametrize("sc", SCENARIOS)
def test_engine_invariants(sc):
    for seed in range(1, 21):
        run = run_scenario(sc, seed, trace=True)
        # hook-set and retrieval are never active in the same tick (checked on the full tick trace)
        assert not any(t["hook"]["active"] and t["retr"]["active"] for t in run.trace)
        for r in run.records:
            m = r["outcome"]["milestones"]
            assert m["fish_landed"] == "unknown"
            assert r["source"]["mode"] == "simulation" and r["actuation"]["simulated"] is True
            if r["safety"]["faults"]:
                assert r["outcome"]["final_state"] == "SAFE_STOP" and r["safety"]["decision"] == "safe_stop"
            if m["hook_set_completed"]:
                assert m["bite_detected"]
            for f, t in r["safety"]["fault_latched_t_ms"].items():
                assert f in r["safety"]["faults"] and 0 <= t <= 8000
            # retrieval is only ever requested after the hook-set stroke + the minimum gap
            req = r["actuation"]["requested"]
            ret = [q["t_ms"] for q in req if q["actuator"] == "retrieval"]
            stroke = [f["t_ms"] for f in r["actuation"]["feedback"]
                      if f["actuator"] == "hook_set" and f["status"] == "at_target"]
            if ret:
                assert stroke and ret[0] - stroke[0] >= SafetyLimits().hook_to_retrieval_gap_ms


def test_disarmed_never_actuates():
    for sc in ("bite", "excessive_tension", "motor_stall"):
        for seed in range(1, 11):
            run = run_scenario(sc, seed, armed=False, trace=True)
            assert not any(t["cmds"] for t in run.trace)
            assert not any(t["hook"]["active"] or t["retr"]["active"] for t in run.trace)
            for r in run.records:
                assert r["actuation"]["requested"] == [] and r["actuation"]["feedback"] == []
                assert r["outcome"]["final_state"] in ("IDLE", "SAFE_STOP")
                assert r["safety"]["armed"] is False
            first = run.records[0]
            if first["classification"]["result"] == "bite_candidate_confirmed":
                assert first["outcome"]["stop_reason"].startswith("disarmed_observation_only")


class _In:
    """Minimal SafetyInputs for unit tests."""
    def __init__(self, raw=1.0, valid=True, health="ok"):
        from evidence_service.engine import Feedback, SafetyInputs, empty_sample
        s = empty_sample()
        s.raw, s.valid, s.health = raw, valid, health
        self.v = SafetyInputs(now_ms=0, sample=s, hook=Feedback(), retrieval=Feedback(), bus_voltage_v=11.4)

    def __getattr__(self, k):
        return getattr(self.v, k)


def test_safety_controller_rules():
    s = SafetyController(SafetyLimits())
    i = _In().v
    assert not s.permit("hook_set", 12.0, 60.0, i).permitted          # disarmed
    s.update(i)
    assert s.arm(i)[0]
    assert s.permit("hook_set", 12.0, 60.0, i).permitted
    assert not s.permit("hook_set", 15.5, 60.0, i).permitted          # travel limit
    assert not s.permit("hook_set", 12.0, 500.0, i).permitted         # speed limit
    i.hook.active = True
    s.update(i)                                                       # hook seen active at t=0
    d = s.permit("retrieval", 1.0, 0.5, i)
    assert not d.permitted and "mutual exclusion" in d.reason
    i.hook.active = False
    i.now_ms = 20
    s.update(i)                                                       # hook seen inactive at t=20
    i.now_ms = 60
    d = s.permit("retrieval", 1.0, 0.5, i)
    assert not d.permitted and "hook_to_retrieval_gap_ms" in d.reason  # 40 ms < 100 ms gap
    i.now_ms = 120
    assert s.permit("retrieval", 1.0, 0.5, i).permitted
    i.sample.raw = 8.5
    assert not s.permit("retrieval", 1.0, 0.5, i).permitted           # retrieval force limit
    # over-tension debounce: 2 consecutive samples above hard max
    i.sample.raw = 20.5
    i.now_ms = 140
    assert s.update(i) == 0
    i.now_ms = 160
    assert s.update(i) & F["OVER_TENSION"] and s.latch_time_ms("OVER_TENSION") == 160 and not s.armed
    i.sample.raw = 1.0
    i.now_ms = 180
    s.update(i)
    assert s.clear_faults() and s.faults == []                        # condition gone -> reset ok
    # WATCHDOG_RESET is acknowledgement-only; COMM_LOSS only latches while armed
    s.latch_fault(F["WATCHDOG_RESET"])
    assert not s.arm(i)[0] and s.clear_faults() and s.arm(i)[0]
    s.disarm()
    i.comm_link_ok = False
    assert s.update(i) == 0 and s.active & F["COMM_LOSS"] and not s.arm(i)[0]
