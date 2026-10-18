import copy
import json
import os
import zipfile

import pytest

from evidence_service.canonical import seal
from evidence_service.engine import EventLog, build_event
from konnex_adapter import (DeviceIdentity, KonnexCliAdapter, MockKonnexAdapter, RealKonnexDisabled, TaskSpec,
                            build_bundle, check_safety, read_bundle, verify_bundle, verify_evidence)
from konnex_adapter.task import DEFAULT_SAFETY_CONSTRAINTS, task_result_from_record


def _spec(sc="bite", seed=1):
    return TaskSpec(task_id="t1", device=DeviceIdentity("hp-sim-001", "sim-py-0.1.0"), test_scenario=sc, seed=seed)


def _crit(res, name):
    return next(c for c in res["criteria"] if c["name"] == name)["status"]


def test_taskspec_roundtrip_and_fields():
    s = _spec()
    d = s.to_dict()
    assert d["task_type"] == "detect_bite_and_execute_hook_set"
    for k in ("task_id", "device", "firmware_version", "test_scenario", "allowed_trigger_params",
              "safety_constraints", "required_evidence_fields"):
        assert k in d
    assert "proposed" in d["task_schema_version"]
    assert TaskSpec.from_dict(json.loads(json.dumps(d))).to_dict() == d


def test_task_outputs_mapping():
    out = task_result_from_record(build_event("motor_stall", 2))
    assert set(out) == {"detection_result", "sensor_data_ref", "rationale", "actuator_telemetry", "safety_status",
                        "integrity_metadata", "final_status"}
    assert out["safety_status"]["faults"] == ["MOTOR_STALL"]
    assert out["integrity_metadata"]["signature"] is None


def test_mock_full_flow_accepts(tmp_path):
    ad = MockKonnexAdapter(str(tmp_path / "store"))
    jid = ad.submit_task(_spec("excessive_tension", 4))
    with pytest.raises(RuntimeError):
        ad.execute(jid)
    ad.attach_robot(jid, "hp-sim-001")
    for s in (4, 5, 6):
        ad.execute(jid, seed=s)
    b = ad.record(jid)
    rc = ad.prove(jid, b["path"])
    assert rc["label"] == "SIMULATION" and rc["onchain_tx"] is None
    res = ad.verify(jid)
    assert res["verdict"] == "accept", [c for c in res["criteria"] if c["status"] == "fail"]
    assert ad.get_job(jid)["status"] == "verified_mock"
    assert "does NOT prove the physical event" in res["trust_note"]


def test_attach_wrong_robot(tmp_path):
    ad = MockKonnexAdapter(str(tmp_path))
    jid = ad.submit_task(_spec())
    with pytest.raises(ValueError):
        ad.attach_robot(jid, "someone-else")


def test_bundle_tamper_after_prove_rejected(tmp_path):
    ad = MockKonnexAdapter(str(tmp_path))
    jid = ad.submit_task(_spec())
    ad.attach_robot(jid, "hp-sim-001")
    ad.execute(jid)
    b = ad.record(jid)
    ad.prove(jid, b["path"])
    with zipfile.ZipFile(b["path"], "a") as z:
        z.writestr("extra.txt", "x")
    res = ad.verify(jid)
    assert res["verdict"] == "reject"
    assert _crit(res, "bundle_files_intact") == "fail"


def test_bundle_is_deterministic_and_self_describing(tmp_path):
    log = EventLog()
    raw = str(tmp_path / "raw")
    for i in range(3):
        log.append(build_event("bite", i + 1, counter=i + 1, raw_dir=raw))
    a = build_bundle(log.records, str(tmp_path / "a.zip"), raw_dir=raw, task=_spec().to_dict(), job_id="j")
    b = build_bundle(log.records, str(tmp_path / "b.zip"), raw_dir=raw, task=_spec().to_dict(), job_id="j")
    assert a["bundle_sha256"] == b["bundle_sha256"]
    rb = read_bundle(str(tmp_path / "a.zip"))
    assert "NOT the official Konnex" in rb["manifest"]["layout_note"]
    assert rb["manifest"]["label"] == "SIMULATION" and len(rb["records"]) == 3
    assert verify_bundle(str(tmp_path / "a.zip"))["verdict"] == "accept"


def _records():
    log = EventLog()
    for i, sc in enumerate(["bite", "motor_stall"], 1):
        log.append(build_event(sc, 3, counter=i))
    return log.records


def _reseal(recs):
    prev = None
    for r in recs:
        seal(r, prev)
        prev = r["integrity"]["record_sha256"]
    return recs


@pytest.mark.parametrize("mutate,criterion", [
    (lambda r: r[0]["outcome"]["milestones"].__setitem__("fish_landed", "confirmed_by_observer"), "fish_landed_not_inferred"),
    (lambda r: r[0]["classification"]["thresholds"].__setitem__("trigger_delta_n", 0.01), "trigger_params_within_allowed"),
    (lambda r: r[0]["device"].__setitem__("firmware_version", "other"), "device_identity_matches_task"),
    (lambda r: r[0]["safety"].__setitem__("armed", False), "safety_constraints_respected"),
    (lambda r: r[1]["safety"].__setitem__("decision", "permit"), "safety_constraints_respected"),
    (lambda r: r[0]["actuation"].__setitem__("simulated", False), "simulation_labeled"),
    (lambda r: r[0]["classification"].__setitem__("result", "rejected_noise"), "classification_replay_matches"),
    (lambda r: r[0]["classification"].pop("rule_trace"), "required_fields_present"),
])
def test_each_criterion_can_fail(mutate, criterion):
    recs = copy.deepcopy(_records())
    mutate(recs)
    _reseal(recs)  # attacker re-seals: hash chain passes, the semantic criterion must still fail
    res = verify_evidence(recs, task=_spec().to_dict())
    assert _crit(res, "hash_chain_intact") == "pass"
    assert _crit(res, criterion) == "fail"
    assert res["verdict"] == "reject"


def test_hook_travel_and_over_tension_violations():
    r = copy.deepcopy(build_event("bite", 1))
    hook = [f for f in r["actuation"]["feedback"] if f["actuator"] == "hook_set"]
    hook[-1]["position"] = 99.0
    assert any("hook travel" in v for v in check_safety(r, DEFAULT_SAFETY_CONSTRAINTS))
    r2 = copy.deepcopy(build_event("bite", 1))
    ret = [f for f in r2["actuation"]["feedback"] if f["actuator"] == "retrieval"]
    for f in ret[2:5]:
        f["tension_n"] = 12.0
    v = check_safety(r2, DEFAULT_SAFETY_CONSTRAINTS)
    assert any("retrieval force limit" in x for x in v)


def test_unedited_records_pass_safety():
    for sc in ("bite", "excessive_tension", "motor_stall", "comm_loss", "manual_handling"):
        for s in range(1, 8):
            assert check_safety(build_event(sc, s), DEFAULT_SAFETY_CONSTRAINTS) == [], (sc, s)


def test_cli_adapter_refuses_by_default(monkeypatch):
    monkeypatch.delenv("HOOKPILOT_ENABLE_REAL_KONNEX", raising=False)
    k = KonnexCliAdapter()
    assert k.argv_attach("r1", "sn.x", "./keys.json") == ["konnex", "robot", "attach", "--robot-id", "r1", "--subnet", "sn.x", "--keys", "./keys.json"]
    assert k.argv_record("J", ["tension", "motor_current"], "./powp.zip") == ["konnex", "robot", "record", "--job-id", "J", "--sensors", "tension,motor_current", "--out", "./powp.zip"]
    assert k.argv_prove("J", "./powp.zip") == ["konnex", "prove", "--job-id", "J", "--bundle", "./powp.zip"]
    for call in (lambda: k.attach("r", "s", "k"), lambda: k.record("J", ["t"], "o"), lambda: k.prove("J", "b"),
                 lambda: k.submit_task(), lambda: k.verify()):
        with pytest.raises(RealKonnexDisabled):
            call()
    with pytest.raises(NotImplementedError):
        k.parse_job_result()


def test_cli_adapter_flag_without_binary(monkeypatch):
    monkeypatch.setenv("HOOKPILOT_ENABLE_REAL_KONNEX", "1")
    k = KonnexCliAdapter(binary="konnex-definitely-not-installed")
    ok, why = k.enabled_reason(k.binary)
    assert not ok and "not found" in why
    with pytest.raises(RealKonnexDisabled):
        k.prove("J", "b")


def test_cli_adapter_dry_run_with_fake_binary(monkeypatch, tmp_path):
    fake = tmp_path / "konnex"
    fake.write_text("#!/bin/sh\necho should-not-run\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOOKPILOT_ENABLE_REAL_KONNEX", "1")
    k = KonnexCliAdapter()
    out = k.prove("J", "b.zip")
    assert out["dry_run"] is True and out["argv"][0] == "konnex"
    with pytest.raises(NotImplementedError):
        k.submit_task()


def test_real_overlap_detected_but_handover_allowed():
    base = {"safety": {"armed": True, "decision": "permit", "faults": []}, "outcome": {"final_state": "MONITOR"}}
    req = [{"actuator": "hook_set", "t_ms": 100}, {"actuator": "retrieval", "t_ms": 200}]
    handover = [{"actuator": "hook_set", "t_ms": 100, "position": 0, "status": "moving"},
                {"actuator": "hook_set", "t_ms": 200, "position": 12, "status": "at_target"},
                {"actuator": "retrieval", "t_ms": 200, "position": 0, "status": "retrieving"}]
    r = dict(base, actuation={"requested": req, "feedback": handover})
    assert check_safety(r, DEFAULT_SAFETY_CONSTRAINTS) == []
    overlap = [{"actuator": "hook_set", "t_ms": 100, "position": 0, "status": "moving"},
               {"actuator": "retrieval", "t_ms": 200, "position": 0, "status": "retrieving"},
               {"actuator": "hook_set", "t_ms": 260, "position": 12, "status": "at_target"},
               {"actuator": "retrieval", "t_ms": 400, "position": 0.1, "status": "at_target"}]
    r2 = dict(base, actuation={"requested": req, "feedback": overlap})
    assert any("overlaps" in v for v in check_safety(r2, DEFAULT_SAFETY_CONSTRAINTS))


def test_assist_pause_is_opt_in():
    r = copy.deepcopy(build_event("bite", 1))
    ret = [f for f in r["actuation"]["feedback"] if f["actuator"] == "retrieval"]
    ret[5]["tension_n"] = 7.0  # winding while above 6 N
    assert not any("assist_pause" in v for v in check_safety(r, DEFAULT_SAFETY_CONSTRAINTS))
    assert any("assist_pause" in v for v in check_safety(r, dict(DEFAULT_SAFETY_CONSTRAINTS, assist_pause_n=6.0)))
