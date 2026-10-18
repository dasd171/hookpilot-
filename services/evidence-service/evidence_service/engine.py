"""Python reference engine: (scenario, seed) -> complete HookPilot event record. SIMULATION.

Pipeline: synth signal -> deterministic-baseline-v1 -> safety gate ->
simulated hook-set / retrieval telemetry -> outcome -> (sealed by EventLog).

The actuator models are kinematic placeholders (no physics), labelled
actuation.simulated = true. fish_landed is never inferred: always "unknown".
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from .canonical import js_number, seal
from .classifier import CLASSIFIER_ID, StreamingClassifier, Thresholds
from .safety import ActuatorParams, SafetyController, SafetyLimits
from .synth import (EXPECTED_CLASSIFICATION, EXPECTED_FAULT, GENERATOR_ID, GROUND_TRUTH,
                    SynthSignal, generate)

SCHEMA_VERSION = "1.0.0"
DEFAULT_DEVICE = {"device_id": "hp-sim-001", "firmware_version": "sim-py-0.1.0",
                  "hardware_revision": "none-simulation"}
SIM_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def write_raw_csv(path: str, samples: list[list[float]]) -> None:
    """Raw data file format v1: header 't_ms,tension_n', LF line endings, JS number formatting."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("t_ms,tension_n\n")
        for t, x in samples:
            f.write(f"{js_number(t)},{js_number(x)}\n")


def read_raw_csv(path: str) -> list[list[float]]:
    out = []
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip()
        if header != "t_ms,tension_n":
            raise ValueError(f"unexpected raw header {header!r}")
        for line in f:
            if line.strip():
                a, b = line.strip().split(",")
                out.append([float(a), float(b)])
    return out


def _sample_at(sig: SynthSignal, t: float) -> tuple[int, float]:
    dt = 1000.0 / sig.sample_rate_hz
    i = min(len(sig.tension_n) - 1, max(0, int(round(t / dt))))
    return i, sig.tension_n[i]


def build_event(scenario: str, seed: int, *, armed: bool = True, counter: int = 1,
                device: dict | None = None, thresholds: Thresholds | None = None,
                limits: SafetyLimits | None = None, actuators: ActuatorParams | None = None, start_utc: datetime | None = None,
                raw_dir: str | None = None, raw_ref_prefix: str = "raw/",
                inline_samples: bool = True) -> dict:
    th = thresholds or Thresholds.defaults("fw")
    L = limits or SafetyLimits()
    dev = dict(device or DEFAULT_DEVICE)
    sig = generate(scenario, seed)
    samples = sig.samples()
    dt = 1000.0 / sig.sample_rate_hz

    # ---- classification (streaming, as firmware would) -------------------
    clf = StreamingClassifier(th, sig.sample_rate_hz)
    for t, x in samples:
        if clf.push(t, x) is not None:
            break
    dec = clf.finish(samples[-1][0])

    # ---- safety + actuation ----------------------------------------------
    A = actuators or ActuatorParams()
    sc = SafetyController(L, armed=armed)
    requested, feedback = [], []
    hook_done = retr_done = False
    stop_reason = ""
    sensor_fault = dec.result == "rejected_sensor_fault"
    ot_run = 0

    def over_tension(x: float, limit: float) -> bool:
        nonlocal ot_run
        ot_run = ot_run + 1 if x > limit else 0
        return ot_run >= L.over_tension_samples

    if sensor_fault:
        h = dec.features.get("sensor_health")
        sc.latch({"stuck": "SENSOR_STUCK", "disconnected": "SENSOR_DISCONNECTED"}.get(h, "SENSOR_OUT_OF_RANGE"),
                 dec.decision_t_ms or 0.0)
    elif dec.result == "bite_candidate_confirmed":
        tc = dec.decision_t_ms
        _, x_c = _sample_at(sig, tc)
        requested.append({"actuator": "hook_set", "command": "move_to",
                          "limit": {"target_mm": A.hook_set_target_mm, "speed_mm_s": A.hook_set_speed_mm_s,
                                    "travel_limit_mm": L.hook_set_travel_limit_mm,
                                    "timeout_ms": L.hook_set_timeout_ms}, "t_ms": tc})
        ok, _ = sc.request("hook_set", tc, x_c, "ok")
        if ok:
            # hook-set: kinematic ramp to the target (mechanical stop just beyond travel limit)
            dur = 1000.0 * A.hook_set_target_mm / A.hook_set_speed_mm_s
            steps = int(round(dur / dt))
            ot_run = 0
            for k in range(steps + 1):
                t = tc + k * dt
                _, x = _sample_at(sig, t)
                pos = min(A.hook_set_target_mm, A.hook_set_speed_mm_s * k * dt / 1000.0)
                feedback.append({"actuator": "hook_set", "t_ms": t, "position": round(pos, 6),
                                 "current_a": 0.9 if k < steps else 0.1, "tension_n": x,
                                 "status": "complete" if k == steps else "moving"})
                if over_tension(x, L.hard_max_tension_n):
                    sc.latch("OVER_TENSION", t)
                    break
            sc.release("hook_set")
            if not sc.faults:
                hook_done = True
                tr = tc + steps * dt + L.actuator_gap_ms
                _, x_r = _sample_at(sig, tr)
                requested.append({"actuator": "retrieval", "command": "start",
                                  "limit": {"target_m": A.retrieval_target_m, "speed_m_s": A.retrieval_speed_m_s,
                                            "retrieval_force_limit_n": L.retrieval_force_limit_n,
                                            "assist_pause_n": L.assist_pause_n,
                                            "timeout_ms": L.retrieval_timeout_ms}, "t_ms": tr})
                ok2, why = sc.request("retrieval", tr, x_r, "ok")
                if not ok2 and x_r >= L.retrieval_force_limit_n:
                    sc.latch("OVER_TENSION", tr)
                if ok2:
                    fault_t = None
                    if scenario in ("motor_stall", "comm_loss"):
                        fault_t = tr + dt * round(sig.params["fault_after_retrieval_start_ms"] / dt)
                    pos = 0.0
                    hist: list[tuple[float, float]] = []
                    t = tr
                    ot_run = 0
                    t_limit = min(tr + L.retrieval_timeout_ms, samples[-1][0])
                    while t <= t_limit + 1e-9:
                        _, x = _sample_at(sig, t)
                        if scenario == "comm_loss" and fault_t is not None and t >= fault_t:
                            if t - fault_t >= L.comm_timeout_ms:
                                feedback.append({"actuator": "retrieval", "t_ms": t, "position": None,
                                                 "current_a": None, "tension_n": None,
                                                 "status": "comm_timeout_detected_by_safety"})
                                sc.latch("COMM_LOSS", t)
                                break
                            t += dt
                            continue  # no heartbeat / telemetry during the outage
                        stalled = scenario == "motor_stall" and fault_t is not None and t >= fault_t
                        paused = x > L.assist_pause_n
                        if stalled:
                            cur = 2.6
                        else:
                            cur = 0.3 + 0.12 * x
                            if not paused:
                                pos = min(A.retrieval_target_m, pos + A.retrieval_speed_m_s * dt / 1000.0)
                        status = "stalled" if stalled else ("assist_paused_drag_governs" if paused else "winding")
                        feedback.append({"actuator": "retrieval", "t_ms": t, "position": round(pos, 6),
                                         "current_a": round(cur, 6), "tension_n": x, "status": status})
                        hist.append((t, pos))
                        if over_tension(x, L.retrieval_force_limit_n):
                            sc.latch("OVER_TENSION", t)
                            break
                        if t - tr >= L.stall_grace_ms and cur >= L.stall_current_a:
                            old = [p for (tt, p) in hist if tt <= t - L.stall_window_ms]
                            if old and pos - old[-1] < L.stall_min_progress_m:
                                sc.latch("MOTOR_STALL", t)
                                break
                        if pos >= A.retrieval_target_m - 1e-12:
                            retr_done = True
                            feedback[-1]["status"] = "complete"
                            break
                        t += dt
                    else:
                        if not sc.faults and t - tr >= L.retrieval_timeout_ms:
                            sc.latch("ACTUATOR_TIMEOUT", t)
                    sc.release("retrieval")

    # ---- outcome ------------------------------------------------------------
    if sc.faults:
        decision, final_state = "safe_stop", "SAFE_STOP"
        stop_reason = "fault:" + ",".join(sc.faults)
    elif requested and hook_done:
        decision = "permit"
        final_state = "MONITOR"
        stop_reason = "cycle_complete" if retr_done else "retrieval_incomplete_window_end"
    else:
        decision = "deny"
        if dec.result == "bite_candidate_confirmed" and not armed:
            final_state, stop_reason = "IDLE", "disarmed_observation_only"
        elif dec.result == "no_event":
            final_state, stop_reason = ("MONITOR" if armed else "IDLE"), "no_event"
        else:
            final_state, stop_reason = ("MONITOR" if armed else "IDLE"), f"rejected:{dec.result}"

    # test_outcome (SIMULATION ground truth): positives must be confirmed (and, when armed, hit the
    # expected safety fault); negatives pass when no actuation was triggered and no unexpected fault
    # latched. Exact class agreement is reported separately (class_match) – see tests/reports.
    from .synth import POSITIVE_SCENARIOS
    if scenario in POSITIVE_SCENARIOS:
        exp_ok = dec.result == "bite_candidate_confirmed"
    else:
        exp_ok = dec.result != "bite_candidate_confirmed"
    if armed and scenario in EXPECTED_FAULT:
        exp_ok = exp_ok and any(f in sc.faults for f in EXPECTED_FAULT[scenario])
    if armed and scenario not in EXPECTED_FAULT and sc.faults:
        exp_ok = False
    class_match = dec.result in EXPECTED_CLASSIFICATION[scenario]

    h8 = hashlib.sha256(f"{dev['device_id']}:{scenario}:{seed}:{counter}".encode()).hexdigest()[:8]
    event_id = f"{dev['device_id']}-{counter:06d}-{h8}"
    start = start_utc or (SIM_EPOCH + timedelta(seconds=10 * counter))

    sensor_data = {"sample_rate_hz": sig.sample_rate_hz, "units": "N",
                   "raw_data_ref": None, "raw_data_sha256": None}
    if inline_samples:
        sensor_data["samples"] = samples
    if raw_dir is not None:
        from .canonical import file_sha256
        fname = f"{event_id}.csv"
        path = os.path.join(raw_dir, fname)
        write_raw_csv(path, samples)
        sensor_data["raw_data_ref"] = raw_ref_prefix + fname
        sensor_data["raw_data_sha256"] = file_sha256(path)

    record = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "device": dev,
        "source": {"mode": "simulation", "scenario": scenario, "seed": seed,
                   "ground_truth_label": GROUND_TRUTH[scenario], "generator": GENERATOR_ID,
                   "label": "SIMULATION"},
        "timestamp": {"start_utc": iso(start), "end_utc": iso(start + timedelta(milliseconds=sig.duration_ms)),
                      "monotonic_ms": 10_000 * counter, "clock_quality": "host_clock_simulated"},
        "calibration": {"calibration_version": "sim-identity-v0", "scale_n_per_count": 1.0,
                        "offset_counts": 0.0, "calibrated_utc": None},
        "sensor_data": sensor_data,
        "features": dict(dec.features),
        "classification": dec.classification_block(th),
        "safety": {"decision": decision, "armed": armed,
                   "interlocks": {"armed": armed, "e_stop_released": True,
                                  "sensor_health_ok": not sensor_fault,
                                  "battery_v": sc.battery_v, "battery_ok": sc.battery_v >= L.undervoltage_v,
                                  "battery_v_simulated": True,
                                  "no_simultaneous_actuation": True,
                                  "limits": L.to_record(), "log": sc.log},
                   "faults": list(sc.faults)},
        "actuation": {"requested": requested, "feedback": feedback, "simulated": True},
        "outcome": {"final_state": final_state, "stop_reason": stop_reason,
                    "milestones": {"bite_detected": dec.result == "bite_candidate_confirmed",
                                   "hook_set_completed": hook_done,
                                   "retrieval_completed": retr_done,
                                   "fish_landed": "unknown"},
                    "test_outcome": "pass" if exp_ok else "fail",
                    "class_match": class_match},
        "integrity": {"record_sha256": "", "hash_algorithm": "sha256",
                      "prev_record_sha256": None, "signature": None},
    }
    if dec.decision_t_ms is not None:
        record["classification"]["decision_t_ms"] = dec.decision_t_ms
        record["classification"]["trigger_t_ms"] = dec.trigger_t_ms
    return record


class EventLog:
    """Append-only hash-chained log (in memory, optional JSONL file)."""

    def __init__(self, path: str | None = None, prev: str | None = None) -> None:
        self.path = path
        self.records: list[dict] = []
        self.prev = prev
        if path and os.path.exists(path):
            from .chain import load_jsonl
            self.records = load_jsonl(path)
            if self.records:
                self.prev = self.records[-1]["integrity"]["record_sha256"]

    def append(self, record: dict) -> dict:
        seal(record, self.prev)
        self.prev = record["integrity"]["record_sha256"]
        self.records.append(record)
        if self.path:
            import json
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
