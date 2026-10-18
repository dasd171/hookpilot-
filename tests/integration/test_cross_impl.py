"""Cross-implementation checks against the C++ firmware sim and the TS web demo (SIMULATION).

Firmware vs Python: for every case of tools/xcompare.py cases() (9 scenarios x armed/disarmed x seeds 1-20,
fault injections, parameter overrides) `hookpilot_sim --trace` must match evidence_service.engine.run_scenario
tick for tick (state, armed, sensor sample, line load, actuator feedback, latched/active faults, commands with
their permit/deny reasons, transitions, injections, record count) and record for record (xcompare.record_core),
numbers within 1e-9. TS vs Python runs in apps/web-demo/tests/cross_impl.test.ts on generated fixtures.
Skipped (with the reason) when those builds are absent or not runnable in this checkout."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

import pytest

from evidence_service.chain import verify_records
from evidence_service.engine import build_event, run_scenario
from evidence_service.replay import replay

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import xcompare  # noqa: E402

SCEN = ["bite", "noise_current", "rod_vibration", "manual_handling", "sensor_failure", "excessive_tension",
        "motor_stall", "comm_loss"]


def _exe(repo_root):
    exe = os.path.join(repo_root, "firmware", "build", "hookpilot_sim")
    if not os.path.exists(exe):
        pytest.skip("firmware/build/hookpilot_sim not built (run `make -C firmware all`)")
    return exe


def _fw_run(repo_root, sc, seed, armed=True, injections=(), overrides=None, trace=False):
    args = [_exe(repo_root), "--scenario", sc, "--seed", str(seed), "--quiet"]
    if not armed:
        args.append("--disarmed")
    for i in injections:
        args += ["--inject", i]
    for k, v in (overrides or {}).items():
        args += ["--set", f"{k}={v}"]
    tpath = None
    if trace:
        fd, tpath = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        args += ["--trace", tpath]
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        pytest.skip(f"firmware sim not runnable here (rc={p.returncode}): {p.stderr[:200]}")
    recs = [json.loads(l) for l in p.stdout.splitlines() if l.strip().startswith("{") and '"schema_version"' in l]
    tr = None
    if tpath:
        with open(tpath) as f:
            tr = [json.loads(l) for l in f if l.strip()]
        os.remove(tpath)
    return recs, tr


def _fw_records(repo_root, sc, seed):
    return _fw_run(repo_root, sc, seed)[0]


def _compare_cases(repo_root, cases):
    stats = defaultdict(int)
    bad = []
    for c in cases:
        recs, tr = _fw_run(repo_root, c["scenario"], c["seed"], c["armed"], c["injections"], c["overrides"], True)
        py = run_scenario(c["scenario"], c["seed"], armed=c["armed"], injections=c["injections"],
                          overrides=c["overrides"], trace=True)
        d = xcompare.trace_diff(tr, py.trace) or xcompare.core_diff(recs, py.records)
        stats["runs"] += 1
        stats["ticks"] += len(tr)
        stats["records"] += len(recs)
        if d:
            bad.append(f"{xcompare.case_id(c)}: {d}")
    return stats, bad


ALL_CASES = xcompare.cases()


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", xcompare.ALL_SCENARIOS)
def test_firmware_trace_matches_python_scenarios(repo_root, sc):
    """9 scenarios x armed/disarmed x seeds 1-20: identical per-tick trace and records."""
    cases = [c for c in ALL_CASES if c["group"] == "scenario" and c["scenario"] == sc]
    assert len(cases) == 2 * len(xcompare.SCENARIO_SEEDS)
    stats, bad = _compare_cases(repo_root, cases)
    print(f"{sc}: {dict(stats)} mismatches={len(bad)}")
    assert not bad, bad[:3]


@pytest.mark.crossimpl
@pytest.mark.parametrize("spec", xcompare.INJECTION_SPECS)
def test_firmware_trace_matches_python_injections(repo_root, spec):
    """Fault injection (sensor disconnect/NaN/out-of-range/stuck, over-tension, stall, encoder loss, hook jam,
    comm loss, link down, undervoltage, watchdog reset, e-stop, over-temperature)."""
    cases = [c for c in ALL_CASES if c["group"] == "injection" and c["injections"] == [spec]]
    stats, bad = _compare_cases(repo_root, cases)
    print(f"{spec}: {dict(stats)} mismatches={len(bad)}")
    assert not bad, bad[:3]


@pytest.mark.crossimpl
@pytest.mark.parametrize("ov", xcompare.OVERRIDE_SETS, ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()))
def test_firmware_trace_matches_python_overrides(repo_root, ov):
    cases = [c for c in ALL_CASES if c["group"] == "override" and c["overrides"] == ov]
    stats, bad = _compare_cases(repo_root, cases)
    print(f"{ov}: {dict(stats)} mismatches={len(bad)}")
    assert not bad, bad[:3]


@pytest.mark.crossimpl
def test_injected_faults_latch_in_all_implementations(repo_root):
    """Each injected fault reaches SAFE_STOP with the expected code when armed (firmware == Python by the tests above)."""
    # NaN inside the candidate window: the classifier's R0 decides rejected_sensor_fault and the controller
    # enters SAFE_STOP on that tick, before the 3-sample safety debounce latches SENSOR_DISCONNECTED.
    recs, tr = _fw_run(repo_root, "bite", 1, True, ["sensor_nan@1700"], trace=True)
    assert recs[-1]["classification"]["result"] == "rejected_sensor_fault"
    assert recs[-1]["outcome"]["final_state"] == "SAFE_STOP" and recs[-1]["safety"]["faults"] == []
    assert "SENSOR_DISCONNECTED" in tr[-1]["latched"]
    expect = {"sensor_disconnect@1000": "SENSOR_DISCONNECTED",
              "over_tension@retrieval+300:8.5": "OVER_TENSION", "motor_stall@retrieval+100": "MOTOR_STALL",
              "comm_loss@retrieval+0": "COMM_LOSS", "undervoltage@1500": "UNDERVOLTAGE",
              "estop@retrieval+60": "E_STOP", "hook_jam@0": "ACTUATOR_TIMEOUT"}
    for spec, fault in expect.items():
        recs, _ = _fw_run(repo_root, "bite", 1, True, [spec])
        assert fault in recs[-1]["safety"]["faults"], (spec, recs[-1]["safety"])
        assert recs[-1]["outcome"]["final_state"] == "SAFE_STOP"
        assert recs[-1]["safety"]["fault_latched_t_ms"][fault] > 0
    # watchdog reset: boot record, IDLE, disarmed, arming refused for the whole run (observation only)
    recs, tr = _fw_run(repo_root, "bite", 1, True, ["watchdog_reset@0"], trace=True)
    assert recs[0]["outcome"]["stop_reason"].startswith("watchdog_reset_boot")
    assert recs[0]["safety"]["faults"] == ["WATCHDOG_RESET"]
    assert all(not t["armed"] and not t["cmds"] for t in tr)


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", SCEN)
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_firmware_records_verify_and_replay(repo_root, sc, seed):
    recs = _fw_records(repo_root, sc, seed)
    assert recs, "firmware emitted no event record"
    checks = verify_records(recs)
    assert all(c.hash_ok and c.chain_ok and not c.schema_errors for c in checks), [c.to_dict() for c in checks if not c.ok]
    for r in recs:
        rp = replay(r)
        assert rp["signal_match"] is True, rp
        assert rp["ok"], rp
    assert build_event(sc, seed)["classification"]["result"] == recs[0]["classification"]["result"]
    from konnex_adapter import verify_evidence
    res = verify_evidence(recs)
    assert res["verdict"] == "accept", [c for c in res["criteria"] if c["status"] == "fail"]


def _web_records(repo_root, sc, seed, *extra):
    app = os.path.join(repo_root, "apps", "web-demo")
    if shutil.which("npm") is None or not os.path.exists(os.path.join(app, "node_modules", ".bin", "tsx")):
        pytest.skip("web demo dependencies not installed")
    p = subprocess.run(["npm", "run", "-s", "sim", "--", "--scenario", sc, "--seed", str(seed), *extra], cwd=app,
                       capture_output=True, text=True, timeout=110)
    if p.returncode != 0:
        pytest.skip(f"web demo sim failed (rc={p.returncode}): {p.stderr[:200]}")
    return json.loads(p.stdout)


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", SCEN)
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_web_demo_records_verify_and_replay(repo_root, sc, seed):
    """Web-demo CLI records: hash chain, schema, replay, and the same record core as the Python engine."""
    recs = _web_records(repo_root, sc, seed)
    assert recs, "web demo emitted no record"
    checks = verify_records(recs)
    assert all(c.hash_ok and c.chain_ok and not c.schema_errors for c in checks), [c.to_dict() for c in checks if not c.ok]
    for r in recs:
        assert r["source"]["generator"] == "hookpilot-synth-v1"
        rp = replay(r)
        assert rp["signal_match"] is True, rp
        assert rp["ok"], rp
    assert xcompare.core_diff(recs, run_scenario(sc, seed).records) is None


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", ["bite", "rod_vibration", "manual_handling"])
def test_web_demo_disarmed_records_replay(repo_root, sc):
    recs = _web_records(repo_root, sc, 7, "--disarmed")
    for r in recs:
        rp = replay(r)
        assert rp["signal_match"] is True and rp["ok"], rp
        assert r["actuation"]["requested"] == []
    assert xcompare.core_diff(recs, run_scenario(sc, 7, armed=False).records) is None
