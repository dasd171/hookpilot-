/**
 * Synthetic signal generators - SIMULATION ONLY. Reproducible from (scenario, seed) using mulberry32.
 * Generator id: "hookpilot-web-synth-v1" (web demo). Sample rate 50 Hz (t_k = 20*k ms).
 *
 * Draw order (all from one Mulberry32(seed)):
 *   1. phase = uniform(0, 2*pi)       2. base0 = uniform(0.5, 0.7)
 *   3. scenario parameters, in the order listed in each generator below
 *   4. per sample k: n_k = gauss()  (two draws) ; manual_handling adds a second gauss() per sample
 * base(t) = base0 + 0.05*sin(2*pi*t/4000 + phase) + 0.03*n_k   [N]
 * Times drawn with uniform() are rounded to the 20 ms grid.
 */
import { Mulberry32 } from './prng';
import type { ClassificationResult, ScenarioName } from '../../../../packages/shared-types/src/index';
import { DT_MS } from './config';

export const GENERATOR_ID = 'hookpilot-web-synth-v1';

export interface ScenarioSignal {
  scenario: ScenarioName;
  seed: number;
  generator: string;
  durationMs: number;
  /** environmental line tension per sample in N; NaN = sensor disconnected */
  tension: Float64Array;
  /** 1 = host heartbeat received in this sample, 0 = lost */
  heartbeat: Uint8Array;
  /** retrieval actuator jams from this time (ms since scenario start), or null */
  stallFromMs: number | null;
  /** key times for documentation / tests (ms since scenario start) */
  marks: Record<string, number>;
  /** classifier result the scenario is designed to produce (ground truth for tests, not a validation claim) */
  expectedResult: ClassificationResult;
}

const TWO_PI = Math.PI * 2;
const grid = (ms: number) => Math.round(ms / DT_MS) * DT_MS;
/** smoothstep 0..1 */
function ss(x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return x * x * (3 - 2 * x);
}
/** envelope rising over [a, a+rise], falling over [b, b+fall] */
function env(t: number, a: number, rise: number, b: number, fall: number): number {
  if (t < a) return 0;
  if (t < b) return ss((t - a) / rise);
  return ss((t - a) / rise) * (1 - ss((t - b) / fall));
}

interface Builder {
  n: number;
  tension: Float64Array;
  heartbeat: Uint8Array;
  base: (k: number) => number;
}

function builder(rng: Mulberry32, durationMs: number): Builder {
  const phase = rng.uniform(0, TWO_PI);
  const base0 = rng.uniform(0.5, 0.7);
  const n = Math.round(durationMs / DT_MS);
  const tension = new Float64Array(n);
  const heartbeat = new Uint8Array(n).fill(1);
  return {
    n,
    tension,
    heartbeat,
    base: (k: number) => {
      const t = k * DT_MS;
      return base0 + 0.05 * Math.sin((TWO_PI * t) / 4000 + phase) + 0.03 * rng.gauss();
    },
  };
}

/** Bite component: sustained pull with low-frequency fish pulls. Params drawn: T0, A. */
function biteParams(rng: Mulberry32) {
  const T0 = grid(rng.uniform(1000, 1200));
  const A = rng.uniform(2.4, 3.0);
  return { T0, A };
}
function biteAt(t: number, T0: number, A: number, releaseAt: number): number {
  const e = env(t, T0, 120, releaseAt, 300);
  return e * (A + 0.3 * Math.sin((TWO_PI * 1.6 * (t - T0)) / 1000));
}

export function generateScenario(scenario: ScenarioName, seed: number): ScenarioSignal {
  const rng = new Mulberry32(seed);
  const marks: Record<string, number> = {};
  let stallFromMs: number | null = null;
  let expectedResult: ClassificationResult;
  let durationMs: number;
  let b: Builder;

  switch (scenario) {
    case 'bite':
    case 'comm_loss':
    case 'motor_stall': {
      durationMs = 5200;
      b = builder(rng, durationMs);
      const { T0, A } = biteParams(rng);
      const rel = 4600;
      for (let k = 0; k < b.n; k++) b.tension[k] = b.base(k) + biteAt(k * DT_MS, T0, A, rel);
      marks.bite_onset = T0;
      marks.release = rel;
      if (scenario === 'comm_loss') {
        const a = T0 + 600, z = T0 + 2600;
        for (let k = 0; k < b.n; k++) if (k * DT_MS >= a && k * DT_MS < z) b.heartbeat[k] = 0;
        marks.comm_lost = a;
        marks.comm_restored = z;
      }
      if (scenario === 'motor_stall') {
        stallFromMs = T0 + 900;
        marks.stall = stallFromMs;
      }
      expectedResult = 'bite_candidate_confirmed';
      break;
    }
    case 'excessive_tension': {
      durationMs = 6000;
      b = builder(rng, durationMs);
      const { T0, A } = biteParams(rng);
      const peak = rng.uniform(22, 26); // extra pull added on top of the bite (N)
      const rel = 5200;
      const S = T0 + 1200;
      for (let k = 0; k < b.n; k++) {
        const t = k * DT_MS;
        b.tension[k] = b.base(k) + biteAt(t, T0, A, rel) + (peak - A) * env(t, S, 1200, rel, 300);
      }
      marks.bite_onset = T0;
      marks.surge_start = S;
      marks.release = rel;
      expectedResult = 'bite_candidate_confirmed';
      break;
    }
    case 'noise_current': {
      durationMs = 5400;
      b = builder(rng, durationMs);
      const Ts = grid(rng.uniform(700, 900));
      const R = rng.uniform(2.2, 2.6);
      for (let k = 0; k < b.n; k++) {
        const t = k * DT_MS;
        const e = env(t, Ts, 1400, 4000, 1000);
        b.tension[k] = b.base(k) + e * (R + 0.12 * Math.sin((TWO_PI * 0.4 * t) / 1000));
      }
      marks.surge_start = Ts;
      expectedResult = 'rejected_noise';
      break;
    }
    case 'rod_vibration': {
      durationMs = 4200;
      b = builder(rng, durationMs);
      const Ts = grid(rng.uniform(800, 1000));
      const f = rng.uniform(10, 12);
      const amp = rng.uniform(1.4, 1.7);
      const Te = Ts + 2200;
      for (let k = 0; k < b.n; k++) {
        const t = k * DT_MS;
        const e = env(t, Ts, 100, Te, 100);
        b.tension[k] = b.base(k) + e * (0.25 + amp * Math.sin((TWO_PI * f * (t - Ts)) / 1000));
      }
      marks.vibration_start = Ts;
      marks.vibration_end = Te;
      expectedResult = 'rejected_vibration';
      break;
    }
    case 'manual_handling': {
      durationMs = 5200;
      b = builder(rng, durationMs);
      const Ts = grid(rng.uniform(900, 1100));
      const M = rng.uniform(13.5, 15.5);
      const rel = Ts + 2900;
      for (let k = 0; k < b.n; k++) {
        const t = k * DT_MS;
        const e = env(t, Ts, 400, rel, 400);
        const irregular = 0.6 * Math.sin((TWO_PI * 0.7 * t) / 1000) + 0.4 * Math.sin((TWO_PI * 1.9 * t) / 1000);
        const base = b.base(k);
        b.tension[k] = base + e * (M + irregular + 0.1 * rng.gauss());
      }
      marks.grab_start = Ts;
      marks.release = rel;
      expectedResult = 'rejected_manual_handling';
      break;
    }
    case 'sensor_failure': {
      durationMs = 4000;
      b = builder(rng, durationMs);
      const Ts = grid(rng.uniform(900, 1100));
      const stuckEnd = Ts + 400, discEnd = Ts + 2400;
      let held = NaN;
      for (let k = 0; k < b.n; k++) {
        const t = k * DT_MS;
        const v = b.base(k); // always draw so later samples are independent of fault timing
        if (t < Ts) {
          b.tension[k] = v;
          held = v;
        } else if (t < stuckEnd) b.tension[k] = held;
        else if (t < discEnd) b.tension[k] = NaN;
        else b.tension[k] = v;
      }
      marks.stuck_start = Ts;
      marks.disconnect_start = stuckEnd;
      marks.restored = discEnd;
      expectedResult = 'rejected_sensor_fault';
      break;
    }
    default: {
      const never: never = scenario;
      throw new Error(`unknown scenario ${String(never)}`);
    }
  }
  return {
    scenario,
    seed,
    generator: GENERATOR_ID,
    durationMs,
    tension: b.tension,
    heartbeat: b.heartbeat,
    stallFromMs,
    marks,
    expectedResult,
  };
}

/** Quiet water between scenarios (continuous, reproducible from seed). */
export class IdleSource {
  private rng: Mulberry32;
  private phase: number;
  private base0: number;
  constructor(seed: number) {
    this.rng = new Mulberry32((seed ^ 0x9e3779b9) | 0);
    this.phase = this.rng.uniform(0, TWO_PI);
    this.base0 = this.rng.uniform(0.5, 0.7);
  }
  sample(tMs: number): number {
    return this.base0 + 0.05 * Math.sin((TWO_PI * tMs) / 4000 + this.phase) + 0.03 * this.rng.gauss();
  }
}
