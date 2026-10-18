"""PROPOSED Konnex task definition for HookPilot: detect_bite_and_execute_hook_set.

This is OUR proposal. Konnex has not published a task schema for non-VLA,
deterministic-controller workloads (see docs/konnex-docs-findings.md, "Unverified").
Field names below are HookPilot's, not Konnex's.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict

TASK_TYPE = "detect_bite_and_execute_hook_set"
TASK_SCHEMA_VERSION = "hookpilot-task-0.1 (proposed, not a Konnex schema)"

DEFAULT_REQUIRED_EVIDENCE_FIELDS = [
    "event_id", "device.device_id", "device.firmware_version", "source.mode",
    "timestamp.start_utc", "timestamp.clock_quality", "calibration.calibration_version",
    "sensor_data.sample_rate_hz", "sensor_data.units", "features.peak_delta_n",
    "features.sensor_health", "classification.result", "classification.reason",
    "classification.rule_trace", "classification.classifier", "classification.thresholds",
    "safety.decision", "safety.armed", "safety.faults", "actuation.requested", "actuation.feedback",
    "outcome.final_state", "outcome.stop_reason", "outcome.milestones.fish_landed",
    "integrity.record_sha256", "integrity.hash_algorithm",
]

# Ranges a submitted run's classifier thresholds must fall inside (task-level policy).
# These ranges are themselves provisional placeholders.
DEFAULT_ALLOWED_TRIGGER_PARAMS = {
    "trigger_delta_n": [0.3, 3.0],
    "confirm_min_ms": [100, 1500],
    "min_rate_n_per_s": [0.5, 20.0],
    "max_osc": [1, 20],
    "manual_handling_delta_n": [2.0, 15.0],
    "max_duration_ms": [1000, 20000],
}

DEFAULT_SAFETY_CONSTRAINTS = {
    # names mirror firmware/include/hookpilot/config.h SafetyLimits (snapshot 2026-10-18)
    "retrieval_force_limit_n": 8.0,
    "hard_max_tension_n": 20.0,
    "over_tension_samples": 2,
    "hook_set_travel_limit_mm": 15.0,
    "stall_current_a": 1.8,
    "stall_window_ms": 200.0,
    "stall_min_progress_m": 0.005,
    # "assist_pause_n" (stop winding above N so the drag governs) is optional: the Python reference
    # engine implements it, firmware 2026-10-18 does not. Add it to a task to require it.
    "require_armed_for_actuation": True,
    "no_simultaneous_actuation": True,
    "safe_stop_on_fault": True,
    "status": "provisional – set from bench calibration and mechanism tests",
}


@dataclass
class DeviceIdentity:
    device_id: str
    firmware_version: str
    hardware_revision: str = "none-simulation"
    public_key: str | None = None  # no secure element in the prototype -> None


@dataclass
class TaskSpec:
    task_id: str
    device: DeviceIdentity
    test_scenario: str
    seed: int | None = None
    mode: str = "simulation"
    allowed_trigger_params: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_ALLOWED_TRIGGER_PARAMS))
    safety_constraints: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_SAFETY_CONSTRAINTS))
    required_evidence_fields: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_EVIDENCE_FIELDS))
    task_type: str = TASK_TYPE

    @property
    def firmware_version(self) -> str:
        return self.device.firmware_version

    def to_dict(self) -> dict:
        d = asdict(self)
        d["task_schema_version"] = TASK_SCHEMA_VERSION
        d["firmware_version"] = self.device.firmware_version
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSpec":
        d = dict(d)
        d.pop("task_schema_version", None)
        d.pop("firmware_version", None)
        dev = DeviceIdentity(**d.pop("device"))
        return cls(device=dev, **d)


def task_result_from_record(record: dict, bundle_sha256: str | None = None, chain_head: str | None = None) -> dict:
    """Map an event record to the proposed task OUTPUTS."""
    fb = (record.get("actuation") or {}).get("feedback") or []
    req = (record.get("actuation") or {}).get("requested") or []
    cur = [f["current_a"] for f in fb if f.get("current_a") is not None]
    ten = [f["tension_n"] for f in fb if f.get("tension_n") is not None]
    hook = [f["position"] for f in fb if f.get("actuator") == "hook_set" and f.get("position") is not None]
    sd = record.get("sensor_data") or {}
    integ = record.get("integrity") or {}
    outc = record.get("outcome") or {}
    return {
        "detection_result": (record.get("classification") or {}).get("result"),
        "sensor_data_ref": {"raw_data_ref": sd.get("raw_data_ref"), "raw_data_sha256": sd.get("raw_data_sha256"),
                            "inline_samples": len(sd.get("samples") or [])},
        "rationale": {"reason": (record.get("classification") or {}).get("reason"),
                      "rule_trace": (record.get("classification") or {}).get("rule_trace")},
        "actuator_telemetry": {"requested": [r.get("actuator") for r in req], "feedback_samples": len(fb),
                               "peak_current_a": max(cur) if cur else None,
                               "peak_tension_n": max(ten) if ten else None,
                               "max_hook_travel": max(hook) if hook else None,
                               "simulated": (record.get("actuation") or {}).get("simulated")},
        "safety_status": {"decision": (record.get("safety") or {}).get("decision"),
                          "armed": (record.get("safety") or {}).get("armed"),
                          "faults": (record.get("safety") or {}).get("faults")},
        "integrity_metadata": {"record_sha256": integ.get("record_sha256"),
                               "prev_record_sha256": integ.get("prev_record_sha256"),
                               "chain_head": chain_head, "bundle_sha256": bundle_sha256,
                               "signature": integ.get("signature"),
                               "signature_note": "null: prototype has no secure element; records are hash-chained, not hardware-signed"},
        "final_status": {"final_state": outc.get("final_state"), "stop_reason": outc.get("stop_reason"),
                         "milestones": outc.get("milestones"), "test_outcome": outc.get("test_outcome")},
    }
