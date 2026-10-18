/** SignalProcessor: per-sample feature extraction + sensor health checks (pure TS, no I/O). */
import type { SensorHealth } from '../../../../packages/shared-types/src/index';
import type { ClassifierThresholds, SafetyLimits } from './config';
import { DT_MS } from './config';

export interface SampleFeatures {
  t_ms: number;
  raw: number; // NaN when disconnected
  valid: boolean;
  health: SensorHealth;
  /** last valid tension value (N) */
  x: number;
  smoothed: number;
  baseline: number;
  baselineFrozen: boolean;
  delta: number;
  rate: number; // N/s, signed
  variance: number;
  detrended: number;
}

export class SignalProcessor {
  private th: ClassifierThresholds;
  private lim: SafetyLimits;
  private initialized = false;
  private baseline = 0;
  private smoothed = 0;
  private smoothHist: number[] = [];
  private window: number[] = [];
  private detrendWin: number[] = [];
  private lastRaw = NaN;
  private identicalRun = 0;
  private last: SampleFeatures | null = null;

  constructor(th: ClassifierThresholds, lim: SafetyLimits) {
    this.th = th;
    this.lim = lim;
  }

  setConfig(th: ClassifierThresholds, lim: SafetyLimits) {
    this.th = th;
    this.lim = lim;
  }

  reset() {
    this.initialized = false;
    this.smoothHist = [];
    this.window = [];
    this.detrendWin = [];
    this.lastRaw = NaN;
    this.identicalRun = 0;
    this.last = null;
  }

  get latest(): SampleFeatures | null {
    return this.last;
  }

  update(tMs: number, raw: number, freezeBaseline: boolean): SampleFeatures {
    const th = this.th;
    // ---- health checks ----
    let health: SensorHealth = 'ok';
    if (!Number.isFinite(raw)) {
      health = 'disconnected';
      this.identicalRun = 0;
    } else {
      if (raw === this.lastRaw) this.identicalRun++;
      else this.identicalRun = 1;
      if (raw < this.lim.sensor_min_n || raw > this.lim.sensor_max_n) health = 'out_of_range';
      else if (this.identicalRun >= this.lim.stuck_samples) health = 'stuck';
    }
    this.lastRaw = raw;

    if (health !== 'ok' || !this.initialized) {
      if (health === 'ok' && !this.initialized) {
        this.initialized = true;
        this.baseline = raw;
        this.smoothed = raw;
      } else if (health !== 'ok') {
        // hold previous features, mark invalid
        const prev = this.last;
        const f: SampleFeatures = {
          t_ms: tMs,
          raw,
          valid: false,
          health,
          x: prev ? prev.x : 0,
          smoothed: prev ? prev.smoothed : 0,
          baseline: prev ? prev.baseline : 0,
          baselineFrozen: freezeBaseline,
          delta: prev ? prev.delta : 0,
          rate: 0,
          variance: prev ? prev.variance : 0,
          detrended: 0,
        };
        this.last = f;
        return f;
      }
    }

    const x = raw;
    if (!freezeBaseline) this.baseline += th.baseline_alpha * (x - this.baseline);
    this.smoothed += th.smoothing_alpha * (x - this.smoothed);
    this.smoothHist.push(this.smoothed);
    if (this.smoothHist.length > th.rate_lag_samples + 1) this.smoothHist.shift();
    const lagN = this.smoothHist.length - 1;
    const rate = lagN > 0 ? ((this.smoothed - this.smoothHist[0]) / (lagN * DT_MS)) * 1000 : 0;

    this.window.push(x);
    if (this.window.length > th.variance_window_samples) this.window.shift();
    let mean = 0;
    for (const v of this.window) mean += v;
    mean /= this.window.length;
    let variance = 0;
    for (const v of this.window) variance += (v - mean) * (v - mean);
    variance /= this.window.length;

    this.detrendWin.push(x);
    if (this.detrendWin.length > th.detrend_window_samples) this.detrendWin.shift();
    let dm = 0;
    for (const v of this.detrendWin) dm += v;
    dm /= this.detrendWin.length;

    const f: SampleFeatures = {
      t_ms: tMs,
      raw,
      valid: true,
      health,
      x,
      smoothed: this.smoothed,
      baseline: this.baseline,
      baselineFrozen: freezeBaseline,
      delta: x - this.baseline,
      rate,
      variance,
      detrended: x - dm,
    };
    this.last = f;
    return f;
  }
}
