"""deterministic-baseline-v1 — Python ports of the shared rule-based bite classifier.

Two implementation variants exist in this monorepo (both named deterministic-baseline-v1):

* ``fw``  — line-by-line port of firmware/src/core/{signal_processor,bite_detector}.cpp
            (snapshot read 2026-10-18). DEFAULT: device evidence comes from firmware, and the
            Python reference engine uses this variant.
* ``web`` — port of apps/web-demo/src/engine/{processor,detector}.ts (snapshot 2026-10-18).

They differ in decision-window semantics (fw: decision_window_ms; web: confirm_min_ms),
rule order (both now check rate before transient; firmware changed this on 2026-10-18),
Schmitt comparison (fw >=/<=, web >/<), out-of-range bound (fw >= max, web > max),
initial rate lag and defaults. Replay picks the variant from the record
(classification.implementation / thresholds keys) — see ``detect_variant``.

Every threshold is a CONFIGURABLE PLACEHOLDER:
"provisional – derive from calibration + labeled data". None is validated.

Convention of the Python engine (controller code not yet published by firmware):
a sensor-health failure while in MONITOR (no open candidate) yields
``rejected_sensor_fault`` for the window; the first decision in a window is final.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field, fields

CLASSIFIER_ID = "deterministic-baseline-v1"
PROVISIONAL_NOTE = "provisional – derive from calibration + labeled data"
DT_MS = 20.0
IMPLEMENTATION = {"fw": "python-port-of-firmware-cpp@2026-10-18", "web": "python-port-of-web-demo-ts@2026-10-18"}

FW_DEFAULTS = dict(trigger_delta_n=1.0, confirm_min_ms=300.0, min_rate_n_per_s=2.0, max_osc=4,
                   manual_handling_delta_n=5.0, max_duration_ms=8000.0, decision_window_ms=600.0,
                   osc_band_n=0.25, baseline_alpha=0.002, smoothing_alpha=0.35, rate_lag_samples=5,
                   variance_window_samples=25, detrend_window_samples=5, release_ratio=0.5,
                   rearm_quiet_ms=300.0, sensor_min_n=-5.0, sensor_max_n=49.9, stuck_samples=15)
WEB_DEFAULTS = dict(trigger_delta_n=1.0, confirm_min_ms=300.0, min_rate_n_per_s=5.0, max_osc=4,
                    manual_handling_delta_n=12.0, max_duration_ms=8000.0, decision_window_ms=None,
                    osc_band_n=0.25, baseline_alpha=0.02, smoothing_alpha=0.35, rate_lag_samples=5,
                    variance_window_samples=25, detrend_window_samples=5, release_ratio=0.5,
                    rearm_quiet_ms=300.0, sensor_min_n=-5.0, sensor_max_n=50.0, stuck_samples=15)
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
        d = FW_DEFAULTS if variant == "fw" else WEB_DEFAULTS
        return cls(variant=variant, **d)

    def to_record(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["status"] = PROVISIONAL_NOTE
        return d

    @classmethod
    def from_record(cls, d: dict | None, variant: str | None = None) -> "Thresholds":
        d = dict(d or {})
        v = variant or d.get("variant") or ("fw" if "decision_window_ms" in d else ("web" if d else "fw"))
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
    cls = record.get("classification") or {}
    impl = str(cls.get("implementation", ""))
    if "web" in impl:
        return "web"
    if "firmware" in impl:
        return "fw"
    th = cls.get("thresholds") or {}
    if th.get("variant") in ("fw", "web"):
        return th["variant"]
    return "fw" if "decision_window_ms" in th else ("web" if th else "fw")


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


class WebProcessor:
    """Port of SignalProcessor (TypeScript, web demo)."""

    def __init__(self, th: Thresholds) -> None:
        self.th = th
        self.init = False
        self.frozen = False
        self.bl = self.sm = 0.0
        self.sh: list[float] = []
        self.win: list[float] = []
        self.dwin: list[float] = []
        self.last_raw = float("nan")
        self.run = 0
        self.last: _Sample | None = None

    def process(self, t: float, v: float) -> _Sample:
        th = self.th
        health = "ok"
        if not math.isfinite(v):
            health = "disconnected"
            self.run = 0
        else:
            self.run = self.run + 1 if v == self.last_raw else 1
            if v < th.sensor_min_n or v > th.sensor_max_n:
                health = "out_of_range"
            elif self.run >= th.stuck_samples:
                health = "stuck"
        self.last_raw = v
        if health != "ok" or not self.init:
            if health == "ok" and not self.init:
                self.init = True
                self.bl = self.sm = v
            elif health != "ok":
                p = self.last
                s = _Sample()
                s.t_ms, s.raw, s.valid, s.health = t, v, False, health
                s.x = p.x if p else 0.0
                s.smoothed = p.smoothed if p else 0.0
                s.baseline = p.baseline if p else 0.0
                s.delta = p.delta if p else 0.0
                s.rate = 0.0
                s.variance = p.variance if p else 0.0
                s.detrended = 0.0
                self.last = s
                return s
        if not self.frozen:
            self.bl += th.baseline_alpha * (v - self.bl)
        self.sm += th.smoothing_alpha * (v - self.sm)
        self.sh.append(self.sm)
        if len(self.sh) > th.rate_lag_samples + 1:
            del self.sh[0]
        lag_n = len(self.sh) - 1
        rate = ((self.sm - self.sh[0]) / (lag_n * DT_MS)) * 1000.0 if lag_n > 0 else 0.0
        self.win.append(v)
        if len(self.win) > th.variance_window_samples:
            del self.win[0]
        m = 0.0
        for a in self.win:
            m += a
        m /= len(self.win)
        var = 0.0
        for a in self.win:
            var += (a - m) * (a - m)
        var /= len(self.win)
        self.dwin.append(v)
        if len(self.dwin) > th.detrend_window_samples:
            del self.dwin[0]
        dm = 0.0
        for a in self.dwin:
            dm += a
        dm /= len(self.dwin)
        s = _Sample()
        s.t_ms, s.raw, s.valid, s.health = t, v, True, health
        s.x, s.smoothed, s.baseline = v, self.sm, self.bl
        s.delta, s.rate, s.variance, s.detrended = v - self.bl, rate, var, v - dm
        self.last = s
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


def decide_web(f: dict, th: Thresholds) -> tuple[str, str, list[str]]:
    tr = [f"TRIGGER delta >= {th.trigger_delta_n:.2f} N; baseline frozen at {f.get('baseline_n', 0.0):.2f} N; window {th.confirm_min_ms:g} ms"]
    h = f.get("sensor_health", "ok")
    if h != "ok":
        tr += [f"R1 sensor_health={h} != ok -> FAIL", "=> rejected_sensor_fault"]
        return "rejected_sensor_fault", f'Sensor health is "{h}"; readings cannot be trusted, no actuation.', tr
    tr.append("R1 sensor_health=ok -> pass")
    if f["peak_delta_n"] > th.manual_handling_delta_n:
        tr += ["R2 -> FAIL", "=> rejected_manual_handling"]
        return "rejected_manual_handling", "Peak delta exceeds manual-handling limit.", tr
    if f["duration_above_ms"] > th.max_duration_ms:
        tr += ["R3 -> FAIL", "=> rejected_manual_handling"]
        return "rejected_manual_handling", "Above-trigger duration exceeds max_duration_ms.", tr
    if f["oscillation_count"] > th.max_osc:
        tr += ["R4 -> FAIL", "=> rejected_vibration"]
        return "rejected_vibration", "Oscillation count exceeds max_osc.", tr
    if f["max_rate_n_per_s"] < th.min_rate_n_per_s:
        tr += ["R5 -> FAIL", "=> rejected_noise"]
        return "rejected_noise", "Slow rise: drift from current/drag, not a strike.", tr
    if f["duration_above_ms"] < th.confirm_min_ms:
        tr += ["R6 -> FAIL", "=> rejected_transient"]
        return "rejected_transient", "Delta fell back below trigger: short transient.", tr
    tr += ["R1-R6 pass", "=> bite_candidate_confirmed"]
    return "bite_candidate_confirmed", "Sustained pull confirmed by provisional rules (not validated).", tr


def decide_from_features(features: dict, th: Thresholds) -> str:
    """Rule re-evaluation from recorded features only (used by the rule-consistency check).
    Note: for fw, R5 needs continuous_above_ms; when a record lacks it, duration_above_ms is used."""
    return (decide_fw(features, th) if th.variant == "fw" else decide_web(features, th))[0]


# --------------------------------------------------------------------------------------------
# Streaming classifier (processor + detector)
# --------------------------------------------------------------------------------------------
_RANK = {"ok": 0, "degraded": 1, "stuck": 2, "out_of_range": 3, "disconnected": 4}


class StreamingClassifier:
    def __init__(self, th: Thresholds | None = None, sample_rate_hz: float = 50.0) -> None:
        self.th = th or Thresholds.defaults("fw")
        if abs(sample_rate_hz - 50.0) > 1e-9:
            raise ValueError("deterministic-baseline-v1 ports assume 50 Hz (20 ms) samples")
        self.v = self.th.variant
        self.proc = FwProcessor(self.th) if self.v == "fw" else WebProcessor(self.th)
        self.state = "MONITOR"
        self.decision: Decision | None = None
        self.trigger_trace: list[str] = []
        self.last: _Sample | None = None

    # candidate state
    def _begin(self, s: _Sample) -> None:
        th = self.th
        self.t_trig = self.t_last = s.t_ms
        self.f = {"baseline_n": s.baseline, "peak_delta_n": 0.0 if self.v == "fw" else s.delta,
                  "max_rate_n_per_s": 0.0 if self.v == "fw" else max(0.0, s.rate),
                  "duration_above_ms": 0.0, "continuous_above_ms": 0.0,
                  "short_term_variance": 0.0 if self.v == "fw" else s.variance,
                  "oscillation_count": 0, "sensor_health": s.health}
        self.run_alive = True
        self.schmitt = 0
        self.last_above = s.t_ms
        self.trigger_trace = [f"t={s.t_ms:.0f}ms TRIGGER delta={s.delta:.3f}N >= trigger_delta_n={th.trigger_delta_n}N "
                              f"(baseline frozen at {s.baseline:.3f}N)"]
        self.proc.frozen = True
        self.state = "CANDIDATE"
        if self.v == "fw":
            self._update_fw(s)  # beginCandidate() calls update(trigger); return value ignored
        else:
            self._osc_web(s.detrended)

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

    def _osc_web(self, d: float) -> None:
        band = self.th.osc_band_n
        sg = 1 if d > band else (-1 if d < -band else 0)
        if sg != 0:
            if self.schmitt != 0 and sg != self.schmitt:
                self.f["oscillation_count"] += 1
            self.schmitt = sg

    def _update_web(self, s: _Sample) -> bool:
        th, f = self.th, self.f
        if _RANK.get(s.health, 0) > _RANK.get(f["sensor_health"], 0):
            f["sensor_health"] = s.health
        if not s.valid:
            return True
        f["peak_delta_n"] = max(f["peak_delta_n"], s.delta)
        f["max_rate_n_per_s"] = max(f["max_rate_n_per_s"], s.rate)
        f["short_term_variance"] = max(f["short_term_variance"], s.variance)
        if s.delta >= th.trigger_delta_n:
            if self.run_alive:
                self.last_above = s.t_ms
        else:
            self.run_alive = False
        self._osc_web(s.detrended)
        self.t_last = s.t_ms
        if f["peak_delta_n"] > th.manual_handling_delta_n:
            return True
        return s.t_ms - self.t_trig + DT_MS >= th.confirm_min_ms

    def _finalize(self, t: float) -> Decision:
        th = self.th
        if self.v == "fw":
            feats = dict(self.f)
            res, reason, tr = decide_fw(feats, th, (self.t_last - self.t_trig) + DT_MS)
        else:
            self.f["duration_above_ms"] = self.last_above - self.t_trig + DT_MS
            self.f["continuous_above_ms"] = self.f["duration_above_ms"]
            feats = dict(self.f)
            res, reason, tr = decide_web(feats, th)
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
        done = self._update_fw(s) if self.v == "fw" else self._update_web(s)
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
