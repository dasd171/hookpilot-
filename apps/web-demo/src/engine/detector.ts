/**
 * BiteDetector "deterministic-baseline-v1" (see docs/SHARED_CONTRACT.md).
 * Thresholds are configurable PLACEHOLDERS (provisional - derive from calibration + labeled data).
 *
 * TypeScript port of firmware DeterministicBaselineV1 (firmware/src/core/bite_detector.cpp), identical
 * to the Python port (evidence_service.classifier). One rule set for all three implementations.
 *
 * Trigger: valid sample with delta >= trigger_delta_n opens a candidate (the caller freezes the baseline).
 * The candidate is observed for decision_window_ms from the trigger sample (early exit on an invalid
 * sample or on delta > manual_handling_delta_n), then rules are evaluated in this fixed order; the first
 * failing rule decides:
 *   R0 sensor_health ok/degraded                          else rejected_sensor_fault
 *   R1 peak_delta <= manual_handling_delta_n              else rejected_manual_handling
 *   R2 duration_above <= max_duration_ms                  else rejected_manual_handling
 *   R3 oscillation_count <= max_osc                       else rejected_vibration
 *   R4 max_rate >= min_rate_n_per_s                       else rejected_noise (slow drift / current)
 *   R5 continuous_above >= confirm_min_ms                 else rejected_transient
 *   all pass                                              -> bite_candidate_confirmed
 * Rule-trace text is byte-identical to the C++ / Python output (printf-style number formatting).
 */
import type { ClassificationResult, SensorHealth } from '../../../../packages/shared-types/src/index';
import type { ClassifierThresholds } from './config';
import { DT_MS } from './config';
import { cfix, pyRepr } from './fmt';
import type { SampleFeatures } from './processor';

export interface CandidateFeatures {
  baseline_n: number;
  peak_delta_n: number;
  max_rate_n_per_s: number;
  duration_above_ms: number;
  /** run length from the trigger sample without dropping below trigger */
  continuous_above_ms: number;
  short_term_variance: number;
  oscillation_count: number;
  sensor_health: SensorHealth;
}

export interface Decision {
  result: ClassificationResult;
  reason: string;
  rule_trace: string[];
  features: CandidateFeatures;
  t_start_ms: number;
  t_decision_ms: number;
}

export type DetectorPhase = 'ready' | 'candidate' | 'refractory';

export type DetectorOutput =
  | { kind: 'none' }
  | { kind: 'candidate_started'; t_ms: number }
  | { kind: 'decision'; decision: Decision };

const f0 = (v: number) => cfix(v, 0);
const f3 = (v: number) => cfix(v, 3);

export function emptyFeatures(over: Partial<CandidateFeatures> = {}): CandidateFeatures {
  return {
    baseline_n: 0,
    peak_delta_n: 0,
    max_rate_n_per_s: 0,
    duration_above_ms: 0,
    continuous_above_ms: 0,
    short_term_variance: 0,
    oscillation_count: 0,
    sensor_health: 'ok',
    ...over,
  };
}

/**
 * Pure rule evaluation (firmware DeterministicBaselineV1::decide / Python decide_fw).
 * windowMs: observed window after the trigger; when given, the trace starts with the window line.
 */
export function classify(
  cf: CandidateFeatures,
  th: ClassifierThresholds,
  windowMs?: number,
): Omit<Decision, 'features' | 't_start_ms' | 't_decision_ms'> {
  const tr: string[] = [];
  const out = (result: ClassificationResult, reason: string) => ({ result, reason, rule_trace: tr });
  if (windowMs !== undefined) tr.push(`window: ${f0(windowMs)} ms observed after trigger (decision_window_ms=${f0(th.decision_window_ms)})`);
  const h = cf.sensor_health;
  if (h !== 'ok' && h !== 'degraded') {
    tr.push(`R0 sensor_health=${h} != ok -> FAIL`);
    return out('rejected_sensor_fault', `Sensor health '${h}' during candidate window; no decision can be trusted.`);
  }
  tr.push('R0 sensor_health=ok -> pass');
  const pd = cf.peak_delta_n, mh = th.manual_handling_delta_n;
  if (pd > mh) {
    tr.push(`R1 peak_delta ${f3(pd)} N > manual_handling_delta_n ${f3(mh)} N -> FAIL`);
    return out('rejected_manual_handling', `Peak delta ${f3(pd)} N exceeds manual-handling threshold ${f3(mh)} N (line handled by a person).`);
  }
  tr.push(`R1 peak_delta ${f3(pd)} N <= manual_handling_delta_n ${f3(mh)} N -> pass`);
  const da = cf.duration_above_ms;
  if (da > th.max_duration_ms) {
    tr.push(`R2 duration_above ${f0(da)} ms > max_duration_ms ${f0(th.max_duration_ms)} -> FAIL`);
    return out('rejected_manual_handling', 'Above-trigger duration longer than max_duration_ms (treated as manual handling).');
  }
  tr.push(`R2 duration_above ${f0(da)} ms <= max_duration_ms ${f0(th.max_duration_ms)} -> pass`);
  const oc = cf.oscillation_count;
  if (oc > th.max_osc) {
    tr.push(`R3 oscillation_count ${f0(oc)} > max_osc ${f0(th.max_osc)} -> FAIL`);
    return out('rejected_vibration', `Oscillation count ${f0(oc)} exceeds max_osc ${f0(th.max_osc)} (rod/wave vibration, low mean pull).`);
  }
  tr.push(`R3 oscillation_count ${f0(oc)} <= max_osc ${f0(th.max_osc)} -> pass`);
  const mr = cf.max_rate_n_per_s;
  if (mr < th.min_rate_n_per_s) {
    tr.push(`R4 max_rate ${f3(mr)} N/s < min_rate ${f3(th.min_rate_n_per_s)} N/s -> FAIL`);
    return out(
      'rejected_noise',
      `Sustained but slow rise (peak rate ${f3(mr)} N/s < ${f3(th.min_rate_n_per_s)} N/s): current/drift, not a bite.`,
    );
  }
  tr.push(`R4 max_rate ${f3(mr)} N/s >= min_rate ${f3(th.min_rate_n_per_s)} N/s -> pass`);
  const ca = cf.continuous_above_ms;
  if (ca < th.confirm_min_ms) {
    tr.push(`R5 continuous_above ${f0(ca)} ms < confirm_min_ms ${f0(th.confirm_min_ms)} -> FAIL`);
    return out('rejected_transient', `Delta stayed above trigger for only ${f0(ca)} ms (< ${f0(th.confirm_min_ms)} ms): transient.`);
  }
  tr.push(`R5 continuous_above ${f0(ca)} ms >= confirm_min_ms ${f0(th.confirm_min_ms)} -> pass`);
  tr.push('ALL rules pass -> bite_candidate_confirmed (thresholds provisional)');
  return out(
    'bite_candidate_confirmed',
    `Sustained pull (${f0(ca)} ms above trigger) with fast rise (${f3(mr)} N/s), low oscillation, healthy sensor.`,
  );
}

/** Python-port convention: sensor health failure while monitoring (no open candidate). */
export function monitorSensorFaultDecision(f: SampleFeatures): Decision {
  const t = f.t_ms;
  return {
    result: 'rejected_sensor_fault',
    reason: `Sensor health '${f.health}' at t=${f0(t)} ms while monitoring; measurements cannot be trusted, no actuation.`,
    rule_trace: [`t=${f0(t)}ms sensor_health=${f.health} while MONITOR (no candidate open) -> FAIL`],
    features: emptyFeatures({ baseline_n: f.baseline, short_term_variance: f.variance, sensor_health: f.health }),
    t_start_ms: t,
    t_decision_ms: t,
  };
}

/** Python-port convention: no candidate in the whole window. */
export function noEventDecision(tEnd: number, th: ClassifierThresholds, last: SampleFeatures | null): Decision {
  return {
    result: 'no_event',
    reason: `delta never reached trigger_delta_n ${pyRepr(th.trigger_delta_n)} N in the window`,
    rule_trace: [`t=${f0(tEnd)}ms no candidate in window`],
    features: emptyFeatures({
      baseline_n: last ? last.baseline : 0,
      short_term_variance: last ? last.variance : 0,
      sensor_health: last ? last.health : 'ok',
    }),
    t_start_ms: tEnd,
    t_decision_ms: tEnd,
  };
}

export class BiteDetector {
  private th: ClassifierThresholds;
  phase: DetectorPhase = 'ready';
  private f: CandidateFeatures = emptyFeatures();
  private tTrigger = 0;
  private tLast = 0;
  private runAlive = false;
  private schmitt = 0; // -1 low, 0 unknown, +1 high
  private triggerTrace: string[] = [];
  private quiet = false;
  private quietSince = 0;

  constructor(th: ClassifierThresholds) {
    this.th = th;
  }
  setThresholds(th: ClassifierThresholds) {
    this.th = th;
  }
  reset() {
    this.phase = 'ready';
    this.quiet = false;
  }
  get inCandidate() {
    return this.phase === 'candidate';
  }

  /** Force a decision on the open candidate (end of the scenario window, safe stop or operator abort). */
  forceDecision(tMs: number): Decision | null {
    if (this.phase !== 'candidate') return null;
    return this.decide(tMs);
  }

  private decide(tMs: number): Decision {
    const features = { ...this.f };
    const c = classify(features, this.th, this.tLast - this.tTrigger + DT_MS);
    this.phase = 'refractory';
    this.quiet = false;
    return { result: c.result, reason: c.reason, rule_trace: [...this.triggerTrace, ...c.rule_trace], features, t_start_ms: this.tTrigger, t_decision_ms: tMs };
  }

  /** firmware DeterministicBaselineV1::update - returns true when the candidate window is complete. */
  private step(s: SampleFeatures): boolean {
    const th = this.th;
    const f = this.f;
    if (!s.valid) {
      f.sensor_health = s.health;
      return true;
    }
    f.peak_delta_n = Math.max(f.peak_delta_n, s.delta);
    f.max_rate_n_per_s = Math.max(f.max_rate_n_per_s, s.rate);
    f.short_term_variance = Math.max(f.short_term_variance, s.variance);
    const above = s.delta >= th.trigger_delta_n;
    if (above) f.duration_above_ms += DT_MS;
    if (this.runAlive) {
      if (above) f.continuous_above_ms = s.t_ms - this.tTrigger + DT_MS;
      else this.runAlive = false;
    }
    if (s.detrended >= th.osc_band_n) {
      if (this.schmitt === -1) f.oscillation_count++;
      this.schmitt = 1;
    } else if (s.detrended <= -th.osc_band_n) {
      if (this.schmitt === 1) f.oscillation_count++;
      this.schmitt = -1;
    }
    this.tLast = s.t_ms;
    if (s.delta > th.manual_handling_delta_n) return true;
    return s.t_ms - this.tTrigger + DT_MS >= th.decision_window_ms;
  }

  /** firmware DeterministicBaselineV1::rearmed */
  private rearmed(s: SampleFeatures): boolean {
    const th = this.th;
    const below = s.valid && s.delta < th.trigger_delta_n * th.release_ratio;
    if (!below) {
      this.quiet = false;
      return false;
    }
    if (!this.quiet) {
      this.quiet = true;
      this.quietSince = s.t_ms;
    }
    return s.t_ms - this.quietSince >= th.rearm_quiet_ms;
  }

  update(s: SampleFeatures): DetectorOutput {
    const th = this.th;
    if (this.phase === 'refractory') {
      if (this.rearmed(s)) this.phase = 'ready';
      return { kind: 'none' };
    }
    if (this.phase === 'ready') {
      if (s.valid && s.delta >= th.trigger_delta_n) {
        this.phase = 'candidate';
        this.f = emptyFeatures({ baseline_n: s.baseline, sensor_health: s.health });
        this.tTrigger = this.tLast = s.t_ms;
        this.runAlive = true;
        this.schmitt = 0;
        this.triggerTrace = [
          `t=${f0(s.t_ms)}ms TRIGGER delta=${f3(s.delta)}N >= trigger_delta_n=${pyRepr(th.trigger_delta_n)}N (baseline frozen at ${f3(s.baseline)}N)`,
        ];
        this.step(s); // beginCandidate() calls update(trigger); the return value is ignored
        return { kind: 'candidate_started', t_ms: s.t_ms };
      }
      return { kind: 'none' };
    }
    // candidate
    if (this.step(s)) return { kind: 'decision', decision: this.decide(s.t_ms) };
    return { kind: 'none' };
  }
}
