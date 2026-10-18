"""deterministic-baseline-v1 — Python port of the ONE shared rule-based bite classifier.

Line-by-line port of firmware/src/core/{signal_processor,bite_detector}.cpp (ground truth).
apps/web-demo/src/engine/{processor,detector}.ts is a TypeScript port of the same firmware code
(aligned 2026-10-18); tests/integration and apps/web-demo/tests/cross_impl.test.ts check that all
three produce the same result, features and rule-trace text on hookpilot-synth-v1 scenarios.

The former web-demo variant (window = confirm_min_ms, manual 12 N, alpha 0.02, out-of-range > 50 N)
was retired on 2026-10-18 when the web demo adopted the firmware rules and defaults.
``Thresholds.variant`` stays in records ("fw") only for backward compatibility.

Every threshold is a CONFIGURABLE PLACEHOLDER:
"provisional – derive from calibration + labeled data". None is validated.

Convention of the Python engine (shared by the web demo): a sensor-health failure while in MONITOR
(no open candidate) yields ``rejected_sensor_fault`` for the window; the first decision in a window is
final. (The firmware controller records the same result via its SAFE_STOP path.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field, fields

CLASSIFIER_ID = "deterministic-baseline-v1"
PROVISIONAL_NOTE = "provisional – derive from calibration + labeled data"
DT_MS = 20.0
IMPLEMENTATION = {"fw": "python-port-of-firmware-cpp@2026-10-18"}

FW_DEFAULTS = dict(trigger_delta_n=1.0, confirm_min_ms=300.0, min_rate_n_per_s=2.0, max_osc=4,
                   manual_handling_delta_n=5.0, max_duration_ms=8000.0, decision_window_ms=600.0,
                   osc_band_n=0.25, baseline_alpha=0.002, smoothing_alpha=0.35, rate_lag_samples=5,
                   variance_window_samples=25, detrend_window_samples=5, release_ratio=0.5,
                   rearm_quiet_ms=300.0, sensor_min_n=-5.0, sensor_max_n=49.9, stuck_samples=15)
_INT_KEYS = ("max_osc", "rate_lag_samples", "variance_window_samples", "detrend_window_samples", "stuck_samples")


@dataclass
class Thresholds:
    variant: str = "fw"
    trigger_delta_n: float = 1.0
    confirm_min_ms: float = 300.0
    min_rate_n_per_s: float = 2.0
    max_osc: int = 4
    manual_handling_delta_n: float = 5.0
    max_duration_ms: float = 8000.0
    decision_window_ms: float | None = 600.0
    osc_band_n: float = 0.25
    baseline_alpha: float = 0.002
    smoothing_alpha: float = 0.35
    rate_lag_samples: int = 5
    variance_window_samples: int = 25
    detrend_window_samples: int = 5
    release_ratio: float = 0.5
    rearm_quiet_ms: float = 300.0
    sensor_min_n: float = -5.0
    sensor_max_n: float = 49.9
    stuck_samples: int = 15

    @classmethod
    def defaults(cls, variant: str = "fw") -> "Thresholds":
        if variant != "fw":
            raise ValueError(f"unknown classifier variant {variant!r} (only 'fw' exists since 2026-10-18)")
        return cls(variant="fw", **FW_DEFAULTS)

    def to_record(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["status"] = PROVISIONAL_NOTE
        return d

    @classmethod
    def from_record(cls, d: dict | None, variant: str | None = None) -> "Thresholds":
        d = dict(d or {})
        v = "fw"
        t = cls.defaults(v)
        names = {f.name for f in fields(cls)}
        for k, val in d.items():
            if k in names and k != "variant" and val is not None:
                setattr(t, k, val)
        for k in _INT_KEYS:
            setattr(t, k, int(getattr(t, k)))
        t.variant = v
        return t


def detect_variant(record: dict) -> str:
    """Only one rule set exists (firmware). Kept so replay output keeps its 'variant' key."""
    return "fw"


@dataclass
class Decision:
    result: str
    reason: str
    rule_trace: list[str]
    decision_t_ms: float | None
    trigger_t_ms: float | None
    features: dict = field(default_factory=dict)
    classifier: str = CLASSIFIER_ID
    variant: str = "fw"

    def classification_block(self, th: Thresholds) -> dict:
        return {"result": self.result, "reason": self.reason, "rule_trace": list(self.rule_trace),
                "classifier": self.classifier, "implementation": IMPLEMENTATION[self.variant],
                "thresholds": th.to_record()}


# --------------------------------------------------------------------------------------------
# Signal processors
# --------------------------------------------------------------------------------------------
class _Sample:
    __slots__ = ("t_ms", "raw", "valid", "health", "x", "smoothed", "baseline", "delta", "rate", "variance",
                 "detrended")


class FwProcessor:
    """Port of BaselineSignalProcessor (C++)."""
    K = 64

    def __init__(self, th: Thresholds) -> None:
        self.th = th
        self.vw = max(2, min(th.variance_window_samples, self.K))
        self.dw = max(1, min(th.detrend_window_samples, self.K))
        self.lag = max(1, min(th.rate_lag_samples, self.K - 1))
        self.init = False
        self.frozen = False
        self.x = self.sm = self.bl = 0.0
        self.xs: list[float] = []
        self.ss: list[float] = []
        self.last_raw = 0.0
        self.have_last = False
        self.identical = 0

    def _health(self, v: float) -> str:
        if not math.isfinite(v):
            self.have_last = False
            self.identical = 0
            return "disconnected"
        if v < self.th.sensor_min_n or v >= self.th.sensor_max_n:
            return "out_of_range"
        if self.have_last and v == self.last_raw:
            self.identical += 1
        else:
            self.identical = 1
        self.last_raw = v
        self.have_last = True
        if self.identical >= self.th.stuck_samples:
            return "stuck"
        return "ok"

    def process(self, t: float, v: float) -> _Sample:
        th = self.th
        s = _Sample()
        s.t_ms, s.raw = t, v
        s.health = self._health(v)
        s.valid = s.health in ("ok", "degraded")
        if s.valid:
            if not self.init:
                self.x = self.sm = self.bl = v
                self.init = True
            else:
                self.x = v
                self.sm += th.smoothing_alpha * (v - self.sm)
                if not self.frozen:
                    self.bl += th.baseline_alpha * (v - self.bl)
            self.xs.append(self.x)
            self.ss.append(self.sm)
            if len(self.xs) > self.K:
                del self.xs[0]
                del self.ss[0]
        s.x, s.smoothed, s.baseline = self.x, self.sm, self.bl
        s.delta = self.x - self.bl if self.init else 0.0
        n = len(self.xs)
        s.rate = (self.ss[-1] - self.ss[-1 - self.lag]) / (self.lag * (DT_MS / 1000.0)) if n > self.lag else 0.0
        vw = min(self.vw, n)
        s.variance = 0.0
        if vw >= 2:
            w = self.xs[-vw:]
            m = 0.0
            for a in w:
                m += a
            m /= vw
            acc = 0.0
            for a in w:
                acc += (a - m) * (a - m)
            s.variance = acc / vw
        dw = min(self.dw, n)
        s.detrended = 0.0
        if dw >= 1:
            m = 0.0
            for a in self.xs[-dw:]:
                m += a
            s.detrended = self.x - m / dw
        return s


# --------------------------------------------------------------------------------------------
# Rule evaluation (pure functions of features; usable for rule-consistency checks)
# --------------------------------------------------------------------------------------------
def decide_fw(f: dict, th: Thresholds, window_ms: float | None = None) -> tuple[str, str, list[str]]:
    tr: list[str] = []
    if window_ms is not None:
        tr.append(f"window: {window_ms:.0f} ms observed after trigger (decision_window_ms={th.decision_window_ms:.0f})")
    h = f.get("sensor_health", "ok")
    if h not in ("ok", "degraded"):
        tr.append(f"R0 sensor_health={h} != ok -> FAIL")
        return "rejected_sensor_fault", f"Sensor health '{h}' during candidate window; no decision can be trusted.", tr
    tr.append("R0 sensor_health=ok -> pass")
    pd, mh = f["peak_delta_n"], th.manual_handling_delta_n
    if pd > mh:
        tr.append(f"R1 peak_delta {pd:.3f} N > manual_handling_delta_n {mh:.3f} N -> FAIL")
        return ("rejected_manual_handling",
                f"Peak delta {pd:.3f} N exceeds manual-handling threshold {mh:.3f} N (line handled by a person).", tr)
    tr.append(f"R1 peak_delta {pd:.3f} N <= manual_handling_delta_n {mh:.3f} N -> pass")
    da = f["duration_above_ms"]
    if da > th.max_duration_ms:
        tr.append(f"R2 duration_above {da:.0f} ms > max_duration_ms {th.max_duration_ms:.0f} -> FAIL")
        return "rejected_manual_handling", "Above-trigger duration longer than max_duration_ms (treated as manual handling).", tr
    tr.append(f"R2 duration_above {da:.0f} ms <= max_duration_ms {th.max_duration_ms:.0f} -> pass")
    oc = f["oscillation_count"]
    if oc > th.max_osc:
        tr.append(f"R3 oscillation_count {oc:.0f} > max_osc {th.max_osc:.0f} -> FAIL")
        return ("rejected_vibration",
                f"Oscillation count {oc:.0f} exceeds max_osc {th.max_osc:.0f} (rod/wave vibration, low mean pull).", tr)
    tr.append(f"R3 oscillation_count {oc:.0f} <= max_osc {th.max_osc:.0f} -> pass")
    mr = f["max_rate_n_per_s"]
    if mr < th.min_rate_n_per_s:
        tr.append(f"R4 max_rate {mr:.3f} N/s < min_rate {th.min_rate_n_per_s:.3f} N/s -> FAIL")
        return ("rejected_noise",
                f"Sustained but slow rise (peak rate {mr:.3f} N/s < {th.min_rate_n_per_s:.3f} N/s): current/drift, not a bite.", tr)
    tr.append(f"R4 max_rate {mr:.3f} N/s >= min_rate {th.min_rate_n_per_s:.3f} N/s -> pass")
    ca = f.get("continuous_above_ms", da)
    if ca < th.confirm_min_ms:
        tr.append(f"R5 continuous_above {ca:.0f} ms < confirm_min_ms {th.confirm_min_ms:.0f} -> FAIL")
        return ("rejected_transient",
                f"Delta stayed above trigger for only {ca:.0f} ms (< {th.confirm_min_ms:.0f} ms): transient.", tr)
    tr.append(f"R5 continuous_above {ca:.0f} ms >= confirm_min_ms {th.confirm_min_ms:.0f} -> pass")
    tr.append("ALL rules pass -> bite_candidate_confirmed (thresholds provisional)")
    return ("bite_candidate_confirmed",
            f"Sustained pull ({ca:.0f} ms above trigger) with fast rise ({mr:.3f} N/s), low oscillation, healthy sensor.", tr)


def decide_from_features(features: dict, th: Thresholds) -> str:
    """Rule re-evaluation from recorded features only (used by the rule-consistency check).
    Note: for fw, R5 needs continuous_above_ms; when a record lacks it, duration_above_ms is used."""
    return decide_fw(features, th)[0]


# --------------------------------------------------------------------------------------------
# Streaming classifier (processor + detector)
# --------------------------------------------------------------------------------------------
class StreamingClassifier:
    def __init__(self, th: Thresholds | None = None, sample_rate_hz: float = 50.0) -> None:
        self.th = th or Thresholds.defaults("fw")
        if abs(sample_rate_hz - 50.0) > 1e-9:
            raise ValueError("deterministic-baseline-v1 ports assume 50 Hz (20 ms) samples")
        self.v = self.th.variant
        self.proc = FwProcessor(self.th)
        self.state = "MONITOR"
        self.decision: Decision | None = None
        self.trigger_trace: list[str] = []
        self.last: _Sample | None = None

    # candidate state
    def _begin(self, s: _Sample) -> None:
        th = self.th
        self.t_trig = self.t_last = s.t_ms
        self.f = {"baseline_n": s.baseline, "peak_delta_n": 0.0,
                  "max_rate_n_per_s": 0.0,
                  "duration_above_ms": 0.0, "continuous_above_ms": 0.0,
                  "short_term_variance": 0.0,
                  "oscillation_count": 0, "sensor_health": s.health}
        self.run_alive = True
        self.schmitt = 0
        self.trigger_trace = [f"t={s.t_ms:.0f}ms TRIGGER delta={s.delta:.3f}N >= trigger_delta_n={th.trigger_delta_n}N "
                              f"(baseline frozen at {s.baseline:.3f}N)"]
        self.proc.frozen = True
        self.state = "CANDIDATE"
        self._update_fw(s)  # beginCandidate() calls update(trigger); return value ignored

    def _update_fw(self, s: _Sample) -> bool:
        th, f = self.th, self.f
        if not s.valid:
            f["sensor_health"] = s.health
            return True
        f["peak_delta_n"] = max(f["peak_delta_n"], s.delta)
        f["max_rate_n_per_s"] = max(f["max_rate_n_per_s"], s.rate)
        f["short_term_variance"] = max(f["short_term_variance"], s.variance)
        above = s.delta >= th.trigger_delta_n
        if above:
            f["duration_above_ms"] += DT_MS
        if self.run_alive:
            if above:
                f["continuous_above_ms"] = (s.t_ms - self.t_trig) + DT_MS
            else:
                self.run_alive = False
        if s.detrended >= th.osc_band_n:
            if self.schmitt == -1:
                f["oscillation_count"] += 1
            self.schmitt = 1
        elif s.detrended <= -th.osc_band_n:
            if self.schmitt == 1:
                f["oscillation_count"] += 1
            self.schmitt = -1
        self.t_last = s.t_ms
        if s.delta > th.manual_handling_delta_n:
            return True
        return (s.t_ms - self.t_trig) + DT_MS >= th.decision_window_ms

    def _finalize(self, t: float) -> Decision:
        th = self.th
        feats = dict(self.f)
        res, reason, tr = decide_fw(feats, th, (self.t_last - self.t_trig) + DT_MS)
        self.decision = Decision(res, reason, self.trigger_trace + tr, t, self.t_trig, feats, variant=self.v)
        self.state = "DONE"
        return self.decision

    def push(self, t: float, x: float) -> Decision | None:
        if self.state == "DONE":
            return self.decision
        s = self.proc.process(t, x)
        self.last = s
        if self.state == "MONITOR":
            if s.valid and s.delta >= self.th.trigger_delta_n:
                self._begin(s)
                return None
            if not s.valid:
                feats = {"baseline_n": s.baseline, "peak_delta_n": 0.0, "max_rate_n_per_s": 0.0,
                         "duration_above_ms": 0.0, "continuous_above_ms": 0.0,
                         "short_term_variance": s.variance, "oscillation_count": 0, "sensor_health": s.health}
                tr = [f"t={t:.0f}ms sensor_health={s.health} while MONITOR (no candidate open) -> FAIL"]
                self.decision = Decision("rejected_sensor_fault",
                                         f"Sensor health '{s.health}' at t={t:.0f} ms while monitoring; measurements "
                                         "cannot be trusted, no actuation.", tr, t, None, feats, variant=self.v)
                self.state = "DONE"
                return self.decision
            return None
        done = self._update_fw(s)
        if done:
            return self._finalize(t)
        return None

    def finish(self, t_end: float) -> Decision:
        if self.decision is not None:
            return self.decision
        if self.state == "CANDIDATE":
            return self._finalize(t_end)
        s = self.last
        feats = {"baseline_n": s.baseline if s else 0.0, "peak_delta_n": 0.0, "max_rate_n_per_s": 0.0,
                 "duration_above_ms": 0.0, "continuous_above_ms": 0.0,
                 "short_term_variance": s.variance if s else 0.0, "oscillation_count": 0,
                 "sensor_health": s.health if s else "ok"}
        self.decision = Decision("no_event", f"delta never reached trigger_delta_n {self.th.trigger_delta_n} N in the window",
                                 [f"t={t_end:.0f}ms no candidate in window"], None, None, feats, variant=self.v)
        self.state = "DONE"
        return self.decision


def classify(samples, th: Thresholds | None = None, sample_rate_hz: float = 50.0) -> Decision:
    c = StreamingClassifier(th, sample_rate_hz)
    for t, x in samples:
        if c.push(float(t), float(x)) is not None:
            return c.decision
    return c.finish(float(samples[-1][0]) if samples else 0.0)
