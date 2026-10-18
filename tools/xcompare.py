#!/usr/bin/env python3
"""Shared cross-implementation comparison helpers (SIMULATION).

Used by tests/integration/test_cross_impl.py (firmware vs Python) and tools/gen_web_fixtures.py (the
Python side of the TS comparison). See docs/engine-spec.md "Cross-implementation comparison".

  trace_diff(a, b)      first differing tick-trace field (numbers within TOL), or None
  record_core(rec)      the implementation-independent part of an event record
  core_diff(a, b)       first differing record-core field, or None
"""
from __future__ import annotations

import math

TOL = 1e-9
TRACE_KEYS = ("k", "phase", "t", "state", "armed", "raw", "valid", "health", "x", "baseline", "delta", "load_n",
              "hook", "retr", "hb_ms", "latched", "active", "cmds", "trans", "injected", "records")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def value_diff(a, b, path: str = "") -> str | None:
    if _num(a) and _num(b):
        if math.isnan(a) and math.isnan(b):
            return None
        return None if abs(a - b) <= TOL else f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return f"{path}: keys {sorted(set(a) ^ set(b))} differ"
        for k in sorted(a):
            d = value_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = value_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def normalize_trace_line(line: dict) -> dict:
    out = {k: line.get(k) for k in TRACE_KEYS}
    if isinstance(out["records"], list):  # firmware lists record hashes; the ports count records
        out["records"] = len(out["records"])
    return out


def trace_diff(a: list[dict], b: list[dict]) -> str | None:
    if len(a) != len(b):
        return f"trace length {len(a)} != {len(b)}"
    for x, y in zip(a, b):
        d = value_diff(normalize_trace_line(x), normalize_trace_line(y), f"tick[{x.get('k')}] t={x.get('t')}")
        if d:
            return d
    return None


def max_abs_diff(a, b) -> float:
    if _num(a) and _num(b):
        return 0.0 if (math.isnan(a) and math.isnan(b)) else abs(a - b)
    if isinstance(a, dict) and isinstance(b, dict):
        return max([max_abs_diff(a[k], b[k]) for k in a if k in b] or [0.0])
    if isinstance(a, list) and isinstance(b, list):
        return max([max_abs_diff(x, y) for x, y in zip(a, b)] or [0.0])
    return 0.0


def record_core(r: dict) -> dict:
    cl = r.get("classification") or {}
    sf = r.get("safety") or {}
    sd = r.get("sensor_data") or {}
    oc = r.get("outcome") or {}
    act = r.get("actuation") or {}
    feats = {k: (r.get("features") or {}).get(k) for k in
             ("baseline_n", "peak_delta_n", "max_rate_n_per_s", "duration_above_ms", "continuous_above_ms",
              "short_term_variance", "oscillation_count", "sensor_health")}
    return {
        "scenario": (r.get("source") or {}).get("scenario"), "seed": (r.get("source") or {}).get("seed"),
        "start_ms": (r.get("timestamp") or {}).get("monotonic_ms"),
        "start_utc": (r.get("timestamp") or {}).get("start_utc"), "end_utc": (r.get("timestamp") or {}).get("end_utc"),
        "samples": sd.get("samples"), "invalid_samples_omitted": sd.get("invalid_samples_omitted", 0),
        "features": feats,
        "result": cl.get("result"), "reason": cl.get("reason"), "rule_trace": cl.get("rule_trace"),
        "trigger_t_ms": cl.get("trigger_t_ms"), "decision_t_ms": cl.get("decision_t_ms"),
        "decision": sf.get("decision"), "armed": sf.get("armed"), "faults": sf.get("faults"),
        "fault_latched_t_ms": sf.get("fault_latched_t_ms"), "interlocks": sf.get("interlocks"),
        "requested": [{k: q.get(k) for k in ("actuator", "command", "limit", "t_ms", "decision", "decision_reason")}
                      for q in act.get("requested") or []],
        "feedback": [{k: f.get(k) for k in ("actuator", "t_ms", "position", "current_a", "tension_n", "status")}
                     for f in act.get("feedback") or []],
        "final_state": oc.get("final_state"), "stop_reason": oc.get("stop_reason"),
        "milestones": oc.get("milestones"),
    }


def core_diff(a: list[dict], b: list[dict]) -> str | None:
    if len(a) != len(b):
        return f"record count {len(a)} != {len(b)}"
    for i, (x, y) in enumerate(zip(a, b)):
        d = value_diff(record_core(x), record_core(y), f"record[{i}]")
        if d:
            return d
    return None


# --------------------------------------------------------------------------------------------
# The shared cross-implementation case list (firmware vs Python in tests/integration/test_cross_impl.py,
# TS vs Python via tools/gen_web_fixtures.py -> apps/web-demo/tests/cross_impl.test.ts).
# --------------------------------------------------------------------------------------------
ALL_SCENARIOS = ("idle", "bite", "noise_current", "rod_vibration", "manual_handling", "sensor_failure",
                 "excessive_tension", "motor_stall", "comm_loss")
SCENARIO_SEEDS = tuple(range(1, 21))
# fault injections: "<kind>@<t_ms|retrieval+ms>[:value]" (docs/engine-spec.md)
INJECTION_SPECS = (
    "sensor_disconnect@1000", "sensor_disconnect@retrieval+200", "sensor_nan@1700", "sensor_nan@retrieval+40",
    "sensor_out_of_range@2500", "sensor_stuck@1000", "over_tension@1000:25", "over_tension@retrieval+300:8.5",
    "over_tension@retrieval+100:7.9", "motor_stall@retrieval+100", "encoder_loss@retrieval+260", "hook_jam@0",
    "comm_loss@1000", "comm_loss@retrieval+0", "link_down@2000", "undervoltage@1500",
    "undervoltage@retrieval+100:10", "watchdog_reset@0", "estop@1000", "estop@retrieval+60", "over_temp@2000",
)
INJECTION_SCENARIOS = ("bite", "idle")
INJECTION_SEEDS = (1, 2)
OVERRIDE_SETS = (
    {"trigger_delta_n": 0.8}, {"retrieval_force_limit_n": 3.0}, {"hook_to_retrieval_gap_ms": 0},
    {"over_tension_samples": 1}, {"stall_window_ms": 100, "stall_grace_ms": 100},
    {"comm_timeout_ms": 100, "retrieval_speed_m_s": 0.2},
)
OVERRIDE_SEEDS = (1, 2)


def cases() -> list[dict]:
    out = []
    for sc in ALL_SCENARIOS:
        for armed in (True, False):
            for seed in SCENARIO_SEEDS:
                out.append({"group": "scenario", "scenario": sc, "seed": seed, "armed": armed, "injections": [],
                            "overrides": {}})
    for spec in INJECTION_SPECS:
        for sc in INJECTION_SCENARIOS:
            for armed in (True, False):
                for seed in INJECTION_SEEDS:
                    out.append({"group": "injection", "scenario": sc, "seed": seed, "armed": armed,
                                "injections": [spec], "overrides": {}})
    for ov in OVERRIDE_SETS:
        for sc in ALL_SCENARIOS:
            for seed in OVERRIDE_SEEDS:
                out.append({"group": "override", "scenario": sc, "seed": seed, "armed": True, "injections": [],
                            "overrides": dict(ov)})
    return out


def case_id(c: dict) -> str:
    s = f"{c['scenario']}-{c['seed']}-{'armed' if c['armed'] else 'disarmed'}"
    for i in c["injections"]:
        s += f"-{i}"
    for k, v in sorted(c["overrides"].items()):
        s += f"-{k}={v}"
    return s


def delta_encode(trace: list[dict]) -> list[dict]:
    """Each line keeps only the keys whose value changed since the previous line (first line in full)."""
    out, prev = [], {}
    for line in trace:
        out.append({k: v for k, v in line.items() if k not in prev or prev[k] != v})
        prev = line
    return out
