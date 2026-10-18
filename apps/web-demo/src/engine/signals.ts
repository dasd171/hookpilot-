/**
 * Synthetic line-tension generator "hookpilot-synth-v1" - SIMULATION ONLY.
 *
 * TypeScript port of services/evidence-service/evidence_service/synth.py, which is itself ported
 * bit-for-bit by firmware/src/core/scenarios.cpp. The draw order is the specification
 * (docs/synthetic-signals.md); following it, (scenario, seed) yields the same samples in TS, Python and
 * C++ up to libm last-ulp differences in log/cos/sin (cross_impl.test.ts checks |diff| <= 1e-9).
 *
 * Units: time ms, tension N. 50 Hz, default 8000 ms window (400 samples), t_i = 20*i.
 * The distributions are invented to exercise every rule; they are not fitted to real bites.
 */
import { Mulberry32 } from './prng';
import type { ClassificationResult, ScenarioName } from '../../../../packages/shared-types/src/index';
import { SAMPLE_RATE_HZ } from './config';

export const GENERATOR_ID = 'hookpilot-synth-v1';
export const NOISE_SIGMA_N = 0.02;
export const SENSOR_SATURATION_N = 49.99;
export const DEFAULT_DURATION_MS = 8000;

/** source.ground_truth_label per scenario (identical to synth.py GROUND_TRUTH). */
export const GROUND_TRUTH: Record<ScenarioName | 'idle', string> = {
  idle: 'no_bite',
  bite: 'bite',
  noise_current: 'no_bite_current_drift',
  rod_vibration: 'no_bite_vibration',
  manual_handling: 'no_bite_manual_handling',
  sensor_failure: 'sensor_fault',
  excessive_tension: 'bite_then_over_tension',
  motor_stall: 'bite_then_motor_stall',
  comm_loss: 'bite_then_comm_loss',
};

/** Acceptable classifications per scenario (synth.py EXPECTED_CLASSIFICATION). Design intent, not validation. */
export const EXPECTED_CLASSIFICATION: Record<ScenarioName | 'idle', ClassificationResult[]> = {
  idle: ['no_event'],
  bite: ['bite_candidate_confirmed'],
  noise_current: ['rejected_noise', 'no_event'],
  rod_vibration: ['rejected_vibration'],
  manual_handling: ['rejected_manual_handling'],
  sensor_failure: ['rejected_sensor_fault'],
  excessive_tension: ['bite_candidate_confirmed'],
  motor_stall: ['bite_candidate_confirmed'],
  comm_loss: ['bite_candidate_confirmed'],
};

export type SynthScenario = ScenarioName | 'idle';

/** Drawn parameters (ground truth). Keys match synth.py's params dict. */
export interface SynthParams {
  base_n: number;
  onset_ms: number;
  amp_n?: number;
  rise_ms?: number;
  hold_ms?: number;
  fall_ms?: number;
  shake_hz?: number;
  shake_amp_n?: number;
  shake_phase?: number;
  ramp_ms?: number;
  freq_hz?: number;
  dur_ms?: number;
  pre_ms?: number;
  over_rate_n_per_s?: number;
  cap_n?: number;
  mode?: 'stuck' | 'out_of_range';
  fault_dur_ms?: number;
  fault_after_retrieval_start_ms?: number;
}

export interface ScenarioSignal {
  scenario: SynthScenario;
  seed: number;
  generator: string;
  sampleRateHz: number;
  durationMs: number;
  /** sample times in ms since scenario start */
  t: Float64Array;
  /** environmental line tension per sample in N */
  tension: Float64Array;
  params: SynthParams;
  groundTruth: string;
  /** classifier results the scenario is designed to produce (first = primary); not a validation claim */
  expectedResults: ClassificationResult[];
  /**
   * motor_stall / comm_loss: the fault is injected by the actuation model this long after retrieval starts
   * (snapped to the 20 ms grid as in engine.py); null otherwise. It is not in the tension signal.
   */
  faultAfterRetrievalStartMs: number | null;
}

const TWO_PI = 2 * Math.PI;
const snap = (ms: number, dt: number) => dt * Math.floor(ms / dt);
/** Python round() (banker's rounding) - used where synth.py / engine.py call round(). */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

function biteShape(tau: number, p: SynthParams): number {
  const A = p.amp_n!, rise = p.rise_ms!, hold = p.hold_ms!, fall = p.fall_ms!;
  if (tau < 0) return 0;
  if (tau < rise) return (A * tau) / rise;
  if (tau < rise + hold) return A + p.shake_amp_n! * Math.sin((2.0 * Math.PI * p.shake_hz! * (tau - rise)) / 1000.0 + p.shake_phase!);
  if (tau < rise + hold + fall) return A * (1.0 - (tau - rise - hold) / fall);
  return 0;
}

export function generateScenario(
  scenario: SynthScenario,
  seed: number,
  sampleRateHz: number = SAMPLE_RATE_HZ,
  durationMs: number = DEFAULT_DURATION_MS,
): ScenarioSignal {
  if (!(scenario in GROUND_TRUTH)) throw new Error(`unknown scenario ${String(scenario)}`);
  const rng = new Mulberry32(seed >>> 0);
  const dt = 1000.0 / sampleRateHz;
  const n = pyRound(durationMs / dt);
  const t = new Float64Array(n);
  for (let i = 0; i < n; i++) t[i] = i * dt;

  // 1) common draws
  const base = rng.uniform(0.35, 0.5);
  const onset = snap(rng.uniform(1500.0, 3000.0), dt);
  const p: SynthParams = { base_n: base, onset_ms: onset };

  // 2) scenario draws (order is normative)
  switch (scenario) {
    case 'bite':
      p.amp_n = rng.uniform(0.7, 2.6);
      p.rise_ms = rng.uniform(60, 200);
      p.hold_ms = rng.uniform(150, 1500);
      p.shake_hz = rng.uniform(1.5, 3.0);
      p.shake_amp_n = rng.uniform(0.03, 0.1);
      p.shake_phase = rng.uniform(0, TWO_PI);
      p.fall_ms = rng.uniform(150, 400);
      break;
    case 'noise_current':
      p.amp_n = rng.uniform(0.9, 1.7);
      p.ramp_ms = rng.uniform(2500, 4000);
      break;
    case 'rod_vibration':
      p.freq_hz = rng.uniform(6, 12);
      p.amp_n = rng.uniform(0.9, 1.5);
      p.dur_ms = rng.uniform(1000, 2000);
      break;
    case 'manual_handling':
      p.amp_n = rng.uniform(6.5, 12.0);
      p.rise_ms = rng.uniform(300, 800);
      p.hold_ms = rng.uniform(1000, 3000);
      p.fall_ms = 300.0;
      break;
    case 'sensor_failure':
      p.mode = rng.next() < 0.5 ? 'stuck' : 'out_of_range';
      p.fault_dur_ms = rng.uniform(200, 1000);
      break;
    case 'excessive_tension':
      p.amp_n = rng.uniform(1.2, 2.2);
      p.rise_ms = rng.uniform(60, 200);
      p.pre_ms = rng.uniform(600, 1000);
      p.over_rate_n_per_s = rng.uniform(8, 16);
      p.cap_n = 15.0;
      break;
    case 'motor_stall':
    case 'comm_loss':
      p.amp_n = rng.uniform(1.2, 2.2);
      p.rise_ms = rng.uniform(60, 200);
      p.hold_ms = rng.uniform(2000, 3000);
      p.shake_hz = rng.uniform(1.5, 3.0);
      p.shake_amp_n = rng.uniform(0.03, 0.1);
      p.shake_phase = rng.uniform(0, TWO_PI);
      p.fall_ms = 300.0;
      p.fault_after_retrieval_start_ms = rng.uniform(200, 800);
      break;
    default:
      break; // idle: no scenario draws
  }

  // 3) noise: one gauss() (two draws) per sample, always, for every sample
  const noise = new Float64Array(n);
  for (let i = 0; i < n; i++) noise[i] = NOISE_SIGMA_N * rng.gauss();

  // 4) compose
  const x = new Float64Array(n);
  const biteLike = scenario === 'bite' || scenario === 'motor_stall' || scenario === 'comm_loss';
  for (let i = 0; i < n; i++) {
    const tau = t[i] - onset;
    let s = 0.0;
    if (biteLike) {
      s = biteShape(tau, p);
    } else if (scenario === 'noise_current') {
      s = tau < 0 ? 0.0 : tau < p.ramp_ms! ? (p.amp_n! * tau) / p.ramp_ms! : p.amp_n!;
    } else if (scenario === 'rod_vibration') {
      if (tau >= 0 && tau < p.dur_ms!) {
        const env = Math.min(1.0, tau / 100.0, (p.dur_ms! - tau) / 100.0);
        s = p.amp_n! * env * Math.sin((2 * Math.PI * p.freq_hz! * tau) / 1000.0);
      }
    } else if (scenario === 'manual_handling') {
      const A = p.amp_n!, r = p.rise_ms!, h = p.hold_ms!, f = p.fall_ms!;
      if (tau >= 0 && tau < r) s = (A * tau) / r;
      else if (tau >= r && tau < r + h) s = A;
      else if (tau >= r + h && tau < r + h + f) s = A * (1 - (tau - r - h) / f);
    } else if (scenario === 'excessive_tension') {
      const A = p.amp_n!, r = p.rise_ms!, pre = p.pre_ms!;
      if (tau >= 0 && tau < r) s = (A * tau) / r;
      else if (tau >= r && tau < r + pre) s = A;
      else if (tau >= r + pre) s = Math.min(p.cap_n!, A + (p.over_rate_n_per_s! * (tau - r - pre)) / 1000.0);
    }
    x[i] = base + s + noise[i];
  }

  // 5) sensor fault post-processing
  if (scenario === 'sensor_failure') {
    const i0 = pyRound(onset / dt);
    if (p.mode === 'stuck') {
      const frozen = x[i0];
      for (let i = i0; i < n; i++) x[i] = frozen;
    } else {
      const i1 = Math.min(n, i0 + pyRound(p.fault_dur_ms! / dt));
      for (let i = i0; i < i1; i++) x[i] = SENSOR_SATURATION_N;
    }
  }

  const faultAfter =
    p.fault_after_retrieval_start_ms !== undefined ? dt * pyRound(p.fault_after_retrieval_start_ms / dt) : null;
  return {
    scenario,
    seed,
    generator: GENERATOR_ID,
    sampleRateHz,
    durationMs,
    t,
    tension: x,
    params: p,
    groundTruth: GROUND_TRUTH[scenario],
    expectedResults: EXPECTED_CLASSIFICATION[scenario],
    faultAfterRetrievalStartMs: faultAfter,
  };
}

/**
 * Quiet water between scenarios in the interactive demo (continuous, reproducible from seed).
 * Same model as the generator's idle scenario (base uniform(0.35, 0.50) N + 0.02 N gaussian noise), but an
 * unbounded stream: it is NOT part of any replayable record, because every scenario run re-initialises the
 * signal processor and records only scenario samples.
 */
export class IdleSource {
  private rng: Mulberry32;
  private base: number;
  constructor(seed: number) {
    this.rng = new Mulberry32((seed ^ 0x9e3779b9) >>> 0);
    this.base = this.rng.uniform(0.35, 0.5);
  }
  sample(_tMs: number): number {
    return this.base + NOISE_SIGMA_N * this.rng.gauss();
  }
}
