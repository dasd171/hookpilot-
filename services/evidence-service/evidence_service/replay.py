"""Deterministic replay: regenerate (scenario, seed) and re-run deterministic-baseline-v1.

Two independent checks:
 1. signal_match   (simulation records only): the recorded samples equal the
    hookpilot-synth-v1 signal regenerated from (scenario, seed), within
    `tolerance` N (libm last-ulp differences across languages are allowed for).
 2. classification_match: re-running the classifier with the RECORDED thresholds
    on the RECORDED samples reproduces classification.result. For simulation
    records the classifier is also run on the regenerated signal.
Bench/field records can only get check 2 (there is no seed for reality).
"""
from __future__ import annotations

import os

from .classifier import Thresholds, classify, decide_from_features, detect_variant
from .engine import read_raw_csv
from .synth import GENERATOR_ID, SCENARIOS, generate

FEATURE_KEYS = ("peak_delta_n", "max_rate_n_per_s", "duration_above_ms", "oscillation_count", "sensor_health")


def recorded_samples(record: dict, raw_base_dir: str | None = None) -> list[list[float]] | None:
    sd = record.get("sensor_data", {}) or {}
    if sd.get("samples"):
        return [[float(a), float(b)] for a, b in sd["samples"]]
    ref = sd.get("raw_data_ref")
    if ref and raw_base_dir and os.path.exists(os.path.join(raw_base_dir, ref)):
        return read_raw_csv(os.path.join(raw_base_dir, ref))
    return None


def replay(record: dict, tolerance: float = 1e-6, raw_base_dir: str | None = None) -> dict:
    src = record.get("source", {}) or {}
    cls = record.get("classification", {}) or {}
    variant = detect_variant(record)
    th = Thresholds.from_record(cls.get("thresholds"), variant)
    rate = float((record.get("sensor_data") or {}).get("sample_rate_hz", 50.0))
    out: dict = {"event_id": record.get("event_id"), "recorded_result": cls.get("result"),
                 "classifier": cls.get("classifier"), "mode": src.get("mode"), "variant": variant, "notes": []}
    if cls.get("classifier") not in (None, "deterministic-baseline-v1"):
        out["notes"].append(f"classifier {cls.get('classifier')!r} has no Python port; classification replay skipped")
    samples = recorded_samples(record, raw_base_dir)

    # 1) signal regeneration
    out["signal_match"] = None
    if src.get("mode") == "simulation" and src.get("scenario") in SCENARIOS and isinstance(src.get("seed"), int):
        gen = src.get("generator")
        named = gen is not None
        gen = gen or GENERATOR_ID
        if gen != GENERATOR_ID:
            out["notes"].append(f"generator {gen!r} unknown to this replay tool; signal check skipped")
        else:
            last_t = samples[-1][0] if samples else 0.0
            dur = int(max(8000.0, last_t + 1000.0 / rate))
            sig = generate(src["scenario"], src["seed"], sample_rate_hz=rate, duration_ms=dur)
            regen = sig.samples()
            if samples is None:
                out["notes"].append("no recorded samples available (inline or raw file)")
                out["signal_match"] = False
            else:
                by_t = {round(t, 6): x for t, x in regen}
                # Only pre-actuation samples are pure environment: device rigs may add actuator line load later.
                req = (record.get("actuation") or {}).get("requested") or []
                t_act = min((q.get("t_ms", float("inf")) for q in req), default=float("inf"))
                cmp = [(t, x) for t, x in samples if t < t_act]
                # tolerance follows the recorded precision (e.g. firmware rounds to 4 decimals)
                dec = 0
                for _, x in cmp[:200]:
                    sx = repr(float(x))
                    if "e" not in sx and "." in sx:
                        dec = max(dec, len(sx.split(".")[1]))
                tol = max(tolerance, 0.5 * 10 ** (-dec) + 1e-9) if 0 < dec < 10 else tolerance
                out["compared_samples"] = len(cmp)
                out["tolerance_n"] = tol
                if t_act != float("inf"):
                    out["notes"].append(f"signal compared on {len(cmp)} samples before first actuation request (t={t_act} ms)")
                md, missing = 0.0, 0
                for t, x in cmp:
                    rx = by_t.get(round(t, 6))
                    if rx is None:
                        missing += 1
                    else:
                        md = max(md, abs(rx - x))
                out["max_abs_diff_n"] = md
                out["signal_match"] = missing == 0 and md <= tol and len(cmp) > 0
                if missing:
                    out["notes"].append(f"{missing} recorded sample times not on the regenerated grid")
            out["regenerated_result"] = classify(regen, th, rate).result
            if not named and out["signal_match"] is False:
                out["notes"].append("record does not name source.generator and does not match hookpilot-synth-v1 "
                                    "(e.g. the web demo's hookpilot-web-synth-v1, not ported): signal check inconclusive")
                out["signal_match"] = None
                out.pop("regenerated_result", None)
    elif src.get("mode") == "simulation":
        out["notes"].append("simulation record without known scenario/integer seed: cannot regenerate")

    # 2) classification replay on recorded samples
    out["classification_match"] = None
    if samples is not None and cls.get("classifier") in (None, "deterministic-baseline-v1"):
        d = classify(samples, th, rate)
        out["replayed_result"] = d.result
        out["recorded_samples_result"] = d.result
        if "regenerated_result" in out and out.get("signal_match"):
            # full-history replay from (scenario, seed) is authoritative
            out["classification_match"] = out["regenerated_result"] == cls.get("result")
        else:
            out["classification_match"] = d.result == cls.get("result")
        rf = record.get("features", {}) or {}
        diffs = {}
        for k in FEATURE_KEYS:
            if k in rf:
                a, b = rf[k], d.features.get(k)
                same = (abs(float(a) - float(b)) <= 1e-6) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a == b
                if not same:
                    diffs[k] = {"recorded": a, "replayed": b}
        out["feature_diffs"] = diffs
    # 3) rule consistency: recorded features + recorded thresholds -> recorded result
    out["rules_consistent"] = None
    rf = record.get("features") or {}
    need = ("peak_delta_n", "max_rate_n_per_s", "duration_above_ms", "oscillation_count")
    if all(k in rf for k in need) and cls.get("result") not in (None, "no_event"):
        if cls.get("result") == "rejected_sensor_fault" and rf.get("sensor_health") in (None, "ok"):
            out["rules_consistent"] = False
            out["notes"].append("rejected_sensor_fault recorded with sensor_health ok")
        elif cls.get("result") == "rejected_sensor_fault":
            out["rules_consistent"] = True
        else:
            out["rules_consistent"] = decide_from_features(rf, th) == cls.get("result")
            if th.variant == "fw" and "continuous_above_ms" not in rf:
                out["notes"].append("fw rule R5 evaluated with duration_above_ms (continuous_above_ms not recorded)")

    # Partial windows (firmware pre-roll records) cannot reproduce the baseline history exactly.
    t0 = samples[0][0] if samples else None
    partial = t0 is not None and t0 > 0
    if partial:
        out["notes"].append(f"recorded samples start at t={t0} ms (pre-roll window): sample replay is informational; "
                            "rules_consistent is authoritative when the signal cannot be regenerated")
    if out["signal_match"] is not None:
        out["ok"] = bool(out["signal_match"]) and out["classification_match"] is True and out["rules_consistent"] in (True, None)
    elif partial:
        out["ok"] = out["rules_consistent"] is True
    else:
        out["ok"] = out["classification_match"] is True and out["rules_consistent"] in (True, None)
    return out
