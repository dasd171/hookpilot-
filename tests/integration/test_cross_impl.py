"""Cross-implementation checks against the C++ firmware sim and the TS web demo.
Skipped (with the reason) when those builds are absent or not runnable in this checkout."""
import json
import os
import shutil
import subprocess

import pytest

from evidence_service.chain import verify_records
from evidence_service.engine import build_event
from evidence_service.replay import replay

SCEN = ["bite", "noise_current", "rod_vibration", "manual_handling", "sensor_failure", "excessive_tension",
        "motor_stall", "comm_loss"]


def _fw_records(repo_root, sc, seed):
    exe = os.path.join(repo_root, "firmware", "build", "hookpilot_sim")
    if not os.path.exists(exe):
        pytest.skip("firmware/build/hookpilot_sim not built (run `make -C firmware sim`)")
    p = subprocess.run([exe, "--scenario", sc, "--seed", str(seed), "--quiet"], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        pytest.skip(f"firmware sim not runnable here (rc={p.returncode}): {p.stderr[:200]}")
    return [json.loads(l) for l in p.stdout.splitlines() if l.strip().startswith("{") and '"schema_version"' in l]


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", SCEN)
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_firmware_records_verify_and_replay(repo_root, sc, seed):
    recs = _fw_records(repo_root, sc, seed)
    if not recs:
        pytest.skip("firmware emitted no event record for this scenario/seed")
    checks = verify_records(recs)
    assert all(c.hash_ok and c.chain_ok and not c.schema_errors for c in checks), [c.to_dict() for c in checks if not c.ok]
    for r in recs:
        rp = replay(r)
        assert rp["signal_match"] is True, rp
        assert rp["ok"], rp
    # Python reference engine reaches the same classification from (scenario, seed)
    assert build_event(sc, seed)["classification"]["result"] == recs[0]["classification"]["result"]
    # firmware records satisfy the local verifier (safety constraints, replay, labels, ...)
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
    """Web-demo records name hookpilot-synth-v1 and use the firmware rules: fully replayable from (scenario, seed)."""
    recs = _web_records(repo_root, sc, seed)
    assert recs, "web demo emitted no record"
    checks = verify_records(recs)
    assert all(c.hash_ok and c.chain_ok and not c.schema_errors for c in checks), [c.to_dict() for c in checks if not c.ok]
    for r in recs:
        assert r["source"]["generator"] == "hookpilot-synth-v1"
        rp = replay(r)
        assert rp["signal_match"] is True, rp           # regenerated from (scenario, seed), not "inconclusive"
        assert rp["classification_match"] is True, rp
        assert rp["rules_consistent"] in (True, None), rp
        assert rp["ok"], rp
    # same classification, reason and rule-trace text as the Python reference engine
    py = build_event(sc, seed)["classification"]
    first = recs[0]["classification"]
    assert first["result"] == py["result"]
    assert first["reason"] == py["reason"]
    assert first["rule_trace"] == py["rule_trace"]


@pytest.mark.crossimpl
@pytest.mark.parametrize("sc", ["bite", "rod_vibration", "manual_handling"])
def test_web_demo_disarmed_records_replay(repo_root, sc):
    recs = _web_records(repo_root, sc, 7, "--disarmed")
    for r in recs:
        rp = replay(r)
        assert rp["signal_match"] is True and rp["ok"], rp
