/**
 * HookPilot shared types (schema_version 1.0.0).
 * Mirrors packages/event-schema/hookpilot-event.schema.json and docs/SHARED_CONTRACT.md.
 * Types plus a few constant lists (STATES, SCENARIOS); no other runtime code.
 */

export const STATES = [
  'IDLE',
  'MONITOR',
  'CANDIDATE',
  'CONFIRM',
  'HOOK_SET',
  'RETRIEVE',
  'VERIFY',
  'SAFE_STOP',
] as const;
export type MachineState = (typeof STATES)[number];

export type SourceMode = 'simulation' | 'bench' | 'field';
export type ClockQuality = 'ntp_synced' | 'gps' | 'rtc_unsynced' | 'host_clock_simulated';
export type SensorHealth = 'ok' | 'degraded' | 'disconnected' | 'out_of_range' | 'stuck';

export type ClassificationResult =
  | 'bite_candidate_confirmed'
  | 'rejected_transient'
  | 'rejected_noise'
  | 'rejected_vibration'
  | 'rejected_manual_handling'
  | 'rejected_sensor_fault'
  | 'no_event';

export type SafetyDecision = 'permit' | 'deny' | 'safe_stop';

export type FaultCode =
  | 'E_STOP'
  | 'OVER_TENSION'
  | 'SENSOR_DISCONNECTED'
  | 'SENSOR_OUT_OF_RANGE'
  | 'SENSOR_STUCK'
  | 'MOTOR_STALL'
  | 'ACTUATOR_TIMEOUT'
  | 'TRAVEL_LIMIT'
  | 'COMM_LOSS'
  | 'UNDERVOLTAGE'
  | 'WATCHDOG_RESET'
  | 'OVER_TEMP'
  | 'INTERLOCK';

export type ActuatorName = 'hook_set' | 'retrieval';
export type FinalState = 'MONITOR' | 'SAFE_STOP' | 'IDLE';
export type FishLanded = 'unknown' | 'confirmed_by_observer' | 'not_landed';

export interface ActuationRequest {
  actuator: ActuatorName;
  command: string;
  limit?: Record<string, number>;
  t_ms?: number;
  /** safety controller decision on this request (firmware, Python and web records) */
  decision?: 'permit' | 'deny';
  decision_reason?: string;
}

export interface ActuationFeedback {
  actuator: string;
  t_ms?: number;
  position?: number | null;
  current_a?: number | null;
  tension_n?: number | null;
  status?: string;
}

export interface HookPilotEvent {
  schema_version: '1.0.0';
  event_id: string;
  device: { device_id: string; firmware_version: string; hardware_revision?: string };
  source: {
    mode: SourceMode;
    scenario?: string;
    seed?: number;
    ground_truth_label?: string | null;
    /** synthetic generator id, e.g. "hookpilot-synth-v1" (same field in firmware, Python and web records) */
    generator?: string;
  };
  timestamp: {
    start_utc: string;
    end_utc?: string;
    monotonic_ms?: number;
    clock_quality: ClockQuality;
  };
  calibration: {
    calibration_version: string;
    scale_n_per_count?: number;
    offset_counts?: number;
    calibrated_utc?: string | null;
  };
  sensor_data: {
    sample_rate_hz: number;
    units: 'N';
    samples?: Array<[number, number]>;
    raw_data_ref?: string | null;
    raw_data_sha256?: string | null;
    /** non-finite readings inside the record window that were not inlined */
    invalid_samples_omitted?: number;
  };
  features: {
    baseline_n?: number;
    peak_delta_n?: number;
    max_rate_n_per_s?: number;
    duration_above_ms?: number;
    continuous_above_ms?: number;
    short_term_variance?: number;
    oscillation_count?: number;
    sensor_health?: SensorHealth;
  };
  classification: {
    result: ClassificationResult;
    reason: string;
    rule_trace?: string[];
    classifier?: string;
    implementation?: string;
    thresholds?: Record<string, number | string>;
    decision_t_ms?: number;
    trigger_t_ms?: number;
  };
  safety: {
    decision: SafetyDecision;
    armed?: boolean;
    interlocks?: Record<string, boolean | string | number>;
    faults?: string[];
    /** fault -> t_ms of the tick it latched (latched faults only) */
    fault_latched_t_ms?: Record<string, number>;
  };
  actuation: {
    requested?: ActuationRequest[];
    feedback?: ActuationFeedback[];
    simulated?: boolean;
  };
  outcome: {
    final_state: FinalState;
    stop_reason: string;
    milestones: {
      bite_detected: boolean;
      hook_set_completed: boolean;
      retrieval_completed: boolean;
      fish_landed: FishLanded;
    };
    test_outcome?: 'pass' | 'fail' | 'not_evaluated';
  };
  integrity: {
    hash_algorithm: 'sha256';
    record_sha256: string;
    prev_record_sha256?: string | null;
    signature?: string | null;
  };
}

export const SCENARIOS = [
  'bite',
  'noise_current',
  'rod_vibration',
  'manual_handling',
  'excessive_tension',
  'sensor_failure',
  'comm_loss',
  'motor_stall',
] as const;
export type ScenarioName = (typeof SCENARIOS)[number];
