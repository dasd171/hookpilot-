"""Flatten event records into CSV (one row per record)."""
from __future__ import annotations

import csv

COLUMNS = [
    ("event_id", ("event_id",)), ("device_id", ("device", "device_id")),
    ("firmware_version", ("device", "firmware_version")), ("mode", ("source", "mode")),
    ("scenario", ("source", "scenario")), ("seed", ("source", "seed")),
    ("ground_truth_label", ("source", "ground_truth_label")), ("start_utc", ("timestamp", "start_utc")),
    ("clock_quality", ("timestamp", "clock_quality")), ("calibration_version", ("calibration", "calibration_version")),
    ("sample_rate_hz", ("sensor_data", "sample_rate_hz")), ("raw_data_ref", ("sensor_data", "raw_data_ref")),
    ("raw_data_sha256", ("sensor_data", "raw_data_sha256")), ("baseline_n", ("features", "baseline_n")),
    ("peak_delta_n", ("features", "peak_delta_n")), ("max_rate_n_per_s", ("features", "max_rate_n_per_s")),
    ("duration_above_ms", ("features", "duration_above_ms")), ("oscillation_count", ("features", "oscillation_count")),
    ("sensor_health", ("features", "sensor_health")), ("classification", ("classification", "result")),
    ("classifier", ("classification", "classifier")), ("reason", ("classification", "reason")),
    ("safety_decision", ("safety", "decision")), ("armed", ("safety", "armed")),
    ("final_state", ("outcome", "final_state")), ("stop_reason", ("outcome", "stop_reason")),
    ("bite_detected", ("outcome", "milestones", "bite_detected")),
    ("hook_set_completed", ("outcome", "milestones", "hook_set_completed")),
    ("retrieval_completed", ("outcome", "milestones", "retrieval_completed")),
    ("fish_landed", ("outcome", "milestones", "fish_landed")), ("test_outcome", ("outcome", "test_outcome")),
    ("record_sha256", ("integrity", "record_sha256")), ("prev_record_sha256", ("integrity", "prev_record_sha256")),
]


def _get(r, path):
    for p in path:
        if not isinstance(r, dict):
            return None
        r = r.get(p)
    return r


def to_csv(records: list[dict], out_path: str) -> int:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in COLUMNS] + ["faults", "n_samples"])
        for r in records:
            faults = ";".join((r.get("safety") or {}).get("faults") or [])
            n = len((r.get("sensor_data") or {}).get("samples") or [])
            w.writerow([_get(r, p) for _, p in COLUMNS] + [faults, n])
    return len(records)
