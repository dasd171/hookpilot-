/**
 * BiteDetector "deterministic-baseline-v1" (see SHARED_CONTRACT.md).
 * Thresholds are configurable PLACEHOLDERS (provisional - derive from calibration + labeled data).
 *
 * Trigger: delta >= trigger_delta_n opens a candidate (baseline is frozen by the caller while open).
 * The candidate is observed for confirm_min_ms (early exit on sensor fault or manual-handling delta),
 * then rules are evaluated in this fixed order; the first failing rule decides the result:
 *   R1 sensor_health == ok                                   else rejected_sensor_fault
 *   R2 peak_delta <= manual_handling_delta_n                 else rejected_manual_handling
 *   R3 duration_above <= max_duration_ms                     else rejected_manual_handling
 *   R4 oscillation_count <= max_osc                          else rejected_vibration
 *   R5 max_rate >= min_rate_n_per_s                          else rejected_noise (slow drift / current)
 *   R6 duration_above (continuous) >= confirm_min_ms         else rejected_transient (fast but short)
 *   all pass                                                 -> bite_candidate_confirmed
 */
import type { ClassificationResult, SensorHealth } from '../../../../packages/shared-types/src/index';
import type { ClassifierThresholds } from './config';
import { DT_MS } from './config';
import type { SampleFeatures } from './processor';

export interface CandidateFeatures {
  baseline_n: number;
  peak_delta_n: number;
  max_rate_n_per_s: number;
  duration_above_ms: number;
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

const f2 = (v: number) => v.toFixed(2);

const HEALTH_RANK: Record<SensorHealth, number> = { ok: 0, degraded: 1, stuck: 2, out_of_range: 3, disconnected: 4 };

/** Pure rule evaluation - exported for unit tests. */
export function classify(cf: CandidateFeatures, th: ClassifierThresholds): Omit<Decision, 'features' | 't_start_ms' | 't_decision_ms'> {
  const trace: string[] = [];
  const done = (result: ClassificationResult, reason: string) => {
    trace.push(`=> ${result}`);
    return { result, reason, rule_trace: trace };
  };
  trace.push(`TRIGGER delta >= ${f2(th.trigger_delta_n)} N; baseline frozen at ${f2(cf.baseline_n)} N; window ${th.confirm_min_ms} ms`);
  if (cf.sensor_health !== 'ok') {
    trace.push(`R1 sensor_health=${cf.sensor_health} != ok -> FAIL`);
    return done('rejected_sensor_fault', `Sensor health is "${cf.sensor_health}"; readings cannot be trusted, no actuation.`);
  }
  trace.push('R1 sensor_health=ok -> pass');
  if (cf.peak_delta_n > th.manual_handling_delta_n) {
    trace.push(`R2 peak_delta ${f2(cf.peak_delta_n)} N > manual_handling_delta ${f2(th.manual_handling_delta_n)} N -> FAIL`);
    return done(
      'rejected_manual_handling',
      `Peak delta ${f2(cf.peak_delta_n)} N exceeds manual-handling limit ${f2(th.manual_handling_delta_n)} N (line likely grabbed or rod handled).`,
    );
  }
  trace.push(`R2 peak_delta ${f2(cf.peak_delta_n)} N <= manual_handling_delta ${f2(th.manual_handling_delta_n)} N -> pass`);
  if (cf.duration_above_ms > th.max_duration_ms) {
    trace.push(`R3 duration_above ${cf.duration_above_ms} ms > max_duration ${th.max_duration_ms} ms -> FAIL`);
    return done('rejected_manual_handling', `Tension stayed above trigger for ${cf.duration_above_ms} ms (> ${th.max_duration_ms} ms), consistent with handling, not a bite.`);
  }
  trace.push(`R3 duration_above ${cf.duration_above_ms} ms <= max_duration ${th.max_duration_ms} ms -> pass`);
  if (cf.oscillation_count > th.max_osc) {
    trace.push(`R4 oscillation_count ${cf.oscillation_count} > max_osc ${th.max_osc} -> FAIL`);
    return done('rejected_vibration', `Signal oscillated ${cf.oscillation_count} times in the window (> ${th.max_osc}): rod/line vibration, not a sustained pull.`);
  }
  trace.push(`R4 oscillation_count ${cf.oscillation_count} <= max_osc ${th.max_osc} -> pass`);
  if (cf.max_rate_n_per_s < th.min_rate_n_per_s) {
    trace.push(`R5 max_rate ${f2(cf.max_rate_n_per_s)} N/s < min_rate ${f2(th.min_rate_n_per_s)} N/s -> FAIL`);
    return done(
      'rejected_noise',
      `Tension rose slowly (peak rate ${f2(cf.max_rate_n_per_s)} N/s < ${f2(th.min_rate_n_per_s)} N/s): drift from current/drag, not a strike.`,
    );
  }
  trace.push(`R5 max_rate ${f2(cf.max_rate_n_per_s)} N/s >= min_rate ${f2(th.min_rate_n_per_s)} N/s -> pass`);
  if (cf.duration_above_ms < th.confirm_min_ms) {
    trace.push(`R6 duration_above ${cf.duration_above_ms} ms < confirm_min ${th.confirm_min_ms} ms -> FAIL`);
    return done('rejected_transient', `Delta fell back below trigger after ${cf.duration_above_ms} ms (< ${th.confirm_min_ms} ms): short transient.`);
  }
  trace.push(`R6 duration_above ${cf.duration_above_ms} ms >= confirm_min ${th.confirm_min_ms} ms -> pass`);
  return done(
    'bite_candidate_confirmed',
    `Sustained pull: delta ${f2(cf.peak_delta_n)} N held ${cf.duration_above_ms} ms, rate ${f2(cf.max_rate_n_per_s)} N/s, ${cf.oscillation_count} oscillations. Candidate confirmed by provisional rules (not validated).`,
  );
}

export class BiteDetector {
  private th: ClassifierThresholds;
  phase: DetectorPhase = 'ready';
  private tStart = 0;
  private baselineAtTrigger = 0;
  private peakDelta = 0;
  private maxRate = 0;
  private continuous = true;
  private lastAboveT = 0;
  private oscState = 0; // -1, 0, +1
  private oscCount = 0;
  private maxVar = 0;
  private worstHealth: SensorHealth = 'ok';
  private quietMs = 0;

  constructor(th: ClassifierThresholds) {
    this.th = th;
  }
  setThresholds(th: ClassifierThresholds) {
    this.th = th;
  }
  reset() {
    this.phase = 'ready';
    this.quietMs = 0;
  }
  get inCandidate() {
    return this.phase === 'candidate';
  }

  private currentFeatures(): CandidateFeatures {
    return {
      baseline_n: this.baselineAtTrigger,
      peak_delta_n: this.peakDelta,
      max_rate_n_per_s: this.maxRate,
      duration_above_ms: this.lastAboveT - this.tStart + DT_MS,
      short_term_variance: this.maxVar,
      oscillation_count: this.oscCount,
      sensor_health: this.worstHealth,
    };
  }

  /** Force a decision on the open candidate (e.g. safe stop or operator abort). */
  forceDecision(tMs: number): Decision | null {
    if (this.phase !== 'candidate') return null;
    return this.decide(tMs);
  }

  private decide(tMs: number): Decision {
    const features = this.currentFeatures();
    const c = classify(features, this.th);
    this.phase = 'refractory';
    this.quietMs = 0;
    return { ...c, features, t_start_ms: this.tStart, t_decision_ms: tMs };
  }

  update(f: SampleFeatures): DetectorOutput {
    const th = this.th;
    if (this.phase === 'refractory') {
      if (f.valid && f.delta < th.trigger_delta_n * th.release_ratio) this.quietMs += DT_MS;
      else this.quietMs = 0;
      if (this.quietMs >= th.rearm_quiet_ms) this.phase = 'ready';
      return { kind: 'none' };
    }
    if (this.phase === 'ready') {
      if (f.valid && f.delta >= th.trigger_delta_n) {
        this.phase = 'candidate';
        this.tStart = f.t_ms;
        this.baselineAtTrigger = f.baseline;
        this.peakDelta = f.delta;
        this.maxRate = Math.max(0, f.rate);
        this.continuous = true;
        this.lastAboveT = f.t_ms;
        this.oscState = 0;
        this.oscCount = 0;
        this.maxVar = f.variance;
        this.worstHealth = f.health;
        this.trackOsc(f.detrended);
        return { kind: 'candidate_started', t_ms: f.t_ms };
      }
      return { kind: 'none' };
    }
    // candidate
    if (HEALTH_RANK[f.health] > HEALTH_RANK[this.worstHealth]) this.worstHealth = f.health;
    if (!f.valid) return { kind: 'decision', decision: this.decide(f.t_ms) };
    this.peakDelta = Math.max(this.peakDelta, f.delta);
    this.maxRate = Math.max(this.maxRate, f.rate);
    this.maxVar = Math.max(this.maxVar, f.variance);
    if (f.delta >= th.trigger_delta_n) {
      if (this.continuous) this.lastAboveT = f.t_ms;
    } else this.continuous = false;
    this.trackOsc(f.detrended);
    if (this.peakDelta > th.manual_handling_delta_n) return { kind: 'decision', decision: this.decide(f.t_ms) };
    if (f.t_ms - this.tStart + DT_MS >= th.confirm_min_ms) return { kind: 'decision', decision: this.decide(f.t_ms) };
    return { kind: 'none' };
  }

  private trackOsc(d: number) {
    const band = this.th.osc_band_n;
    const s = d > band ? 1 : d < -band ? -1 : 0;
    if (s !== 0) {
      if (this.oscState !== 0 && s !== this.oscState) this.oscCount++;
      this.oscState = s;
    }
  }
}
