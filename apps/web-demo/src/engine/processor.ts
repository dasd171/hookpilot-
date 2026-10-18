/**
 * SignalProcessor: per-sample feature extraction + sensor health checks (pure TS, no I/O).
 *
 * Line-by-line port of firmware BaselineSignalProcessor (firmware/src/core/signal_processor.cpp),
 * identical to evidence_service.classifier.FwProcessor:
 *  - health: non-finite -> disconnected; raw < sensor_min_n or raw >= sensor_max_n -> out_of_range;
 *    stuck_samples bit-identical consecutive readings -> stuck.
 *  - only valid samples update x / smoothed / baseline and the 64-sample history; invalid samples
 *    hold the previous values (features are still computed from the history).
 *  - rate = (smoothed[newest] - smoothed[newest-lag]) / (lag * dt), 0 until lag+1 valid samples exist.
 *  - variance = population variance of the last variance_window_samples valid x values;
 *    detrended = x - mean(last detrend_window_samples valid x values).
 */
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

const K = 64; // firmware kMaxWindow

export class SignalProcessor {
  private th: ClassifierThresholds;
  private lim: SafetyLimits;
  private initialized = false;
  private x = 0;
  private smoothed = 0;
  private baseline = 0;
  private xs: number[] = [];
  private ss: number[] = [];
  private lastRaw = 0;
  private haveLastRaw = false;
  private identical = 0;
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
    this.x = this.smoothed = this.baseline = 0;
    this.xs = [];
    this.ss = [];
    this.lastRaw = 0;
    this.haveLastRaw = false;
    this.identical = 0;
    this.last = null;
  }

  get latest(): SampleFeatures | null {
    return this.last;
  }

  private health(v: number): SensorHealth {
    if (!Number.isFinite(v)) {
      this.haveLastRaw = false;
      this.identical = 0;
      return 'disconnected';
    }
    if (v < this.lim.sensor_min_n || v >= this.lim.sensor_max_n) return 'out_of_range';
    if (this.haveLastRaw && v === this.lastRaw) this.identical++;
    else this.identical = 1;
    this.lastRaw = v;
    this.haveLastRaw = true;
    if (this.identical >= this.lim.stuck_samples) return 'stuck';
    return 'ok';
  }

  update(tMs: number, raw: number, freezeBaseline: boolean): SampleFeatures {
    const th = this.th;
    const vw0 = Math.max(2, Math.min(th.variance_window_samples, K));
    const dw0 = Math.max(1, Math.min(th.detrend_window_samples, K));
    const lag = Math.max(1, Math.min(th.rate_lag_samples, K - 1));

    const health = this.health(raw);
    const valid = health === 'ok' || health === 'degraded';
    if (valid) {
      if (!this.initialized) {
        this.x = this.smoothed = this.baseline = raw;
        this.initialized = true;
      } else {
        this.x = raw;
        this.smoothed += th.smoothing_alpha * (raw - this.smoothed);
        if (!freezeBaseline) this.baseline += th.baseline_alpha * (raw - this.baseline);
      }
      this.xs.push(this.x);
      this.ss.push(this.smoothed);
      if (this.xs.length > K) {
        this.xs.shift();
        this.ss.shift();
      }
    }
    const n = this.xs.length;
    const rate = n > lag ? (this.ss[n - 1] - this.ss[n - 1 - lag]) / (lag * (DT_MS / 1000.0)) : 0.0;
    const vw = Math.min(vw0, n);
    let variance = 0.0;
    if (vw >= 2) {
      let m = 0.0;
      for (let k = n - vw; k < n; k++) m += this.xs[k];
      m /= vw;
      let acc = 0.0;
      for (let k = n - vw; k < n; k++) acc += (this.xs[k] - m) * (this.xs[k] - m);
      variance = acc / vw;
    }
    const dw = Math.min(dw0, n);
    let detrended = 0.0;
    if (dw >= 1) {
      let m = 0.0;
      for (let k = n - dw; k < n; k++) m += this.xs[k];
      detrended = this.x - m / dw;
    }
    const f: SampleFeatures = {
      t_ms: tMs,
      raw,
      valid,
      health,
      x: this.x,
      smoothed: this.smoothed,
      baseline: this.baseline,
      baselineFrozen: freezeBaseline,
      delta: this.initialized ? this.x - this.baseline : 0.0,
      rate,
      variance,
      detrended,
    };
    this.last = f;
    return f;
  }
}
