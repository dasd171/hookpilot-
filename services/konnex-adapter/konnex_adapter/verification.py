"""Local evidence scoring against explicit criteria (HookPilot's own validator logic).

This is NOT a Konnex validator and its score is NOT a Konnex/PoPW score. It is
the check a HookPilot reviewer (or a future subnet validator) would run first.

Criteria (all must pass for verdict "accept"):
  C1 schema_valid                  every record validates against the event schema
  C2 hash_chain_intact             record hashes recompute; prev links; manifest chain head
  C3 bundle_files_intact           manifest_sha256 recomputes; every file sha256 matches
  C4 raw_data_integrity            raw_data_sha256 of every referenced raw file matches
  C5 required_fields_present       task.required_evidence_fields present & non-null
  C6 safety_constraints_respected  actuation only when armed+permitted; never simultaneous;
                                   travel/tension/force/stall limits; faults -> SAFE_STOP
  C7 classification_replay_matches deterministic-baseline-v1 re-run reproduces result
                                   (and signal regenerates from scenario+seed for SIMULATION)
  C8 fish_landed_not_inferred      fish_landed == "unknown" unless an observer attestation exists
  C9 trigger_params_within_allowed thresholds inside task.allowed_trigger_params
  C10 device_identity_matches_task device_id / firmware_version equal the task's
  C11 simulation_labeled           simulation records say so (source.mode, actuation.simulated, manifest)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile

from evidence_service.chain import verify_records
from evidence_service.replay import replay

from .bundle import manifest_hash, read_bundle
from .task import DEFAULT_REQUIRED_EVIDENCE_FIELDS, DEFAULT_SAFETY_CONSTRAINTS, DEFAULT_ALLOWED_TRIGGER_PARAMS

TRUST_NOTE = ("Local mock scoring. A passing score shows the records are internally consistent, untampered "
              "since sealing, reproducible and within declared safety limits. It does NOT prove the physical "
              "event happened: there is no hardware signature, the sensor and calibration are self-reported, "
              "and no independent observation is included.")

_MISSING = object()


def _get(d, dotted):
    for p in dotted.split("."):
        if not isinstance(d, dict) or p not in d:
            return _MISSING
        d = d[p]
    return d


def _crit(cid, name, status, details):
    return {"id": cid, "name": name, "status": status, "details": details}


_DONE = ("at_target", "complete", "stopped", "stalled", "comm_timeout_detected_by_safety", "aborted", "safe_stop")


def _active_intervals(req: list, fb: list) -> list[tuple[str, float, float]]:
    """Active interval per request: from its t_ms to the first terminal feedback status of that actuator
    (or that actuator's last feedback sample before its next request)."""
    out = []
    reqs = sorted((r for r in req if r.get("actuator") in ("hook_set", "retrieval")), key=lambda r: r.get("t_ms", 0))
    for i, r in enumerate(reqs):
        a, t0 = r["actuator"], float(r.get("t_ms", 0))
        nxt = next((float(q["t_ms"]) for q in reqs[i + 1:] if q["actuator"] == a), float("inf"))
        mine = [f for f in fb if f.get("actuator") == a and t0 <= float(f.get("t_ms", -1)) < nxt]
        end = t0
        for f in sorted(mine, key=lambda f: f["t_ms"]):
            end = float(f["t_ms"])
            if any(str(f.get("status", "")).startswith(d) for d in _DONE):
                break
        out.append((a, t0, end))
    return out


def check_safety(record: dict, sc: dict) -> list[str]:
    """Return list of violations (empty == respected)."""
    D = DEFAULT_SAFETY_CONSTRAINTS
    v = []
    saf = record.get("safety") or {}
    act = record.get("actuation") or {}
    req = act.get("requested") or []
    fb = act.get("feedback") or []
    faults = saf.get("faults") or []
    final = (record.get("outcome") or {}).get("final_state")
    hard = float(sc.get("hard_max_tension_n", D["hard_max_tension_n"]))
    rlim = float(sc.get("retrieval_force_limit_n", D["retrieval_force_limit_n"]))
    n_ot = int(sc.get("over_tension_samples", D["over_tension_samples"]))
    trav = float(sc.get("hook_set_travel_limit_mm", D["hook_set_travel_limit_mm"]))
    stall_a = float(sc.get("stall_current_a", D["stall_current_a"]))
    stall_ms = float(sc.get("stall_window_ms", D["stall_window_ms"]))
    prog = float(sc.get("stall_min_progress_m", D["stall_min_progress_m"]))
    pause = sc.get("assist_pause_n")
    if req and sc.get("require_armed_for_actuation", True) and saf.get("armed") is not True:
        v.append("actuation requested while not armed")
    if fb and saf.get("decision") == "deny":
        v.append("actuator feedback present although safety decision is deny")
    if faults and sc.get("safe_stop_on_fault", True) and (final != "SAFE_STOP" or saf.get("decision") != "safe_stop"):
        v.append(f"faults {faults} latched but final_state={final} decision={saf.get('decision')}")
    hook_fb = [f for f in fb if f.get("actuator") == "hook_set"]
    ret_fb = [f for f in fb if f.get("actuator") == "retrieval"]
    if sc.get("no_simultaneous_actuation", True):
        ivs = _active_intervals(req, fb)
        hooks = [iv for iv in ivs if iv[0] == "hook_set"]
        rets = [iv for iv in ivs if iv[0] == "retrieval"]
        for _, h0, h1 in hooks:
            for _, r0, r1 in rets:
                if max(h0, r0) < min(h1, r1):  # positive-length overlap; a same-tick hand-over is allowed
                    v.append(f"hook_set active [{h0},{h1}] ms overlaps retrieval active [{r0},{r1}] ms")
    for f in hook_fb:
        if f.get("position") is not None and f["position"] > trav + 1e-9:
            v.append(f"hook travel {f['position']} mm > limit {trav} mm at t={f['t_ms']}")

    def run_check(group, limit, label):
        run = 0
        for i, f in enumerate(group):
            t = f.get("tension_n")
            run = run + 1 if (t is not None and t > limit) else 0
            if run >= n_ot and not (i == len(group) - 1 and "OVER_TENSION" in faults):
                v.append(f"tension > {label} {limit} N for {run} samples at t={f['t_ms']} without OVER_TENSION stop")
                return

    run_check(hook_fb, hard, "hard max")
    run_check(ret_fb, rlim, "retrieval force limit")
    if pause is not None:
        prev = None
        for f in ret_fb:
            if prev is not None and f.get("position") is not None and prev.get("position") is not None:
                if f["position"] > prev["position"] + 1e-12 and (f.get("tension_n") or 0) > float(pause):
                    v.append(f"retrieval wound line at {f['tension_n']:.2f} N > assist_pause {pause} N (t={f['t_ms']}): drag bypassed")
                    break
            prev = f
    hist = []
    for f in ret_fb:
        c, p = f.get("current_a"), f.get("position")
        if c is None or p is None:
            continue
        hist.append((f["t_ms"], p, c))
        win = [h for h in hist if h[0] >= f["t_ms"] - stall_ms - 40.0]
        if len(win) >= 2 and all(h[2] >= stall_a for h in win) and win[-1][0] - win[0][0] >= stall_ms + 40.0:
            if win[-1][1] - win[0][1] < prog and "MOTOR_STALL" not in faults:
                v.append(f"stall (>= {stall_a} A, no progress) for > {stall_ms} ms without MOTOR_STALL (t={f['t_ms']})")
                break
    return v


def verify_evidence(records: list[dict], *, task: dict | None = None, raw_dir: str | None = None,
                    manifest: dict | None = None, files: dict | None = None) -> dict:
    task = task or {}
    req_fields = task.get("required_evidence_fields") or DEFAULT_REQUIRED_EVIDENCE_FIELDS
    sc = task.get("safety_constraints") or DEFAULT_SAFETY_CONSTRAINTS
    allowed = task.get("allowed_trigger_params") or DEFAULT_ALLOWED_TRIGGER_PARAMS
    crits = []
    checks = verify_records(records, raw_base_dir=raw_dir, genesis_prev=(manifest or {}).get("chain_genesis_prev"))

    bad = [f"#{c.index} {c.event_id}: {e}" for c in checks for e in c.schema_errors]
    crits.append(_crit("C1", "schema_valid", "pass" if not bad and records else "fail", bad or ([] if records else ["no records"])))

    d = [f"#{c.index} {c.event_id}: record hash mismatch" for c in checks if not c.hash_ok]
    d += [f"#{c.index} {c.event_id}: prev link broken" for c in checks if not c.chain_ok]
    if manifest is not None and records and manifest.get("chain_head") != records[-1]["integrity"].get("record_sha256"):
        d.append("manifest chain_head != last record hash")
    crits.append(_crit("C2", "hash_chain_intact", "pass" if not d and records else "fail", d))

    if manifest is None:
        crits.append(_crit("C3", "bundle_files_intact", "not_applicable", ["no bundle manifest"]))
    else:
        d = []
        if manifest_hash(manifest) != manifest.get("manifest_sha256"):
            d.append("manifest_sha256 does not recompute")
        listed = {f["path"]: f for f in manifest.get("files", [])}
        for p, meta in listed.items():
            b = (files or {}).get(p)
            if b is None:
                d.append(f"missing file {p}")
            elif hashlib.sha256(b).hexdigest() != meta["sha256"]:
                d.append(f"sha256 mismatch {p}")
        extra = set((files or {}).keys()) - set(listed) - {"manifest.json"}
        if extra:
            d.append(f"unlisted files in bundle: {sorted(extra)}")
        crits.append(_crit("C3", "bundle_files_intact", "fail" if d else "pass", d))

    refs = [c for c in checks if c.raw_ok is not None]
    if not refs and not any((r.get("sensor_data") or {}).get("raw_data_ref") for r in records):
        crits.append(_crit("C4", "raw_data_integrity", "not_applicable", ["no raw_data_ref in records (samples inline)"]))
    else:
        d = [f"#{c.index} {c.event_id}: {c.raw_detail}" for c in checks if c.raw_ok is not True]
        crits.append(_crit("C4", "raw_data_integrity", "fail" if d else "pass", d))

    d = []
    for r in records:
        for f in req_fields:
            val = _get(r, f)
            if val is _MISSING or val is None:
                d.append(f"{r.get('event_id')}: missing {f}")
    crits.append(_crit("C5", "required_fields_present", "fail" if d else "pass", d))

    d = []
    for r in records:
        d += [f"{r.get('event_id')}: {x}" for x in check_safety(r, sc)]
    crits.append(_crit("C6", "safety_constraints_respected", "fail" if d else "pass", d))

    d, replays = [], []
    for r in records:
        rp = replay(r, raw_base_dir=raw_dir)
        replays.append(rp)
        if not rp["ok"]:
            d.append(f"{r.get('event_id')}: signal_match={rp['signal_match']} classification_match={rp['classification_match']} "
                     f"recorded={rp['recorded_result']} replayed={rp.get('replayed_result')} {rp['notes']}")
    crits.append(_crit("C7", "classification_replay_matches", "fail" if d else "pass", d))

    d = []
    for r in records:
        fl = _get(r, "outcome.milestones.fish_landed")
        if fl == "confirmed_by_observer":
            if _get(r, "outcome.observer_attestation") in (_MISSING, None):
                d.append(f"{r.get('event_id')}: fish_landed=confirmed_by_observer without outcome.observer_attestation")
        elif fl not in ("unknown", "not_landed"):
            d.append(f"{r.get('event_id')}: fish_landed={fl!r}")
        elif fl == "not_landed" and _get(r, "outcome.observer_attestation") in (_MISSING, None):
            d.append(f"{r.get('event_id')}: fish_landed=not_landed asserted without observer attestation")
    crits.append(_crit("C8", "fish_landed_not_inferred", "fail" if d else "pass", d))

    d = []
    for r in records:
        th = _get(r, "classification.thresholds")
        if not isinstance(th, dict):
            d.append(f"{r.get('event_id')}: no thresholds recorded")
            continue
        for k, (lo, hi) in allowed.items():
            if k not in th:
                d.append(f"{r.get('event_id')}: threshold {k} not recorded")
            elif not (lo <= th[k] <= hi):
                d.append(f"{r.get('event_id')}: {k}={th[k]} outside allowed [{lo}, {hi}]")
    crits.append(_crit("C9", "trigger_params_within_allowed", "fail" if d else "pass", d))

    dev = task.get("device")
    if not dev:
        crits.append(_crit("C10", "device_identity_matches_task", "not_applicable", ["no task device identity"]))
    else:
        d = []
        for r in records:
            rd = r.get("device") or {}
            for k in ("device_id", "firmware_version"):
                if rd.get(k) != dev.get(k):
                    d.append(f"{r.get('event_id')}: {k}={rd.get(k)!r} != task {dev.get(k)!r}")
        crits.append(_crit("C10", "device_identity_matches_task", "fail" if d else "pass", d))

    d = []
    for r in records:
        if _get(r, "source.mode") == "simulation" and _get(r, "actuation.simulated") is not True:
            d.append(f"{r.get('event_id')}: simulation record without actuation.simulated=true")
        if _get(r, "source.mode") != "simulation" and _get(r, "actuation.simulated") is True:
            d.append(f"{r.get('event_id')}: non-simulation record claims simulated actuation")
    if manifest is not None and all(_get(r, "source.mode") == "simulation" for r in records) and manifest.get("label") != "SIMULATION":
        d.append("manifest not labelled SIMULATION")
    crits.append(_crit("C11", "simulation_labeled", "fail" if d else "pass", d))

    applicable = [c for c in crits if c["status"] != "not_applicable"]
    passed = [c for c in applicable if c["status"] == "pass"]
    return {
        "scorer": "hookpilot-local-verifier-v0 (NOT a Konnex validator)",
        "label": "SIMULATION" if records and all(_get(r, "source.mode") == "simulation" for r in records) else "MIXED_OR_REAL",
        "record_count": len(records),
        "criteria": crits,
        "score": round(len(passed) / len(applicable), 4) if applicable else 0.0,
        "passed": len(passed), "applicable": len(applicable),
        "verdict": "accept" if applicable and len(passed) == len(applicable) else "reject",
        "signature": "absent (no secure element) – hardware attestation not evaluated",
        "trust_note": TRUST_NOTE,
        "replays": replays,
    }


def verify_bundle(zip_path: str, task: dict | None = None) -> dict:
    b = read_bundle(zip_path)
    tmp = tempfile.mkdtemp(prefix="hp-bundle-")
    try:
        for name, data in b["files"].items():
            if name.startswith("raw/"):
                p = os.path.join(tmp, name)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
        res = verify_evidence(b["records"], task=task or b["task"], raw_dir=tmp, manifest=b["manifest"], files=b["files"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(zip_path, "rb") as f:
        res["bundle_sha256"] = hashlib.sha256(f.read()).hexdigest()
    return res
