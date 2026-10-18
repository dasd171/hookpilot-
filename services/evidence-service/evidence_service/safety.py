"""Reference SIMULATION of the independent safety controller (host-side model).

Limit names/values mirror firmware/include/hookpilot/config.h (SafetyLimits,
ActuatorParams; snapshot 2026-10-18) so Python-engine records are comparable with
firmware `make sim` output. It is NOT the safety implementation: on hardware the
safety path is an independent MCU + hard-wired e-stop chain (docs/safety.md).

The classifier can only REQUEST an actuation; this controller PERMITs or DENIEs.
All limits are provisional placeholders (bench calibration required).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

PROVISIONAL = "provisional – set from bench calibration and mechanism tests"


@dataclass
class SafetyLimits:
    retrieval_force_limit_n: float = 8.0   # OVER_TENSION while retrieving / requesting retrieval
    hard_max_tension_n: float = 20.0       # OVER_TENSION in any state
    over_tension_samples: int = 2          # consecutive samples above a limit before the fault
    sensor_min_n: float = -5.0
    sensor_max_n: float = 49.9
    stuck_samples: int = 15
    hook_set_timeout_ms: float = 500.0
    retrieval_timeout_ms: float = 4000.0
    hook_set_travel_limit_mm: float = 15.0  # mechanical end stop sits just beyond this
    stall_current_a: float = 1.8
    stall_window_ms: float = 200.0
    stall_min_progress_m: float = 0.005
    stall_grace_ms: float = 200.0
    comm_timeout_ms: float = 300.0
    undervoltage_v: float = 10.5            # 3S Li-ion, ~3.5 V/cell
    nominal_battery_v: float = 11.1
    # Python-engine-only parameters (documented in docs/safety.md):
    assist_pause_n: float = 6.0             # retrieval assist stops winding above this; the reel drag governs
    actuator_gap_ms: float = 40.0           # dead time between hook-set end and retrieval start

    def to_record(self) -> dict:
        d = asdict(self)
        d["status"] = PROVISIONAL
        return d

    @classmethod
    def from_record(cls, d: dict | None) -> "SafetyLimits":
        if not d:
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ActuatorParams:
    hook_set_target_mm: float = 12.0
    hook_set_speed_mm_s: float = 60.0
    retrieval_target_m: float = 1.0
    retrieval_speed_m_s: float = 0.5


class SafetyController:
    """Permit/deny gate + latched faults. Faults latch until explicit reset."""

    def __init__(self, limits: SafetyLimits | None = None, armed: bool = False) -> None:
        self.limits = limits or SafetyLimits()
        self.armed = armed
        self.faults: list[str] = []
        self.active_actuator: str | None = None
        self.battery_v = self.limits.nominal_battery_v
        self.log: list[str] = []

    def latch(self, fault: str, t_ms: float) -> None:
        if fault not in self.faults:
            self.faults.append(fault)
            self.log.append(f"t={t_ms:.0f}ms FAULT {fault} -> SAFE_STOP (all actuators de-energised)")
        self.active_actuator = None

    def request(self, actuator: str, t_ms: float, tension_n: float, sensor_health: str) -> tuple[bool, str]:
        L = self.limits
        reasons = []
        if not self.armed:
            reasons.append("not armed")
        if self.faults:
            reasons.append("faults latched: " + ",".join(self.faults))
        if sensor_health != "ok":
            reasons.append(f"sensor health {sensor_health}")
        if tension_n >= L.hard_max_tension_n:
            reasons.append(f"tension {tension_n:.2f} N >= hard max {L.hard_max_tension_n} N")
        if actuator == "retrieval" and tension_n >= L.retrieval_force_limit_n:
            reasons.append(f"tension {tension_n:.2f} N >= retrieval force limit {L.retrieval_force_limit_n} N")
        if self.battery_v < L.undervoltage_v:
            reasons.append("undervoltage")
        if self.active_actuator is not None and self.active_actuator != actuator:
            reasons.append(f"{self.active_actuator} active (no simultaneous actuation)")
        if reasons:
            msg = f"t={t_ms:.0f}ms DENY {actuator}: " + "; ".join(reasons)
            self.log.append(msg)
            return False, msg
        self.active_actuator = actuator
        msg = f"t={t_ms:.0f}ms PERMIT {actuator}"
        self.log.append(msg)
        return True, msg

    def release(self, actuator: str) -> None:
        if self.active_actuator == actuator:
            self.active_actuator = None

    def reset(self, faults_cleared: bool) -> bool:
        """SAFE_STOP exits only via explicit reset with faults cleared -> IDLE disarmed."""
        if not faults_cleared:
            return False
        self.faults.clear()
        self.armed = False
        self.active_actuator = None
        return True
