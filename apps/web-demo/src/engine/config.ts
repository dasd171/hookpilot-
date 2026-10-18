/**
 * Engine configuration. EVERY numeric threshold below is a configurable PLACEHOLDER:
 * "provisional - derive from calibration + labeled data". None of them is validated.
 *
 * Classifier thresholds and sensor limits are the firmware defaults
 * (firmware/include/hookpilot/config.h, mirrored by evidence_service/classifier.py FW_DEFAULTS).
 * All three implementations share these values; change them in all three or not at all.
 */
export const PROVISIONAL_LABEL = 'provisional - derive from calibration + labeled data';
export const CLASSIFIER_ID = 'deterministic-baseline-v1';
/** recorded in classification.implementation (Python: python-port-of-firmware-cpp@2026-10-18) */
export const CLASSIFIER_IMPLEMENTATION = 'ts-port-of-firmware-cpp@2026-10-18';
export const SAMPLE_RATE_HZ = 50;
export const DT_MS = 1000 / SAMPLE_RATE_HZ; // 20 ms

export interface ClassifierThresholds {
  /** delta >= trigger_delta_n opens a CANDIDATE (UI "bite threshold"). */
  trigger_delta_n: number;
  /** continuous above-trigger run (from the trigger sample) required for a bite. */
  confirm_min_ms: number;
  /** peak smoothed rate required (current drift is slow). */
  min_rate_n_per_s: number;
  /** max Schmitt zero-crossings of the detrended signal in the window. */
  max_osc: number;
  /** delta above this -> manual handling (immediate decision). */
  manual_handling_delta_n: number;
  /** above-trigger duration above this -> manual handling. */
  max_duration_ms: number;
  /** observation window from the trigger before deciding. */
  decision_window_ms: number;
  /** Schmitt band for oscillation counting. */
  osc_band_n: number;
  /** baseline EMA per 20 ms sample (tau ~10 s). Frozen while a candidate/cycle is open. */
  baseline_alpha: number;
  /** EMA used for the rate. */
  smoothing_alpha: number;
  /** rate = (s[t] - s[t-lag]) / (lag*dt) over valid samples. */
  rate_lag_samples: number;
  variance_window_samples: number;
  detrend_window_samples: number;
  /** after a decision, delta must fall below trigger*release_ratio ... */
  release_ratio: number;
  /** ... for this long before a new candidate. */
  rearm_quiet_ms: number;
}

export interface SafetyLimits {
  /** retrieval aborts (SAFE_STOP, OVER_TENSION) if line tension exceeds this while retrieving (UI slider). */
  retrieval_force_limit_n: number;
  /** absolute tension limit in ANY state -> SAFE_STOP. */
  hard_max_tension_n: number;
  sensor_min_n: number;
  /** raw >= sensor_max_n is out of range (the synth's 49.99 N saturation is flagged). */
  sensor_max_n: number;
  /** identical consecutive raw readings -> sensor stuck. */
  stuck_samples: number;
  hook_set_timeout_ms: number;
  retrieval_timeout_ms: number;
  hook_set_travel_limit_mm: number;
  stall_current_a: number;
  stall_window_ms: number;
  stall_min_progress_m: number;
  comm_timeout_ms: number;
  undervoltage_v: number;
}

export interface ActuatorParams {
  hook_set_target_mm: number;
  hook_set_speed_mm_per_s: number;
  hook_set_line_load_n: number;
  retrieval_target_m: number;
  retrieval_speed_m_per_s: number;
  retrieval_line_load_n: number;
}

export interface EngineConfig {
  classifier: ClassifierThresholds;
  safety: SafetyLimits;
  actuators: ActuatorParams;
}

export function defaultConfig(): EngineConfig {
  return {
    classifier: {
      trigger_delta_n: 1.0,
      confirm_min_ms: 300,
      min_rate_n_per_s: 2.0,
      max_osc: 4,
      manual_handling_delta_n: 5.0,
      max_duration_ms: 8000,
      decision_window_ms: 600,
      osc_band_n: 0.25,
      baseline_alpha: 0.002,
      smoothing_alpha: 0.35,
      rate_lag_samples: 5,
      variance_window_samples: 25,
      detrend_window_samples: 5,
      release_ratio: 0.5,
      rearm_quiet_ms: 300,
    },
    safety: {
      retrieval_force_limit_n: 8.0,
      hard_max_tension_n: 20.0,
      sensor_min_n: -5.0,
      sensor_max_n: 49.9,
      stuck_samples: 15,
      hook_set_timeout_ms: 500,
      retrieval_timeout_ms: 4000,
      hook_set_travel_limit_mm: 15,
      stall_current_a: 1.8,
      stall_window_ms: 200,
      stall_min_progress_m: 0.005,
      comm_timeout_ms: 300,
      undervoltage_v: 10.5,
    },
    actuators: {
      hook_set_target_mm: 12,
      hook_set_speed_mm_per_s: 60,
      hook_set_line_load_n: 1.5,
      retrieval_target_m: 1.0,
      retrieval_speed_m_per_s: 0.5,
      retrieval_line_load_n: 0.6,
    },
  };
}

export function cloneConfig(c: EngineConfig): EngineConfig {
  return {
    classifier: { ...c.classifier },
    safety: { ...c.safety },
    actuators: { ...c.actuators },
  };
}

/**
 * classification.thresholds block, same keys as the Python port's Thresholds.to_record() (which replay
 * reads back). Values are written unrounded so replay uses exactly what the classifier used.
 */
export function thresholdsRecord(th: ClassifierThresholds, lim: SafetyLimits): Record<string, number | string> {
  return {
    variant: 'fw',
    trigger_delta_n: th.trigger_delta_n,
    confirm_min_ms: th.confirm_min_ms,
    min_rate_n_per_s: th.min_rate_n_per_s,
    max_osc: th.max_osc,
    manual_handling_delta_n: th.manual_handling_delta_n,
    max_duration_ms: th.max_duration_ms,
    decision_window_ms: th.decision_window_ms,
    osc_band_n: th.osc_band_n,
    baseline_alpha: th.baseline_alpha,
    smoothing_alpha: th.smoothing_alpha,
    rate_lag_samples: th.rate_lag_samples,
    variance_window_samples: th.variance_window_samples,
    detrend_window_samples: th.detrend_window_samples,
    release_ratio: th.release_ratio,
    rearm_quiet_ms: th.rearm_quiet_ms,
    sensor_min_n: lim.sensor_min_n,
    sensor_max_n: lim.sensor_max_n,
    stuck_samples: lim.stuck_samples,
    status: PROVISIONAL_LABEL,
  };
}
