"""End-to-end (SIMULATION): Python engine -> hash-chained log -> bundle -> mock submit -> verify.
Also exercises the three CLIs as subprocesses, the way a reviewer would run them."""
import json
import os
import subprocess
import sys
import zipfile

from evidence_service.engine import EventLog, build_event
from evidence_service.synth import SCENARIOS
from konnex_adapter import DeviceIdentity, MockKonnexAdapter, TaskSpec, build_bundle, verify_bundle


def test_engine_to_bundle_to_mock_verify(tmp_path):
    ad = MockKonnexAdapter(str(tmp_path / "store"), deterministic=True)
    spec = TaskSpec(task_id="e2e-bite", device=DeviceIdentity("hp-sim-001", "sim-py-0.1.0"), test_scenario="bite", seed=1)
    jid = ad.submit_task(spec)
    ad.attach_robot(jid, "hp-sim-001")
    outs = [ad.execute(jid, seed=s) for s in range(1, 6)]
    assert all(o["final_status"]["milestones"]["fish_landed"] == "unknown" for o in outs)
    bundle = ad.record(jid)
    receipt = ad.prove(jid, bundle["path"])
    res = ad.verify(jid)
    assert res["verdict"] == "accept" and res["score"] == 1.0
    assert receipt["bundle_sha256"] == res["bundle_sha256"]
    job = ad.get_job(jid)
    assert [h["event"] for h in job["history"]] == ["submitted", "attached"] + ["executed"] * 5 + ["recorded", "prove_mock", "verify_mock"]


def test_all_scenarios_one_bundle(tmp_path):
    log = EventLog()
    raw = str(tmp_path / "raw")
    n = 0
    for sc in SCENARIOS:
        for seed in (1, 2):
            n += 1
            log.append(build_event(sc, seed, counter=n, raw_dir=raw))
    b = build_bundle(log.records, str(tmp_path / "all.zip"), raw_dir=raw)
    res = verify_bundle(b["path"])
    assert res["verdict"] == "accept", [c for c in res["criteria"] if c["status"] == "fail"]
    # tamper with one raw file inside the zip -> rejected
    src = zipfile.ZipFile(b["path"])
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as z:
        for name in src.namelist():
            data = src.read(name)
            if name.startswith("raw/") and "000003" in name:
                data = data.replace(b"\n2", b"\n3", 1)
            z.writestr(name, data)
    bad = verify_bundle(str(tmp_path / "bad.zip"))
    names = {c["name"]: c["status"] for c in bad["criteria"]}
    assert bad["verdict"] == "reject" and names["bundle_files_intact"] == "fail" and names["raw_data_integrity"] == "fail"


def _run(args, env, cwd, stdin=None):
    return subprocess.run([sys.executable] + args, env=env, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=120)


def test_clis(tmp_path, py_env, repo_root):
    ev = str(tmp_path / "events.jsonl")
    r = _run(["-m", "evidence_service", "generate", "--scenario", "bite,motor_stall,rod_vibration", "--seed", "1",
              "--count", "3", "--raw", "--out", ev], py_env, repo_root)
    assert r.returncode == 0, r.stderr
    r = _run(["-m", "evidence_service", "verify", ev, "--replay"], py_env, repo_root)
    assert r.returncode == 0 and "PASS: 9/9 records verified, 9/9 replays matched" in r.stdout, r.stdout + r.stderr
    one = str(tmp_path / "one.json")
    with open(ev) as f, open(one, "w") as g:
        g.write(f.readline())
    r = _run(["-m", "evidence_service", "replay", one], py_env, repo_root)
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True
    r = _run(["-m", "evidence_service", "export", ev, "--out", str(tmp_path / "e.csv")], py_env, repo_root)
    assert r.returncode == 0 and os.path.exists(tmp_path / "e.csv")
    # tamper -> verify exits 1
    lines = open(ev).read().splitlines()
    rec = json.loads(lines[4])
    rec["outcome"]["stop_reason"] = "cycle_complete"
    lines[4] = json.dumps(rec)
    open(tmp_path / "bad.jsonl", "w").write("\n".join(lines) + "\n")
    r = _run(["-m", "evidence_service", "verify", str(tmp_path / "bad.jsonl")], py_env, repo_root)
    assert r.returncode == 1 and "hash:FAIL" in r.stdout
    # edge bridge consumes the generated stream and drives the mock adapter
    r = _run(["-m", "edge_controller", "--store", str(tmp_path / "edge" / "ev.jsonl"), "--raw-dir", str(tmp_path),
              "--mock-konnex", str(tmp_path / "mk"), "--device-id", "hp-sim-001", "--firmware", "sim-py-0.1.0",
              "--bundle", str(tmp_path / "edge.zip")], py_env, repo_root, stdin="hello fw\n" + open(ev).read())
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["accepted"] == 9 and out["text_lines"] == 1 and out["verification"]["verdict"] == "accept"
    # konnex adapter CLI
    r = _run(["-m", "konnex_adapter", "demo", "--scenario", "comm_loss", "--seed", "2", "--runs", "2",
              "--store", str(tmp_path / "mk2")], py_env, repo_root)
    assert r.returncode == 0 and json.loads(r.stdout)["verification"]["verdict"] == "accept"
    r = _run(["-m", "konnex_adapter", "verify-bundle", str(tmp_path / "edge.zip")], py_env, repo_root)
    assert r.returncode == 0 and "verdict=accept" in r.stdout
    r = _run(["-m", "konnex_adapter", "cli-argv"], py_env, repo_root)
    assert json.loads(r.stdout)["enabled"] is False
