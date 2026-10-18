"""Synthetic line-tension signal generator "hookpilot-synth-v1" (SIMULATION).

Reproducible from (scenario, seed) with mulberry32. The exact draw order is the
specification; see docs/synthetic-signals.md. Any port (TS / C++) that follows
that document yields the same samples up to libm last-ulp differences in
log/cos/sin (replay compares with a tolerance for this reason).

Units: time ms, tension N. Default 50 Hz, 8000 ms window (400 samples).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .prng import Mulberry32

GENERATOR_ID = "hookpilot-synth-v1"

SCENARIOS = (
    "idle",
    "bite",
    "noise_current",
    "rod_vibration",
    "manual_handling",
    "sensor_failure",
    "excessive_tension",
    "motor_stall",
    "comm_loss",
)

# Ground-truth label recorded in source.ground_truth_label for each scenario.
GROUND_TRUTH = {
    "idle": "no_bite",
    "bite": "bite",
    "noise_current": "no_bite_current_drift",
    "rod_vibration": "no_bite_vibration",
    "manual_handling": "no_bite_manual_handling",
    "sensor_failure": "sensor_fault",
    "excessive_tension": "bite_then_over_tension",
    "motor_stall": "bite_then_motor_stall",
    "comm_loss": "bite_then_comm_loss",
}

# Acceptable deterministic-baseline-v1 classifications per scenario (for scoring).
# A slow drift fully absorbed by the EMA baseline (no_event) is as correct as an
# explicit rejected_noise; both mean "no actuation". (firmware/src/core/scenarios.cpp
# lists only the first entry of each tuple.)
EXPECTED_CLASSIFICATION = {
    "idle": ("no_event",),
    "bite": ("bite_candidate_confirmed",),
    "noise_current": ("rejected_noise", "no_event"),
    "rod_vibration": ("rejected_vibration",),
    "manual_handling": ("rejected_manual_handling",),
    "sensor_failure": ("rejected_sensor_fault",),
    "excessive_tension": ("bite_candidate_confirmed",),
    "motor_stall": ("bite_candidate_confirmed",),
    "comm_loss": ("bite_candidate_confirmed",),
}
POSITIVE_SCENARIOS = ("bite", "excessive_tension", "motor_stall", "comm_loss")
NEGATIVE_SCENARIOS = ("idle", "noise_current", "rod_vibration", "manual_handling", "sensor_failure")

# Expected safety fault (if any) when the cycle runs armed.
EXPECTED_FAULT = {
    "sensor_failure": ("SENSOR_STUCK", "SENSOR_OUT_OF_RANGE", "SENSOR_DISCONNECTED"),
    "excessive_tension": ("OVER_TENSION",),
    "motor_stall": ("MOTOR_STALL",),
    "comm_loss": ("COMM_LOSS",),
}

NOISE_SIGMA_N = 0.02
SENSOR_SATURATION_N = 49.99


@dataclass
class SynthSignal:
    scenario: str
    seed: int
    sample_rate_hz: float
    duration_ms: int
    t_ms: list[float]
    tension_n: list[float]
    params: dict = field(default_factory=dict)  # drawn parameters (ground truth)

    def samples(self) -> list[list[float]]:
        return [[t, x] for t, x in zip(self.t_ms, self.tension_n)]


def _snap(ms: float, dt: float) -> float:
    return dt * math.floor(ms / dt)


def _bite_shape(tau: float, p: dict) -> float:
    A, rise, hold, fall = p["amp_n"], p["rise_ms"], p["hold_ms"], p["fall_ms"]
    if tau < 0:
        return 0.0
    if tau < rise:
        return A * tau / rise
    if tau < rise + hold:
        return A + p["shake_amp_n"] * math.sin(
            2.0 * math.pi * p["shake_hz"] * (tau - rise) / 1000.0 + p["shake_phase"]
        )
    if tau < rise + hold + fall:
        return A * (1.0 - (tau - rise - hold) / fall)
    return 0.0


def generate(scenario: str, seed: int, sample_rate_hz: float = 50.0, duration_ms: int = 8000) -> SynthSignal:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; known: {SCENARIOS}")
    rng = Mulberry32(seed)
    dt = 1000.0 / sample_rate_hz
    n = int(round(duration_ms / dt))
    t = [i * dt for i in range(n)]

    # 1) common draws
    base = rng.uniform(0.35, 0.50)
    onset = _snap(rng.uniform(1500.0, 3000.0), dt)
    p: dict = {"base_n": base, "onset_ms": onset}

    # 2) scenario draws (order is normative)
    if scenario == "bite":
        p.update(amp_n=rng.uniform(0.7, 2.6), rise_ms=rng.uniform(60, 200), hold_ms=rng.uniform(150, 1500),
                 shake_hz=rng.uniform(1.5, 3.0), shake_amp_n=rng.uniform(0.03, 0.10),
                 shake_phase=rng.uniform(0, 2 * math.pi), fall_ms=rng.uniform(150, 400))
    elif scenario == "noise_current":
        p.update(amp_n=rng.uniform(0.9, 1.7), ramp_ms=rng.uniform(2500, 4000))
    elif scenario == "rod_vibration":
        p.update(freq_hz=rng.uniform(6, 12), amp_n=rng.uniform(0.9, 1.5), dur_ms=rng.uniform(1000, 2000))
    elif scenario == "manual_handling":
        p.update(amp_n=rng.uniform(6.5, 12.0), rise_ms=rng.uniform(300, 800), hold_ms=rng.uniform(1000, 3000),
                 fall_ms=300.0)
    elif scenario == "sensor_failure":
        p.update(mode="stuck" if rng.next() < 0.5 else "out_of_range", fault_dur_ms=rng.uniform(200, 1000))
    elif scenario == "excessive_tension":
        p.update(amp_n=rng.uniform(1.2, 2.2), rise_ms=rng.uniform(60, 200), pre_ms=rng.uniform(600, 1000),
                 over_rate_n_per_s=rng.uniform(8, 16), cap_n=15.0)
    elif scenario in ("motor_stall", "comm_loss"):
        p.update(amp_n=rng.uniform(1.2, 2.2), rise_ms=rng.uniform(60, 200), hold_ms=rng.uniform(2000, 3000),
                 shake_hz=rng.uniform(1.5, 3.0), shake_amp_n=rng.uniform(0.03, 0.10),
                 shake_phase=rng.uniform(0, 2 * math.pi), fall_ms=300.0,
                 fault_after_retrieval_start_ms=rng.uniform(200, 800))

    # 3) noise: one gauss() (two draws) per sample, always for every sample
    noise = [NOISE_SIGMA_N * rng.gauss() for _ in range(n)]

    # 4) compose
    x = []
    for i in range(n):
        tau = t[i] - onset
        s = 0.0
        if scenario in ("bite", "motor_stall", "comm_loss"):
            s = _bite_shape(tau, p)
        elif scenario == "noise_current":
            s = 0.0 if tau < 0 else (p["amp_n"] * tau / p["ramp_ms"] if tau < p["ramp_ms"] else p["amp_n"])
        elif scenario == "rod_vibration":
            if 0 <= tau < p["dur_ms"]:
                env = min(1.0, tau / 100.0, (p["dur_ms"] - tau) / 100.0)
                s = p["amp_n"] * env * math.sin(2 * math.pi * p["freq_hz"] * tau / 1000.0)
        elif scenario == "manual_handling":
            A, r, h, f = p["amp_n"], p["rise_ms"], p["hold_ms"], p["fall_ms"]
            if 0 <= tau < r:
                s = A * tau / r
            elif r <= tau < r + h:
                s = A
            elif r + h <= tau < r + h + f:
                s = A * (1 - (tau - r - h) / f)
        elif scenario == "excessive_tension":
            A, r, pre = p["amp_n"], p["rise_ms"], p["pre_ms"]
            if 0 <= tau < r:
                s = A * tau / r
            elif r <= tau < r + pre:
                s = A
            elif tau >= r + pre:
                s = min(p["cap_n"], A + p["over_rate_n_per_s"] * (tau - r - pre) / 1000.0)
        x.append(base + s + noise[i])

    # 5) sensor fault post-processing
    if scenario == "sensor_failure":
        i0 = int(round(onset / dt))
        if p["mode"] == "stuck":
            frozen = x[i0]
            for i in range(i0, n):
                x[i] = frozen
        else:
            i1 = min(n, i0 + int(round(p["fault_dur_ms"] / dt)))
            for i in range(i0, i1):
                x[i] = SENSOR_SATURATION_N

    return SynthSignal(scenario, seed, sample_rate_hz, duration_ms, t, x, p)
