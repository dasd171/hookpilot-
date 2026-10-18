// HookPilot firmware configuration.
//
// EVERY numeric value in this file is a configurable PLACEHOLDER:
//   "provisional - derive from calibration + labeled data".
// None of them has been validated on hardware or against labeled bite data.
#pragma once

#include <cstdint>

namespace hp {

constexpr const char* kProvisionalLabel = "provisional - derive from calibration + labeled data";
constexpr const char* kClassifierId = "deterministic-baseline-v1";
constexpr const char* kFirmwareVersion = "0.1.0-dev";
constexpr double kSampleRateHz = 50.0;
constexpr uint32_t kDtMs = 20;

struct ClassifierThresholds {
  double trigger_delta_n = 1.0;          // delta >= this opens CANDIDATE
  uint32_t confirm_min_ms = 300;         // continuous above-trigger run (from trigger) required
  double min_rate_n_per_s = 2.0;         // peak smoothed rate required (current drift is slow)
  int max_osc = 4;                       // max Schmitt zero-crossings of detrended signal in window
  double manual_handling_delta_n = 5.0;  // delta above this -> manual handling (immediate)
  uint32_t max_duration_ms = 8000;       // above-trigger duration above this -> manual handling
  uint32_t decision_window_ms = 600;     // observation window from trigger before deciding
  double osc_band_n = 0.25;              // Schmitt band for oscillation counting
  double baseline_alpha = 0.002;         // baseline EMA per 20 ms sample (tau ~10 s)
  double smoothing_alpha = 0.35;         // EMA used for rate
  int rate_lag_samples = 5;              // rate = (s[t]-s[t-lag]) / (lag*dt)
  int variance_window_samples = 25;
  int detrend_window_samples = 5;
  double release_ratio = 0.5;            // after a decision delta must fall below trigger*ratio ...
  uint32_t rearm_quiet_ms = 300;         // ... for this long before a new candidate
};

struct SafetyLimits {
  double retrieval_force_limit_n = 8.0;  // while retrieving (or requesting retrieval)
  double hard_max_tension_n = 20.0;      // any state
  int over_tension_samples = 2;          // consecutive samples above a limit before fault
  double sensor_min_n = -5.0;
  double sensor_max_n = 49.9;            // >= this is treated as saturation / out of range
  int stuck_samples = 15;                // identical consecutive raw readings -> stuck
  int sensor_fault_samples = 3;          // consecutive invalid samples before a sensor fault
  uint32_t hook_set_timeout_ms = 500;
  uint32_t retrieval_timeout_ms = 4000;
  double hook_set_travel_min_mm = 0.0;
  double hook_set_travel_limit_mm = 15.0;
  double hook_set_max_speed_mm_s = 80.0;
  double retrieval_travel_limit_m = 1.5;
  double retrieval_max_speed_m_s = 0.6;
  double stall_current_a = 1.8;
  uint32_t stall_window_ms = 200;
  double stall_min_progress_m = 0.005;
  uint32_t stall_grace_ms = 200;          // after start before stall evaluation
  uint32_t comm_timeout_ms = 300;        // heartbeat age above this -> COMM_LOSS (latched only while armed)
  uint32_t hook_to_retrieval_gap_ms = 100; // minimum dead time between hook-set stop and retrieval start
  double undervoltage_v = 10.5;           // 3S Li-ion pack, ~3.5 V/cell
  double undervoltage_rearm_v = 10.8;     // hysteresis for arming
  uint32_t undervoltage_debounce_ms = 200;
  double over_temp_c = 70.0;
};

struct ActuatorParams {
  double hook_set_target_mm = 12.0;
  double hook_set_speed_mm_s = 60.0;
  double hook_set_position_tol_mm = 0.5;
  double retrieval_target_m = 1.0;
  double retrieval_speed_m_s = 0.5;
  uint32_t verify_ms = 500;  // VERIFY dwell after retrieval before the cycle closes
};

// Simulation-only coupling used by the host rig (and by the Python / TS ports): line tension the
// actuators add to the load-cell reading while energised. NOT a physical model.
struct SimCoupling {
  double hook_set_line_load_n = 1.5;
  double retrieval_line_load_n = 0.6;
  double retrieval_current_base_a = 0.3;     // retrieval motor current = base + per_n * max(0, x_prev)
  double retrieval_current_per_n_a = 0.1;
};

struct DeviceIdentity {
  const char* device_id = "hookpilot-sim-001";
  const char* hardware_revision = "SIMULATION (no hardware)";
};

struct ControllerConfig {
  ClassifierThresholds classifier;
  SafetyLimits safety;
  ActuatorParams actuators;
  DeviceIdentity identity;
  uint32_t preroll_ms = 500;     // samples kept before the trigger in the event record
  int max_record_samples = 400;  // cap on inlined samples per record
  int max_record_feedback = 60;  // cap on actuator feedback entries per record
  bool observe_when_disarmed = true;  // IDLE (disarmed): classify + log, never request actuation
};

}  // namespace hp
