/**
 * Engine configuration. EVERY numeric threshold below is a configurable PLACEHOLDER:
 * "provisional - derive from calibration + labeled data". None of them is validated.
 */
export const PROVISIONAL_LABEL = 'provisional - derive from calibration + labeled data';
export const CLASSIFIER_ID = 'deterministic-baseline-v1';
export const SAMPLE_RATE_HZ = 50;
export const DT_MS = 1000 / SAMPLE_RATE_HZ; // 20 ms

export interface ClassifierThresholds {
  /** delta >= trigger_delta_n opens a CANDIDATE (UI "bite threshold"). */
  trigger_delta_n: number;
  /** delta must stay >= trigger for at least this long (continuous, from trigger). */
  confirm_min_ms: number;
  /** peak rate of change (smoothed, positive) required for a bite. */
  min_rate_n_per_s: number;
  /** maximum oscillation count (hysteresis zero-crossings of detrended signal) during the window. */
  max_osc: number;
  /** delta above this is treated as manual handling. */
  manual_handling_delta_n: number;
  /** above-trigger duration longer than this is treated as manual handling. */
  max_duration_ms: number;
  /** Schmitt band for oscillation counting. */
  osc_band_n: number;
  /** EMA coefficient for baseline (per 20 ms sample). Frozen while a candidate/cycle is open. */
  baseline_alpha: number;
  /** EMA coefficient for the smoothed signal used for rate. */
  smoothing_alpha: number;
  /** rate = (smoothed[t] - smoothed[t - rate_lag]) / lag. */
  rate_lag_samples: number;
  /** rolling variance window (samples). */
  variance_window_samples: number;
  /** trailing moving-average length used for detrending (samples). */
  detrend_window_samples: number;
  /** after a decision, delta must stay below trigger*release_ratio for rearm_quiet_ms before a new candidate. */
  release_ratio: number;
  rearm_quiet_ms: number;
}

export interface SafetyLimits {
  /** retrieval aborts (SAFE_STOP, OVER_TENSION) if line tension exceeds this while retrieving (UI slider). */
  retrieval_force_limit_n: number;
  /** absolute tension limit in ANY state -> SAFE_STOP. */
  hard_max_tension_n: number;
  sensor_min_n: number;
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
      min_rate_n_per_s: 5.0,
      max_osc: 4,
      manual_handling_delta_n: 12.0,
      max_duration_ms: 8000,
      osc_band_n: 0.25,
      baseline_alpha: 0.02,
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
      sensor_max_n: 50.0,
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
