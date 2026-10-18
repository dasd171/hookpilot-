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

/** Safety limits: firmware SafetyLimits (config.h), same names and defaults as evidence_service.safety. */
export interface SafetyLimits {
  /** OVER_TENSION while retrieving (and retrieval request denied) above this (UI slider). */
  retrieval_force_limit_n: number;
  /** absolute tension limit in ANY state. */
  hard_max_tension_n: number;
  /** consecutive samples above a limit before OVER_TENSION latches. */
  over_tension_samples: number;
  sensor_min_n: number;
  /** raw >= sensor_max_n is out of range (the synth's 49.99 N saturation is flagged). */
  sensor_max_n: number;
  /** identical consecutive raw readings -> sensor stuck. */
  stuck_samples: number;
  /** consecutive invalid samples before a sensor fault latches. */
  sensor_fault_samples: number;
  hook_set_timeout_ms: number;
  retrieval_timeout_ms: number;
  hook_set_travel_min_mm: number;
  hook_set_travel_limit_mm: number;
  hook_set_max_speed_mm_s: number;
  retrieval_travel_limit_m: number;
  retrieval_max_speed_m_s: number;
  stall_current_a: number;
  stall_window_ms: number;
  stall_min_progress_m: number;
  stall_grace_ms: number;
  /** heartbeat age above this -> COMM_LOSS (latched only while armed). */
  comm_timeout_ms: number;
  /** minimum dead time between hook-set stop and retrieval start. */
  hook_to_retrieval_gap_ms: number;
  undervoltage_v: number;
  undervoltage_rearm_v: number;
  undervoltage_debounce_ms: number;
  over_temp_c: number;
}

/** Actuator commands: firmware ActuatorParams. */
export interface ActuatorParams {
  hook_set_target_mm: number;
  hook_set_speed_mm_s: number;
  hook_set_position_tol_mm: number;
  retrieval_target_m: number;
  retrieval_speed_m_s: number;
  /** VERIFY dwell after retrieval before the cycle closes. */
  verify_ms: number;
}

/** SIMULATION-only coupling of the host rig (firmware SimCoupling). Not a physical model. */
export interface SimCoupling {
  hook_set_line_load_n: number;
  retrieval_line_load_n: number;
  retrieval_current_base_a: number;
  retrieval_current_per_n_a: number;
}

export interface EngineConfig {
  classifier: ClassifierThresholds;
  safety: SafetyLimits;
  actuators: ActuatorParams;
  coupling: SimCoupling;
  /** IDLE (disarmed): classify + log, never request actuation. */
  observe_when_disarmed: boolean;
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
      over_tension_samples: 2,
      sensor_min_n: -5.0,
      sensor_max_n: 49.9,
      stuck_samples: 15,
      sensor_fault_samples: 3,
      hook_set_timeout_ms: 500,
      retrieval_timeout_ms: 4000,
      hook_set_travel_min_mm: 0.0,
      hook_set_travel_limit_mm: 15.0,
      hook_set_max_speed_mm_s: 80.0,
      retrieval_travel_limit_m: 1.5,
      retrieval_max_speed_m_s: 0.6,
      stall_current_a: 1.8,
      stall_window_ms: 200,
      stall_min_progress_m: 0.005,
      stall_grace_ms: 200,
      comm_timeout_ms: 300,
      hook_to_retrieval_gap_ms: 100,
      undervoltage_v: 10.5,
      undervoltage_rearm_v: 10.8,
      undervoltage_debounce_ms: 200,
      over_temp_c: 70.0,
    },
    actuators: {
      hook_set_target_mm: 12.0,
      hook_set_speed_mm_s: 60.0,
      hook_set_position_tol_mm: 0.5,
      retrieval_target_m: 1.0,
      retrieval_speed_m_s: 0.5,
      verify_ms: 500,
    },
    coupling: {
      hook_set_line_load_n: 1.5,
      retrieval_line_load_n: 0.6,
      retrieval_current_base_a: 0.3,
      retrieval_current_per_n_a: 0.1,
    },
    observe_when_disarmed: true,
  };
}

/**
 * `--set name=value` semantics shared with firmware sim_main.cpp and evidence_service.engine.apply_overrides.
 * sensor_min_n / sensor_max_n / stuck_samples live in SafetyLimits (the processor reads them from there).
 */
export function applyOverrides(c: EngineConfig, overrides: Record<string, number> | undefined): EngineConfig {
  const out = cloneConfig(c);
  if (!overrides) return out;
  const INT = new Set(['confirm_min_ms', 'max_osc', 'max_duration_ms', 'decision_window_ms', 'rearm_quiet_ms', 'over_tension_samples', 'sensor_fault_samples', 'hook_set_timeout_ms', 'retrieval_timeout_ms', 'stall_window_ms', 'stall_grace_ms', 'comm_timeout_ms', 'hook_to_retrieval_gap_ms', 'undervoltage_debounce_ms', 'verify_ms', 'stuck_samples', 'rate_lag_samples', 'variance_window_samples', 'detrend_window_samples']);
  for (const [k, v0] of Object.entries(overrides)) {
    const v = INT.has(k) ? Math.trunc(v0) : v0;
    let hit = false;
    for (const grp of [out.classifier, out.safety, out.actuators, out.coupling] as unknown as Array<Record<string, number>>) {
      if (k in grp) {
        grp[k] = v;
        hit = true;
        break;
      }
    }
    if (!hit) throw new Error(`unknown parameter ${k}`);
  }
  return out;
}

export function cloneConfig(c: EngineConfig): EngineConfig {
  return {
    classifier: { ...c.classifier },
    safety: { ...c.safety },
    actuators: { ...c.actuators },
    coupling: { ...c.coupling },
    observe_when_disarmed: c.observe_when_disarmed,
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
