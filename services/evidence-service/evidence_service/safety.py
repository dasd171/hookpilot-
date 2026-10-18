"""Independent safety controller + actuator gate (SIMULATION reference port).

Line-by-line port of firmware/src/core/safety_controller.cpp (DefaultSafetyController, ActuatorGate),
the ground truth. Limit names and defaults are firmware/include/hookpilot/config.h (SafetyLimits,
ActuatorParams, SimCoupling). The web demo (apps/web-demo/src/engine/safety.ts) is a port of the same
code; tests/integration/test_cross_impl.py and apps/web-demo/tests/cross_impl.test.ts check that all
three produce the same per-tick faults, latch ticks and permit/deny decisions (docs/engine-spec.md).

It is NOT the hardware safety implementation: on hardware the safety path is an independent MCU +
hard-wired e-stop chain (docs/safety.md). The classifier can only REQUEST an actuation; this controller
PERMITs or DENIEs. Every limit is a provisional placeholder (bench calibration required).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

PROVISIONAL = "provisional - derive from calibration + labeled data"

# Fault bits (firmware types.h order; the list order of every fault array follows it).
FAULT_NAMES = ("E_STOP", "OVER_TENSION", "SENSOR_DISCONNECTED", "SENSOR_OUT_OF_RANGE", "SENSOR_STUCK",
               "MOTOR_STALL", "ACTUATOR_TIMEOUT", "TRAVEL_LIMIT", "COMM_LOSS", "UNDERVOLTAGE", "WATCHDOG_RESET",
               "OVER_TEMP", "INTERLOCK")
F = {n: 1 << i for i, n in enumerate(FAULT_NAMES)}
CRITICAL_MASK = ~F["WATCHDOG_RESET"] & 0xFFFFFFFF


def fault_list(mask: int) -> list[str]:
    return [n for i, n in enumerate(FAULT_NAMES) if mask & (1 << i)]


def join_faults(mask: int, empty: str = "") -> str:
    s = ",".join(fault_list(mask))
    return s if s else empty


@dataclass
class SafetyLimits:
    retrieval_force_limit_n: float = 8.0   # while retrieving (or requesting retrieval)
    hard_max_tension_n: float = 20.0       # any state
    over_tension_samples: int = 2          # consecutive samples above a limit before fault
    sensor_min_n: float = -5.0
    sensor_max_n: float = 49.9             # >= this is saturation / out of range
    stuck_samples: int = 15
    sensor_fault_samples: int = 3          # consecutive invalid samples before a sensor fault
    hook_set_timeout_ms: int = 500
    retrieval_timeout_ms: int = 4000
    hook_set_travel_min_mm: float = 0.0
    hook_set_travel_limit_mm: float = 15.0
    hook_set_max_speed_mm_s: float = 80.0
    retrieval_travel_limit_m: float = 1.5
    retrieval_max_speed_m_s: float = 0.6
    stall_current_a: float = 1.8
    stall_window_ms: int = 200
    stall_min_progress_m: float = 0.005
    stall_grace_ms: int = 200
    comm_timeout_ms: int = 300
    hook_to_retrieval_gap_ms: int = 100    # minimum dead time between hook-set stop and retrieval start
    undervoltage_v: float = 10.5           # 3S Li-ion, ~3.5 V/cell
    undervoltage_rearm_v: float = 10.8
    undervoltage_debounce_ms: int = 200
    over_temp_c: float = 70.0

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
    hook_set_position_tol_mm: float = 0.5
    retrieval_target_m: float = 1.0
    retrieval_speed_m_s: float = 0.5
    verify_ms: int = 500


@dataclass
class SimCoupling:
    """SIMULATION-only coupling of the host rig (firmware config.h SimCoupling). Not a physical model."""
    hook_set_line_load_n: float = 1.5
    retrieval_line_load_n: float = 0.6
    retrieval_current_base_a: float = 0.3
    retrieval_current_per_n_a: float = 0.1


# Names accepted by apply_overrides / `--set` (identical in firmware sim_main.cpp and the TS engine).
_INT_PARAMS = {"confirm_min_ms", "max_osc", "max_duration_ms", "decision_window_ms", "rearm_quiet_ms",
               "over_tension_samples", "sensor_fault_samples", "hook_set_timeout_ms", "retrieval_timeout_ms",
               "stall_window_ms", "stall_grace_ms", "comm_timeout_ms", "hook_to_retrieval_gap_ms",
               "undervoltage_debounce_ms", "verify_ms"}


def dataclass_keys(dc) -> set[str]:
    return {f.name for f in fields(dc)}


class SafetyDecision:
    __slots__ = ("permitted", "reason")

    def __init__(self, permitted: bool = False, reason: str = "") -> None:
        self.permitted = permitted
        self.reason = reason


class SafetyController:
    """Port of DefaultSafetyController. Inputs are a SafetyInputs-like object (engine.SafetyInputs)."""
    K_HIST = 64

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self.lim = limits or SafetyLimits()
        self.armed = False
        self.latched = 0
        self.active = 0
        self.sensor_bad_count = 0
        self.last_bad_health = "ok"
        self.over_tension_count = 0
        self.uv_pending = False
        self.uv_since = 0
        self.hook_was_active = False
        self.retr_was_active = False
        self.hook_since = 0
        self.retr_since = 0
        self.hook_ran = False
        self.hook_idle_since = 0
        self.last_now = 0
        self.latch_t = [0] * len(FAULT_NAMES)
        self.hist_t = [0] * self.K_HIST
        self.hist_p = [0.0] * self.K_HIST
        self.hist_n = 0
        self.hist_head = 0

    # ------------------------------------------------------------------ evaluation
    def _evaluate(self, i) -> int:
        L = self.lim
        c = 0
        s = i.sample
        if i.estop_active:
            c |= F["E_STOP"]
        if not s.valid:
            self.sensor_bad_count += 1
            self.last_bad_health = s.health
        else:
            self.sensor_bad_count = 0
        if self.sensor_bad_count >= L.sensor_fault_samples:
            if self.last_bad_health == "out_of_range":
                c |= F["SENSOR_OUT_OF_RANGE"]
            elif self.last_bad_health == "stuck":
                c |= F["SENSOR_STUCK"]
            else:
                c |= F["SENSOR_DISCONNECTED"]
        over = False
        if s.valid:
            if s.raw > L.hard_max_tension_n:
                over = True
            if i.retrieval.active and s.raw > L.retrieval_force_limit_n:
                over = True
        self.over_tension_count = self.over_tension_count + 1 if over else 0
        if self.over_tension_count >= L.over_tension_samples:
            c |= F["OVER_TENSION"]
        now = i.now_ms
        if i.hook.active:
            if not self.hook_was_active:
                self.hook_since = now
            if now - self.hook_since > L.hook_set_timeout_ms:
                c |= F["ACTUATOR_TIMEOUT"]
        elif self.hook_was_active:
            self.hook_ran = True
            self.hook_idle_since = now
        self.hook_was_active = i.hook.active
        if i.retrieval.active:
            if not self.retr_was_active:
                self.retr_since = now
                self.hist_n = self.hist_head = 0
            if now - self.retr_since > L.retrieval_timeout_ms:
                c |= F["ACTUATOR_TIMEOUT"]
        self.retr_was_active = i.retrieval.active
        if i.hook.position_valid and (i.hook.position > L.hook_set_travel_limit_mm
                                      or i.hook.position < L.hook_set_travel_min_mm - 1.0):
            c |= F["TRAVEL_LIMIT"]
        if i.retrieval.position_valid and i.retrieval.position > L.retrieval_travel_limit_m:
            c |= F["TRAVEL_LIMIT"]
        if i.retrieval.active:
            K = self.K_HIST
            self.hist_t[self.hist_head] = now
            self.hist_p[self.hist_head] = i.retrieval.position
            self.hist_head = (self.hist_head + 1) % K
            if self.hist_n < K:
                self.hist_n += 1
            if now - self.retr_since >= L.stall_grace_ms:
                progress = -1.0
                progress_long = -1.0
                for k in range(self.hist_n):
                    idx = (self.hist_head - 1 - k + K) % K
                    age = now - self.hist_t[idx]
                    if age >= L.stall_window_ms and progress < 0:
                        progress = i.retrieval.position - self.hist_p[idx]
                    if age >= 3 * L.stall_window_ms and progress_long < 0:
                        progress_long = i.retrieval.position - self.hist_p[idx]
                high_current = (not i.retrieval.current_valid) or i.retrieval.current_a >= L.stall_current_a
                if progress >= 0 and progress < L.stall_min_progress_m and high_current:
                    c |= F["MOTOR_STALL"]
                if progress_long >= 0 and progress_long < L.stall_min_progress_m:
                    c |= F["MOTOR_STALL"]
        if i.hook.active and i.retrieval.active:
            c |= F["INTERLOCK"]
        if (not i.comm_link_ok) or now - i.last_heartbeat_ms > L.comm_timeout_ms:
            c |= F["COMM_LOSS"]
        if i.bus_voltage_v < L.undervoltage_v:
            if not self.uv_pending:
                self.uv_pending = True
                self.uv_since = now
            if now - self.uv_since >= L.undervoltage_debounce_ms:
                c |= F["UNDERVOLTAGE"]
        else:
            self.uv_pending = False
        if i.temperature_c > L.over_temp_c:
            c |= F["OVER_TEMP"]
        return c

    def _stamp(self, newly: int, now: int) -> None:
        for b in range(len(FAULT_NAMES)):
            if newly & (1 << b):
                self.latch_t[b] = now

    def latch_time_ms(self, name: str) -> int:
        return self.latch_t[FAULT_NAMES.index(name)]

    def update(self, i) -> int:
        self.last_now = i.now_ms
        self.active = self._evaluate(i)
        latch = self.active
        if not self.armed:
            latch &= ~F["COMM_LOSS"]  # comm loss is only critical while armed (documented)
        self._stamp(latch & ~self.latched, i.now_ms)
        self.latched |= latch
        if self.latched:
            self.armed = False
        return self.latched

    # ------------------------------------------------------------------ permit / arm / reset
    def permit(self, actuator: str, target: float, speed: float, i) -> SafetyDecision:
        L = self.lim

        def deny(why: str) -> SafetyDecision:
            return SafetyDecision(False, why)

        if not self.armed:
            return deny("deny: not armed")
        if self.latched:
            return deny("deny: latched faults " + join_faults(self.latched))
        if self.active:
            return deny("deny: active conditions " + join_faults(self.active))
        if not i.sample.valid:
            return deny(f"deny: sensor not valid ({i.sample.health})")
        if i.sample.raw > L.hard_max_tension_n:
            return deny("deny: tension above hard_max_tension_n")
        if actuator == "hook_set":
            if i.retrieval.active:
                return deny("deny: mutual exclusion (retrieval active)")
            if not (L.hook_set_travel_min_mm <= target <= L.hook_set_travel_limit_mm):
                return deny(f"deny: hook_set target {target:.3f} mm outside travel limits")
            if not (0 < speed <= L.hook_set_max_speed_mm_s):
                return deny(f"deny: hook_set speed {speed:.3f} mm/s outside limits")
        else:
            if i.hook.active:
                return deny("deny: mutual exclusion (hook_set active)")
            if self.hook_ran and i.now_ms - self.hook_idle_since < L.hook_to_retrieval_gap_ms:
                return deny(f"deny: hook_set stopped {i.now_ms - self.hook_idle_since} ms ago "
                            "(< hook_to_retrieval_gap_ms)")
            if not (0 < target <= L.retrieval_travel_limit_m):
                return deny(f"deny: retrieval target {target:.3f} m outside travel limits")
            if not (0 < speed <= L.retrieval_max_speed_m_s):
                return deny(f"deny: retrieval speed {speed:.3f} m/s outside limits")
            if i.sample.raw > L.retrieval_force_limit_n:
                return deny(f"deny: tension {i.sample.raw:.3f} N above retrieval_force_limit_n")
        return SafetyDecision(True, "permit: all interlocks clear")

    def arm(self, i) -> tuple[bool, str]:
        if self.latched:
            return False, "latched faults " + join_faults(self.latched) + " (explicit reset required)"
        if self.active:
            return False, "active conditions " + join_faults(self.active)
        if not i.sample.valid:
            return False, "sensor not valid"
        if i.bus_voltage_v < self.lim.undervoltage_rearm_v:
            return False, "bus voltage below undervoltage_rearm_v"
        if i.estop_active:
            return False, "e-stop active"
        if i.hook.active or i.retrieval.active:
            return False, "actuator active"
        self.armed = True
        return True, "armed"

    def disarm(self) -> None:
        self.armed = False

    def clear_faults(self) -> bool:
        # WATCHDOG_RESET is acknowledgement-only; everything else must be physically gone.
        self.latched &= self.active & ~F["WATCHDOG_RESET"]
        return self.latched == 0

    def latch_fault(self, f: int) -> None:
        self._stamp(f & ~self.latched, self.last_now)
        self.latched |= f
        self.armed = False

    @property
    def faults(self) -> list[str]:
        return fault_list(self.latched)


class ActuatorGate:
    """Port of ActuatorGate: the only path from the controller to the actuators."""

    def __init__(self, safety: SafetyController, hook, retrieval) -> None:
        self.safety = safety
        self.hook = hook
        self.retr = retrieval
        self.issued = 0
        self.denied = 0

    def request(self, actuator: str, target: float, speed: float, i) -> SafetyDecision:
        d = self.safety.permit(actuator, target, speed, i)
        if d.permitted:
            if actuator == "hook_set" and self.retr.fb.active:
                d = SafetyDecision(False, "deny: gate mutual exclusion (retrieval active)")
            elif actuator == "retrieval" and self.hook.fb.active:
                d = SafetyDecision(False, "deny: gate mutual exclusion (hook_set active)")
        if not d.permitted:
            self.denied += 1
            return d
        self.issued += 1
        if actuator == "hook_set":
            self.hook.move_to(target, speed)
        else:
            self.retr.start(target, speed)
        return d

    def stop_all(self) -> None:
        self.hook.stop()
        self.retr.stop()

    def update(self, now: int) -> None:
        self.hook.update(now)
        self.retr.update(now)
