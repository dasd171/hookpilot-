"""Python reference engine (SIMULATION): (scenario, seed[, armed, injections, overrides]) -> event records.

Tick-for-tick port of the firmware control core and host rig (the ground truth):
  firmware/src/core/controller.cpp      HookPilotController (state machine, records)
  firmware/src/core/safety_controller.cpp  -> evidence_service.safety
  firmware/host/mocks.cpp                MockHookActuator, MockRetrieval, MockTensionSensor, MockComm, ...
  firmware/host/sim_harness.cpp          SimRig + runScenario (line load, injections, end of window)
The normative description is docs/engine-spec.md. tests/integration/test_cross_impl.py compares this
engine's per-tick trace with `hookpilot_sim --trace` and apps/web-demo/tests/cross_impl.test.ts compares
the web demo with fixtures generated from here (tools/gen_web_fixtures.py).

The actuator models are kinematic placeholders (no physics), labelled actuation.simulated = true.
fish_landed is never inferred: always "unknown". Every limit is provisional.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone

from .canonical import js_number, seal
from .classifier import CLASSIFIER_ID, IMPLEMENTATION, Detector, FwProcessor, Thresholds, _Sample
from .safety import (CRITICAL_MASK, FAULT_NAMES, PROVISIONAL, ActuatorGate, ActuatorParams, F, SafetyController,
                     SafetyLimits, SimCoupling, dataclass_keys, fault_list, join_faults)
from .synth import (EXPECTED_CLASSIFICATION, EXPECTED_FAULT, GENERATOR_ID, GROUND_TRUTH, POSITIVE_SCENARIOS,
                    generate)

SCHEMA_VERSION = "1.0.0"
DEFAULT_DEVICE = {"device_id": "hp-sim-001", "firmware_version": "sim-py-0.1.0",
                  "hardware_revision": "none-simulation"}
SIM_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)  # firmware kSimulatedEpochMs
DT_MS = 20
PREROLL_MS = 500
MAX_RECORD_SAMPLES = 400
MAX_RECORD_FEEDBACK = 60
K_RING = 64

INJECTION_KINDS = ("sensor_disconnect", "sensor_nan", "sensor_out_of_range", "sensor_stuck", "over_tension",
                   "motor_stall", "encoder_loss", "hook_jam", "comm_loss", "link_down", "undervoltage", "estop",
                   "over_temp", "watchdog_reset")


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


# ============================================================================================
# Mocks (port of firmware/host/mocks.cpp) - SIMULATION ONLY
# ============================================================================================
class Feedback:
    __slots__ = ("position", "current_a", "speed", "active", "at_target", "position_valid", "current_valid", "status")

    def __init__(self) -> None:
        self.position = 0.0
        self.current_a = 0.0
        self.speed = 0.0
        self.active = False
        self.at_target = False
        self.position_valid = True
        self.current_valid = True
        self.status = "idle"

    def copy(self) -> "Feedback":
        c = Feedback()
        for k in self.__slots__:
            setattr(c, k, getattr(self, k))
        return c


class MockHook:
    """Servo hook-set actuator: position (mm), slew at commanded speed, current feedback."""

    def __init__(self, tolerance_mm: float = 0.5) -> None:
        self.fb = Feedback()
        self.target = 0.0
        self.speed = 0.0
        self.last_ms = 0
        self.have_last = False
        self.jam = False
        self.fb_loss = False
        self.overshoot = 0.0
        self.tolerance_mm = tolerance_mm
        self.moves = 0

    def move_to(self, pos: float, speed: float) -> None:
        self.target, self.speed = pos, speed
        self.fb.active, self.fb.at_target, self.fb.status = True, False, "moving"
        self.moves += 1

    def stop(self) -> None:
        self.fb.active = False
        self.fb.speed = 0.0
        self.fb.current_a = 0.0
        if self.fb.status != "idle":
            self.fb.status = "stopped"

    def update(self, now: int) -> None:
        dt = (now - self.last_ms) / 1000.0 if self.have_last else 0.0
        self.last_ms, self.have_last = now, True
        fb = self.fb
        fb.position_valid = not self.fb_loss
        if not fb.active:
            fb.current_a = 0.05
            fb.speed = 0.0
            return
        if self.jam:
            fb.speed, fb.current_a, fb.status = 0.0, 1.6, "jammed"
            return
        direction = 1.0 if self.target >= fb.position else -1.0
        step = self.speed * dt
        remaining = abs(self.target - fb.position)
        if self.overshoot > 0 and self.target > 0:
            fb.position += step
            if fb.position >= self.target + self.overshoot:
                fb.position = self.target + self.overshoot
        elif step >= remaining:
            fb.position = self.target
        else:
            fb.position += direction * step
        fb.speed = self.speed
        fb.current_a = 0.8
        if abs(fb.position - self.target) <= self.tolerance_mm and self.overshoot <= 0:
            fb.active, fb.at_target, fb.speed, fb.current_a, fb.status = False, True, 0.0, 0.1, "at_target"


class MockRetrieval:
    """DC motor + encoder + current-sense retrieval: position (m of line), current (A)."""

    def __init__(self, coupling: SimCoupling) -> None:
        self.fb = Feedback()
        self.target = 0.0
        self.speed = 0.0
        self.load_n = 0.0
        self.last_ms = 0
        self.have_last = False
        self.stall = False
        self.enc_loss = False
        self.runaway = False
        self.base_a = coupling.retrieval_current_base_a
        self.per_n_a = coupling.retrieval_current_per_n_a
        self.starts = 0

    def start(self, target: float, speed: float) -> None:
        self.target, self.speed = target, speed
        self.fb.active, self.fb.at_target, self.fb.status = True, False, "retrieving"
        self.starts += 1

    def stop(self) -> None:
        self.fb.active = False
        self.fb.speed = 0.0
        self.fb.current_a = 0.0
        if self.fb.status not in ("idle", "at_target"):
            self.fb.status = "stopped"

    def reset_position(self) -> None:
        self.fb.position = 0.0

    def update(self, now: int) -> None:
        dt = (now - self.last_ms) / 1000.0 if self.have_last else 0.0
        self.last_ms, self.have_last = now, True
        fb = self.fb
        fb.position_valid = not self.enc_loss
        if not fb.active:
            fb.current_a = 0.0
            fb.speed = 0.0
            return
        if self.stall:
            fb.speed, fb.current_a, fb.status = 0.0, 2.5, "stalled"
            return
        step = self.speed * dt
        if not self.runaway and fb.position + step >= self.target:
            fb.position = self.target
            fb.active, fb.at_target, fb.speed, fb.current_a, fb.status = False, True, 0.0, 0.0, "at_target"
            return
        if not self.enc_loss:
            fb.position += step
        fb.speed = self.speed
        fb.current_a = self.base_a + self.per_n_a * max(0.0, self.load_n)
        fb.status = "retrieving"


class MockSensor:
    """Replays a synthetic signal sample by sample, with injectable faults. Returns the value (NaN = no reading)."""

    def __init__(self, tension: list[float]) -> None:
        self.sig = tension
        self.idx = 0
        self.additive = 0.0
        self.disconnect = self.nan = self.oor = self.stuck = self.override = False
        self.oor_value = 60.0
        self.override_value = 0.0
        self.have_stuck = False
        self.stuck_value = 0.0

    def read(self) -> float:
        if self.idx < len(self.sig):
            v = self.sig[self.idx]
            self.idx += 1
        else:
            return math.nan
        v += self.additive
        if self.override:
            v = self.override_value
        if self.stuck:
            if not self.have_stuck:
                self.stuck_value = v
                self.have_stuck = True
            v = self.stuck_value
        if self.disconnect or self.nan:
            return math.nan
        if self.oor:
            v = self.oor_value
        return v


class MockHealth:
    def __init__(self) -> None:
        self.voltage = 11.4
        self.temp = 25.0
        self.estop = False


class MockComm:
    """Heartbeat arrives on every poll while the link is up (and heartbeats are not lost)."""

    def __init__(self) -> None:
        self.up = True
        self.hb_lost = False
        self.last_hb = 0

    def poll(self, now: int) -> None:
        if self.up and not self.hb_lost:
            self.last_hb = now


def empty_sample() -> _Sample:
    s = _Sample()
    s.t_ms, s.raw, s.valid, s.health = 0, 0.0, False, "disconnected"
    s.x = s.smoothed = s.baseline = s.delta = s.rate = s.variance = s.detrended = 0.0
    return s


@dataclass
class SafetyInputs:
    now_ms: int = 0
    sample: _Sample = field(default_factory=empty_sample)
    hook: Feedback = field(default_factory=Feedback)
    retrieval: Feedback = field(default_factory=Feedback)
    bus_voltage_v: float = 0.0
    temperature_c: float = 25.0
    estop_active: bool = False
    comm_link_ok: bool = True
    last_heartbeat_ms: int = 0


# ============================================================================================
# Controller (port of firmware/src/core/controller.cpp)
# ============================================================================================
class _Event:
    def __init__(self) -> None:
        self.event_id = ""
        self.armed = False
        self.start_ms = 0
        self.end_ms = 0
        self.samples: list[list[float]] = []
        self.invalid_omitted = 0
        self.features = {"baseline_n": 0.0, "peak_delta_n": 0.0, "max_rate_n_per_s": 0.0, "duration_above_ms": 0.0,
                         "continuous_above_ms": 0.0, "short_term_variance": 0.0, "oscillation_count": 0,
                         "sensor_health": "ok"}
        self.result = "no_event"
        self.reason = ""
        self.rule_trace: list[str] = []
        self.trigger_t: int | None = None
        self.decision_t: int | None = None
        self.decision = "deny"
        self.interlocks: dict = {}
        self.faults = 0
        self.fault_latched_t_ms: dict = {}
        self.requested: list[dict] = []
        self.feedback: list[dict] = []
        self.final_state = "MONITOR"
        self.stop_reason = ""
        self.bite_detected = self.hook_set_completed = self.retrieval_completed = False


@dataclass
class EngineConfig:
    thresholds: Thresholds = field(default_factory=lambda: Thresholds.defaults("fw"))
    limits: SafetyLimits = field(default_factory=SafetyLimits)
    actuators: ActuatorParams = field(default_factory=ActuatorParams)
    coupling: SimCoupling = field(default_factory=SimCoupling)
    observe_when_disarmed: bool = True


def apply_overrides(cfg: EngineConfig, overrides: dict | None) -> EngineConfig:
    """`--set name=value` semantics shared with firmware sim_main.cpp and the web demo."""
    if not overrides:
        return cfg
    th, L, A, C = replace(cfg.thresholds), replace(cfg.limits), replace(cfg.actuators), replace(cfg.coupling)
    for k, v in overrides.items():
        for obj in (th, L, A, C):
            if k in dataclass_keys(type(obj)) and k != "variant":
                cur = getattr(obj, k)
                setattr(obj, k, int(v) if isinstance(cur, int) and not isinstance(cur, bool) else float(v))
                break
        else:
            raise ValueError(f"unknown parameter {k!r}")
        if k in ("sensor_min_n", "sensor_max_n", "stuck_samples"):  # shared by processor + safety
            setattr(L if obj is th else th, k, getattr(obj, k))
    return EngineConfig(th, L, A, C, cfg.observe_when_disarmed)


class Controller:
    def __init__(self, cfg: EngineConfig, rig: "Rig") -> None:
        self.cfg = cfg
        self.rig = rig
        self.proc = FwProcessor(cfg.thresholds)
        self.det = Detector(cfg.thresholds)
        self.safety = SafetyController(cfg.limits)
        self.gate = ActuatorGate(self.safety, rig.hook, rig.retrieval)
        self.state = "IDLE"
        self.sample = empty_sample()
        self.inp = SafetyInputs()
        self.transitions: list[dict] = []
        self.request_log: list[dict] = []
        self.ring: list[tuple[int, float, bool]] = []
        self.ev: _Event | None = None
        self.ev_decided = False
        self.ev_any_permit = False
        self.awaiting_rearm = False
        self.state_since = 0
        self.ticks = 0
        self.event_counter = 0
        self.events_logged = 0
        self.hook_done = False
        self.hook_done_ms = 0
        self.records: list[_Event] = []

    # ---------------------------------------------------------------- helpers
    def _transition(self, to: str, why: str) -> None:
        if to == self.state:
            return
        self.transitions.append({"t": self.inp.now_ms, "from": self.state, "to": to, "reason": why})
        self.state = to
        self.state_since = self.inp.now_ms

    def _gather(self, now: int) -> None:
        r = self.rig
        i = self.inp
        i.now_ms = now
        i.sample = self.sample
        i.hook = r.hook.fb.copy()
        i.retrieval = r.retrieval.fb.copy()
        i.bus_voltage_v = r.health.voltage
        i.temperature_c = r.health.temp
        i.estop_active = r.health.estop
        i.comm_link_ok = r.comm.up
        i.last_heartbeat_ms = r.comm.last_hb

    def begin(self) -> None:
        self.gate.stop_all()
        self.state = "IDLE"
        self.safety.disarm()
        self.proc.reset()
        now = self.rig.now
        self.state_since = now
        if self.rig.watchdog_reset:
            self.safety.latch_fault(F["WATCHDOG_RESET"])
            self._gather(now)
            self._open_event(now)
            ev = self.ev
            ev.start_ms = now
            ev.samples = []
            ev.result = "no_event"
            ev.reason = "Boot after watchdog reset: device is IDLE and disarmed; operator reset required before arming."
            ev.rule_trace.append("boot: watchdog reset cause detected -> latch WATCHDOG_RESET")
            self.ev_decided = True
            self._close_event("IDLE", "watchdog_reset_boot: disarmed, awaiting operator reset")

    def tick(self) -> None:
        r = self.rig
        now = r.now
        self.ticks += 1
        r.comm.poll(now)
        self.gate.update(now)
        v = r.sensor.read()
        s = self.proc.process(now, v)
        self.sample = s
        self.ring.append((now, s.raw, s.valid))
        if len(self.ring) > K_RING:
            del self.ring[0]
        ev = self.ev
        if ev is not None and len(ev.samples) < MAX_RECORD_SAMPLES:
            if s.valid and math.isfinite(s.raw):
                ev.samples.append([now, s.raw])
            elif not math.isfinite(s.raw):
                ev.invalid_omitted += 1
            else:
                ev.samples.append([now, s.raw])
        self._gather(now)
        faults = self.safety.update(self.inp)
        if (faults & CRITICAL_MASK) and self.state != "SAFE_STOP":
            self._enter_safe_stop("safe_stop: " + join_faults(faults & CRITICAL_MASK, "none"))
            return
        st = self.state
        if st == "IDLE":
            self.gate.stop_all()
            if self.cfg.observe_when_disarmed:
                self._monitor_step(now)
        elif st == "SAFE_STOP":
            self.gate.stop_all()
        elif st == "MONITOR":
            self._monitor_step(now)
        elif st == "CANDIDATE":
            if self.det.update(s):
                self._transition("CONFIRM", "candidate window complete or early-exit rule hit")
                self._decide_candidate()
        elif st == "CONFIRM":
            self._decide_candidate()
        elif st == "HOOK_SET":
            fb = self.inp.hook
            if self.ticks % 5 == 0:
                self._record_feedback("hook_set", fb)
            if not self.hook_done and fb.at_target and not fb.active:
                self._record_feedback("hook_set", fb)
                self.ev.hook_set_completed = True
                self.hook_done = True
                self.hook_done_ms = now
            if self.hook_done and now - self.hook_done_ms >= self.cfg.limits.hook_to_retrieval_gap_ms:
                A = self.cfg.actuators
                if self._gated_request("retrieval", "retrieve", A.retrieval_target_m, A.retrieval_speed_m_s):
                    self._transition("RETRIEVE", "hook set stroke complete; retrieval permitted")
                else:
                    self._enter_safe_stop("safe_stop: retrieval denied after hook set ("
                                          + self.ev.requested[-1]["decision_reason"] + ")")
        elif st == "RETRIEVE":
            fb = self.inp.retrieval
            if self.ticks % 5 == 0:
                self._record_feedback("retrieval", fb)
            if fb.at_target and not fb.active:
                r.retrieval.stop()
                self._record_feedback("retrieval", r.retrieval.fb)
                self.ev.retrieval_completed = True
                self._transition("VERIFY", "retrieval travel reached")
                self._gated_request("hook_set", "return_to_rest", self.cfg.limits.hook_set_travel_min_mm,
                                    self.cfg.actuators.hook_set_speed_mm_s)
        elif st == "VERIFY":
            fb = self.inp.hook
            if now - self.state_since >= self.cfg.actuators.verify_ms and not fb.active:
                self._record_feedback("hook_set", fb)
                self._close_event("IDLE", "cycle_complete: actuators stopped, disarmed awaiting operator; "
                                          "fish_landed not inferred")
                self.safety.disarm()
                self.proc.frozen = False
                self.awaiting_rearm = True
                self._transition("IDLE", "cycle complete -> disarmed")

    def _monitor_step(self, now: int) -> None:
        if self.awaiting_rearm:
            if self.det.rearmed(self.sample):
                self.awaiting_rearm = False
            return
        if self.det.should_trigger(self.sample):
            self.proc.frozen = True
            self._open_event(now)
            self.ev.trigger_t = now
            self.det.begin_candidate(self.sample)
            self._transition("CANDIDATE", "delta >= trigger_delta_n" if self.safety.armed
                             else "delta >= trigger_delta_n (observation only: disarmed)")

    def _decide_candidate(self) -> None:
        res, reason, tr, feats = self.det.decide()
        ev = self.ev
        ev.result, ev.reason, ev.rule_trace, ev.features = res, reason, tr, feats
        self.ev_decided = True
        ev.decision_t = self.inp.now_ms
        armed = self.safety.armed
        rest = "MONITOR" if armed else "IDLE"
        if res == "bite_candidate_confirmed":
            ev.bite_detected = True
            if not armed:
                self.proc.frozen = False
                self.awaiting_rearm = True
                self._close_event("IDLE", "disarmed_observation_only: no actuation requested")
                self._transition("IDLE", "observation only (disarmed): no actuation requested")
                return
            self.hook_done = False
            A = self.cfg.actuators
            if self._gated_request("hook_set", "stroke_to", A.hook_set_target_mm, A.hook_set_speed_mm_s):
                self._transition("HOOK_SET", "bite_candidate_confirmed; hook set permitted by safety")
            else:
                self.proc.frozen = False
                self.awaiting_rearm = True
                self._close_event("MONITOR", "hook_set_denied: " + ev.requested[-1]["decision_reason"])
                self._transition("MONITOR", "hook set denied by safety")
            return
        if res == "rejected_sensor_fault":
            self._enter_safe_stop(f"safe_stop: sensor fault during candidate ({feats['sensor_health']})")
            return
        self.proc.frozen = False
        self.awaiting_rearm = True
        self._close_event(rest, f"classifier_rejected: {res}")
        self._transition(rest, f"classifier: {res}")

    def _gated_request(self, actuator: str, command: str, target: float, speed: float) -> bool:
        r = self.rig
        self.inp.hook = r.hook.fb.copy()
        self.inp.retrieval = r.retrieval.fb.copy()
        d = self.gate.request(actuator, target, speed, self.inp)
        entry = {"actuator": actuator, "command": command, "target": target, "speed": speed,
                 "t_ms": self.inp.now_ms, "permitted": d.permitted, "reason": d.reason}
        self.request_log.append(entry)
        if self.ev is not None:
            unit = "mm" if actuator == "hook_set" else "m"
            self.ev.requested.append({"actuator": actuator, "command": command,
                                      "limit": {f"target_{unit}": target, f"speed_{unit}_per_s": speed},
                                      "t_ms": self.inp.now_ms, "decision": "permit" if d.permitted else "deny",
                                      "decision_reason": d.reason})
            if d.permitted:
                self.ev_any_permit = True
                self._record_feedback(actuator, r.hook.fb if actuator == "hook_set" else r.retrieval.fb)
        return d.permitted

    def _record_feedback(self, actuator: str, fb: Feedback) -> None:
        ev = self.ev
        if ev is None or len(ev.feedback) >= MAX_RECORD_FEEDBACK:
            return
        s = self.sample
        tv = s.valid and math.isfinite(s.raw)
        ev.feedback.append({"actuator": actuator, "t_ms": self.inp.now_ms,
                            "position": fb.position if fb.position_valid else None,
                            "current_a": fb.current_a if fb.current_valid else None,
                            "tension_n": s.raw if tv else None, "status": fb.status})

    def _open_event(self, t_trigger: int) -> None:
        ev = _Event()
        self.ev = ev
        self.ev_decided = False
        self.ev_any_permit = False
        self.event_counter += 1
        ev.armed = self.safety.armed
        ev.start_ms = t_trigger
        lo = t_trigger - PREROLL_MS if t_trigger >= PREROLL_MS else 0
        for (t, v, _valid) in self.ring:
            if t < lo or t > t_trigger:
                continue
            if math.isfinite(v):
                ev.samples.append([t, v])
            else:
                ev.invalid_omitted += 1
        if ev.samples:
            ev.start_ms = ev.samples[0][0]

    def _interlocks(self) -> dict:
        i, L = self.inp, self.cfg.limits
        return {"estop_clear": not i.estop_active, "sensor_valid": i.sample.valid,
                "comm_link_ok": i.comm_link_ok and (i.now_ms - i.last_heartbeat_ms <= L.comm_timeout_ms),
                "voltage_ok": i.bus_voltage_v >= L.undervoltage_v, "bus_voltage_v": i.bus_voltage_v,
                "hook_set_active": i.hook.active, "retrieval_active": i.retrieval.active,
                "mutual_exclusion": "enforced", "classifier_can_command_actuators": False,
                "retrieval_force_limit_n": L.retrieval_force_limit_n, "hard_max_tension_n": L.hard_max_tension_n,
                "hook_set_travel_limit_mm": L.hook_set_travel_limit_mm,
                "retrieval_travel_limit_m": L.retrieval_travel_limit_m,
                "hook_set_timeout_ms": L.hook_set_timeout_ms, "retrieval_timeout_ms": L.retrieval_timeout_ms,
                "comm_timeout_ms": L.comm_timeout_ms, "undervoltage_v": L.undervoltage_v,
                "limits_label": PROVISIONAL}

    def _close_event(self, final_state: str, stop_reason: str) -> None:
        ev = self.ev
        if ev is None:
            return
        ev.end_ms = max(self.inp.now_ms, ev.start_ms)
        ev.final_state = final_state
        ev.stop_reason = stop_reason
        lat = self.safety.latched
        ev.faults = lat | (self.safety.active if final_state == "SAFE_STOP" else 0)
        ev.fault_latched_t_ms = {n: self.safety.latch_t[b] for b, n in enumerate(FAULT_NAMES) if lat & (1 << b)}
        ev.interlocks = self._interlocks()
        if final_state == "SAFE_STOP":
            ev.decision = "safe_stop"
        elif self.ev_any_permit:
            ev.decision = "permit"
        else:
            ev.decision = "deny"
        self.records.append(ev)
        self.rig.on_record(ev)
        self.events_logged += 1
        self.ev = None

    def _enter_safe_stop(self, why: str) -> None:
        r = self.rig
        self.gate.stop_all()
        self.safety.disarm()
        s = self.sample
        if self.ev is None:
            self._open_event(self.inp.now_ms)
            ev = self.ev
            ev.features["baseline_n"] = s.baseline
            ev.features["sensor_health"] = s.health
            ev.features["short_term_variance"] = s.variance
            if not s.valid:
                ev.result = "rejected_sensor_fault"
                ev.reason = f"Sensor health '{s.health}' while {self.state}; safety controller entered SAFE_STOP."
            else:
                ev.result = "no_event"
                ev.reason = f"No bite candidate; safety fault while {self.state}."
            ev.rule_trace.append(f"state={self.state} -> {why}")
            self.ev_decided = True
            ev.decision_t = self.inp.now_ms
        elif not self.ev_decided:
            res, reason, tr, feats = self.det.decide()
            ev = self.ev
            ev.result, ev.reason, ev.rule_trace, ev.features = res, reason, tr, feats
            self.ev_decided = True
            ev.decision_t = self.inp.now_ms
            ev.rule_trace.append("interrupted by safety: " + why)
        else:
            self.ev.rule_trace.append(f"state={self.state} interrupted: {why}")
        self._record_feedback("hook_set", r.hook.fb)
        self._record_feedback("retrieval", r.retrieval.fb)
        self._close_event("SAFE_STOP", why)
        self.proc.frozen = False
        self.awaiting_rearm = False
        self._transition("SAFE_STOP", why)

    # ---------------------------------------------------------------- operator requests
    def request_arm(self) -> tuple[bool, str]:
        if self.state != "IDLE":
            return False, f"arm only from IDLE (state={self.state})"
        self._gather(self.rig.now)
        ok, why = self.safety.arm(self.inp)
        if not ok:
            return False, why
        self.awaiting_rearm = False
        self.proc.frozen = False
        self._transition("MONITOR", "armed by operator")
        return True, why

    def request_disarm(self) -> None:
        self.gate.stop_all()
        self.safety.disarm()
        self._gather(self.rig.now)
        if self.state == "SAFE_STOP":
            return
        if self.ev is not None:
            if not self.ev_decided:
                res, reason, tr, feats = self.det.decide()
                ev = self.ev
                ev.result, ev.reason, ev.rule_trace, ev.features = res, reason, tr, feats
                self.ev_decided = True
                ev.decision_t = self.inp.now_ms
                ev.rule_trace.append("interrupted by operator_disarm")
            else:
                self.ev.rule_trace.append(f"state={self.state} interrupted: operator_disarm")
            if self.ev_any_permit:
                self._record_feedback("hook_set", self.rig.hook.fb)
                self._record_feedback("retrieval", self.rig.retrieval.fb)
            self._close_event("IDLE", "operator_disarm")
            self.awaiting_rearm = True
        self.proc.frozen = False
        self._transition("IDLE", "disarmed by operator")

    def request_reset(self) -> tuple[bool, str]:
        self._gather(self.rig.now)
        if self.state not in ("SAFE_STOP", "IDLE"):
            return False, "reset only from SAFE_STOP or IDLE"
        if not self.safety.clear_faults():
            return False, "faults still present: " + join_faults(self.safety.latched, "none")
        self.gate.stop_all()
        self.rig.retrieval.reset_position()
        self.proc.reset()
        self.awaiting_rearm = False
        self._transition("IDLE", "explicit reset with faults cleared")
        return True, "faults cleared"

    def log_no_event_summary(self) -> None:
        if self.ev is not None:
            return
        self._open_event(self.inp.now_ms)
        s = self.sample
        ev = self.ev
        ev.features["baseline_n"] = s.baseline
        ev.features["sensor_health"] = s.health
        ev.features["short_term_variance"] = s.variance
        ev.result = "no_event"
        ev.reason = ("No candidate in the observation window: delta never reached trigger_delta_n "
                     f"{self.cfg.thresholds.trigger_delta_n:.3f} N.")
        ev.rule_trace.append(f"t={self.inp.now_ms}ms end of observation window: no candidate opened "
                             f"(state={self.state})")
        self.ev_decided = True
        self._close_event(self.state, "no_event: nothing to actuate")


# ============================================================================================
# Rig + run (port of firmware/host/sim_harness.cpp)
# ============================================================================================
@dataclass
class Injection:
    kind: str
    at_ms: float = 0.0
    rel_retrieval: bool = False
    value: float | None = None

    @classmethod
    def parse(cls, spec: "str | Injection") -> "Injection":
        if isinstance(spec, Injection):
            return spec
        kind, _, rest = spec.partition("@")
        if kind not in INJECTION_KINDS:
            raise ValueError(f"unknown injection kind {kind!r}")
        inj = cls(kind)
        if rest:
            when, _, val = rest.partition(":")
            if val:
                inj.value = float(val)
            if when.startswith("retrieval+"):
                inj.rel_retrieval = True
                when = when[len("retrieval+"):]
            inj.at_ms = float(when)
        return inj

    def spec(self) -> str:
        w = ("retrieval+" if self.rel_retrieval else "") + js_number(self.at_ms)
        return f"{self.kind}@{w}" + (f":{js_number(self.value)}" if self.value is not None else "")


class Rig:
    def __init__(self, cfg: EngineConfig, tension: list[float], watchdog_reset: bool = False) -> None:
        self.cfg = cfg
        self.now = 0
        self.first = True
        self.watchdog_reset = watchdog_reset
        self.sensor = MockSensor(tension)
        self.hook = MockHook(cfg.actuators.hook_set_position_tol_mm)
        self.retrieval = MockRetrieval(cfg.coupling)
        self.health = MockHealth()
        self.comm = MockComm()
        self.last_load = 0.0
        self.record_listeners = []
        self.ctl = Controller(cfg, self)

    def on_record(self, ev: _Event) -> None:
        for fn in self.record_listeners:
            fn(ev)

    def step(self) -> None:
        if not self.first:
            self.now += DT_MS
        self.first = False
        C = self.cfg.coupling
        load = 0.0
        if self.hook.fb.active:
            load += C.hook_set_line_load_n
        if self.retrieval.fb.active:
            load += C.retrieval_line_load_n
        self.sensor.additive = load
        self.last_load = load
        self.retrieval.load_n = self.ctl.sample.x
        self.ctl.tick()

    def apply(self, inj: Injection) -> None:
        k, v = inj.kind, inj.value
        s = self.sensor
        if k == "sensor_disconnect":
            s.disconnect = True
        elif k == "sensor_nan":
            s.nan = True
        elif k == "sensor_out_of_range":
            s.oor, s.oor_value = True, (60.0 if v is None else v)
        elif k == "sensor_stuck":
            s.stuck, s.have_stuck = True, False
        elif k == "over_tension":
            s.override, s.override_value = True, (25.0 if v is None else v)
        elif k == "motor_stall":
            self.retrieval.stall = True
        elif k == "encoder_loss":
            self.retrieval.enc_loss = True
        elif k == "hook_jam":
            self.hook.jam = True
        elif k == "comm_loss":
            self.comm.hb_lost = True
        elif k == "link_down":
            self.comm.up = False
        elif k == "undervoltage":
            self.health.voltage = 10.2 if v is None else v
        elif k == "estop":
            self.health.estop = True
        elif k == "over_temp":
            self.health.temp = 75.0 if v is None else v

    @staticmethod
    def _fb(f: Feedback) -> dict:
        return {"pos": f.position, "cur": f.current_a, "active": f.active, "at_target": f.at_target,
                "pos_valid": f.position_valid, "status": f.status}

    def trace_line(self, k: int, phase: str, cur: dict, injected: list[str]) -> dict:
        c = self.ctl
        s = c.sample
        cmds = c.request_log[cur["req"]:]
        cur["req"] = len(c.request_log)
        trans = c.transitions[cur["trans"]:]
        cur["trans"] = len(c.transitions)
        recs = c.records[cur["rec"]:]
        cur["rec"] = len(c.records)
        return {"k": k, "phase": phase, "t": self.now, "state": c.state, "armed": c.safety.armed,
                "raw": s.raw if math.isfinite(s.raw) else None, "valid": s.valid, "health": s.health,
                "x": s.x, "baseline": s.baseline, "delta": s.delta, "load_n": self.last_load,
                "hook": self._fb(self.hook.fb), "retr": self._fb(self.retrieval.fb), "hb_ms": self.comm.last_hb,
                "latched": fault_list(c.safety.latched), "active": fault_list(c.safety.active),
                "cmds": [dict(x) for x in cmds], "trans": [dict(x) for x in trans], "injected": list(injected),
                "records": len(recs)}


@dataclass
class RunResult:
    scenario: str
    seed: int
    armed: bool
    events: list = field(default_factory=list)   # controller _Event objects, in log order
    records: list = field(default_factory=list)  # schema records (unsealed), same order
    trace: list = field(default_factory=list)    # per-tick trace dicts (+ final "end" line)
    final_state: str = "IDLE"
    faults: list = field(default_factory=list)
    transitions: list = field(default_factory=list)
    reached_retrieve: bool = False


def run_scenario(scenario: str, seed: int, *, armed: bool = True, injections=(), overrides: dict | None = None,
                 thresholds: Thresholds | None = None, limits: SafetyLimits | None = None,
                 actuators: ActuatorParams | None = None, coupling: SimCoupling | None = None,
                 duration_ms: int = 8000, trace: bool = False, counter: int = 1, device: dict | None = None,
                 start_utc: datetime | None = None, raw_dir: str | None = None, raw_ref_prefix: str = "raw/",
                 inline_samples: bool = True) -> RunResult:
    """Runs (scenario, seed) exactly like firmware sim_harness.cpp runScenario (docs/engine-spec.md)."""
    cfg = EngineConfig(thresholds or Thresholds.defaults("fw"), limits or SafetyLimits(),
                       actuators or ActuatorParams(), coupling or SimCoupling())
    cfg = apply_overrides(cfg, overrides)
    injs = [Injection.parse(x) for x in injections]
    sig = generate(scenario, seed, duration_ms=duration_ms)
    rig = Rig(cfg, sig.tension_n, watchdog_reset=any(i.kind == "watchdog_reset" for i in injs))
    sched: list[Injection] = []
    fa = sig.params.get("fault_after_retrieval_start_ms", -1)
    if scenario in ("motor_stall", "comm_loss") and fa is not None and fa >= 0:
        sched.append(Injection(scenario, fa, True))
    sched += [i for i in injs if i.kind != "watchdog_reset"]
    done = [False] * len(sched)
    res = RunResult(scenario, seed, armed)
    cur = {"req": 0, "trans": 0, "rec": 0}
    rig.ctl.begin()
    n = len(sig.tension_n)
    retrieval_seen = False
    retrieval_start = 0
    for i in range(n):
        rig.step()
        if i == 0 and armed:
            rig.ctl.request_arm()
        now = rig.now
        if rig.ctl.state == "RETRIEVE" and not retrieval_seen:
            retrieval_seen = True
            retrieval_start = now
            res.reached_retrieve = True
        applied = []
        for q, inj in enumerate(sched):
            if done[q]:
                continue
            fire = (retrieval_seen and (now - retrieval_start) >= inj.at_ms) if inj.rel_retrieval else now >= inj.at_ms
            if fire:
                done[q] = True
                rig.apply(inj)
                applied.append(inj.kind)
        if trace:
            res.trace.append(rig.trace_line(i, "tick", cur, applied))
    if rig.ctl.ev is not None:
        rig.ctl.request_disarm()
    if rig.ctl.events_logged == 0:
        rig.ctl.log_no_event_summary()
    if trace:
        res.trace.append(rig.trace_line(n, "end", cur, []))
    res.events = list(rig.ctl.records)
    res.final_state = rig.ctl.state
    res.faults = fault_list(rig.ctl.safety.latched)
    res.transitions = list(rig.ctl.transitions)
    dev = dict(device or DEFAULT_DEVICE)
    for j, ev in enumerate(res.events):
        res.records.append(_to_record(ev, scenario, seed, armed, cfg, dev, counter + j, start_utc, raw_dir,
                                      raw_ref_prefix, inline_samples))
    return res


def _test_outcome(scenario: str, result: str, faults: list[str], armed: bool) -> bool:
    ok = (result == "bite_candidate_confirmed") if scenario in POSITIVE_SCENARIOS else \
        (result != "bite_candidate_confirmed")
    if armed and scenario in EXPECTED_FAULT:
        ok = ok and any(f in faults for f in EXPECTED_FAULT[scenario])
    if armed and scenario not in EXPECTED_FAULT and faults:
        ok = False
    return ok


def _to_record(ev: _Event, scenario: str, seed: int, armed: bool, cfg: EngineConfig, dev: dict, counter: int,
               start_utc: datetime | None, raw_dir: str | None, raw_ref_prefix: str, inline_samples: bool) -> dict:
    h8 = hashlib.sha256(f"{dev['device_id']}:{scenario}:{seed}:{counter}".encode()).hexdigest()[:8]
    event_id = f"{dev['device_id']}-{counter:06d}-{h8}"
    epoch = start_utc or SIM_EPOCH
    faults = fault_list(ev.faults)
    sensor_data = {"sample_rate_hz": 50.0, "units": "N", "raw_data_ref": None, "raw_data_sha256": None}
    samples = [[float(t), x] for t, x in ev.samples]
    if inline_samples:
        sensor_data["samples"] = samples
    if ev.invalid_omitted:
        sensor_data["invalid_samples_omitted"] = ev.invalid_omitted
    if raw_dir is not None:
        from .canonical import file_sha256
        fname = f"{event_id}.csv"
        path = os.path.join(raw_dir, fname)
        write_raw_csv(path, samples)
        sensor_data["raw_data_ref"] = raw_ref_prefix + fname
        sensor_data["raw_data_sha256"] = file_sha256(path)
    cls = {"result": ev.result, "reason": ev.reason, "rule_trace": list(ev.rule_trace), "classifier": CLASSIFIER_ID,
           "implementation": IMPLEMENTATION["fw"], "thresholds": cfg.thresholds.to_record()}
    if ev.trigger_t is not None:
        cls["trigger_t_ms"] = float(ev.trigger_t)
    if ev.decision_t is not None:
        cls["decision_t_ms"] = float(ev.decision_t)
    feats = dict(ev.features)
    feats["oscillation_count"] = int(feats["oscillation_count"])
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "device": dev,
        "source": {"mode": "simulation", "scenario": scenario, "seed": seed,
                   "ground_truth_label": GROUND_TRUTH[scenario], "generator": GENERATOR_ID, "label": "SIMULATION"},
        "timestamp": {"start_utc": iso(epoch + timedelta(milliseconds=ev.start_ms)),
                      "end_utc": iso(epoch + timedelta(milliseconds=ev.end_ms)),
                      "monotonic_ms": int(ev.start_ms), "clock_quality": "host_clock_simulated"},
        "calibration": {"calibration_version": "sim-identity-v0", "scale_n_per_count": 1.0,
                        "offset_counts": 0.0, "calibrated_utc": None},
        "sensor_data": sensor_data,
        "features": feats,
        "classification": cls,
        "safety": {"decision": ev.decision, "armed": ev.armed, "interlocks": ev.interlocks, "faults": faults,
                   "fault_latched_t_ms": dict(ev.fault_latched_t_ms)},
        "actuation": {"requested": [dict(q) for q in ev.requested], "feedback": [dict(f) for f in ev.feedback],
                      "simulated": True},
        "outcome": {"final_state": ev.final_state, "stop_reason": ev.stop_reason,
                    "milestones": {"bite_detected": ev.bite_detected, "hook_set_completed": ev.hook_set_completed,
                                   "retrieval_completed": ev.retrieval_completed, "fish_landed": "unknown"},
                    "test_outcome": "pass" if _test_outcome(scenario, ev.result, faults, armed) else "fail",
                    "class_match": ev.result in EXPECTED_CLASSIFICATION[scenario]},
        "integrity": {"record_sha256": "", "hash_algorithm": "sha256", "prev_record_sha256": None, "signature": None},
    }


def build_events(scenario: str, seed: int, **kw) -> list[dict]:
    """All records of one run (multi-event runs: re-armed candidates, observation records, ...)."""
    return run_scenario(scenario, seed, **kw).records


def build_event(scenario: str, seed: int, *, armed: bool = True, counter: int = 1, device: dict | None = None,
                thresholds: Thresholds | None = None, limits: SafetyLimits | None = None,
                actuators: ActuatorParams | None = None, start_utc: datetime | None = None,
                raw_dir: str | None = None, raw_ref_prefix: str = "raw/", inline_samples: bool = True,
                **kw) -> dict:
    """The FIRST record of the run (the decision on the first candidate, a safety stop, the watchdog boot
    record or the end-of-window no_event summary). Use build_events() for every record of the run."""
    recs = run_scenario(scenario, seed, armed=armed, counter=counter, device=device, thresholds=thresholds,
                        limits=limits, actuators=actuators, start_utc=start_utc, raw_dir=raw_dir,
                        raw_ref_prefix=raw_ref_prefix, inline_samples=inline_samples, **kw).records
    return recs[0]


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
