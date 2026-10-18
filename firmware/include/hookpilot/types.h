// HookPilot firmware - shared value types (hardware independent, C++17).
// Units: tension N, time ms, actuator travel: hook set mm, retrieval m (see docs/SHARED_CONTRACT.md).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hp {

// ---- State machine (names identical to every other HookPilot module) ----
enum class State : uint8_t { IDLE, MONITOR, CANDIDATE, CONFIRM, HOOK_SET, RETRIEVE, VERIFY, SAFE_STOP };
const char* toString(State s);

enum class SensorHealth : uint8_t { OK, DEGRADED, DISCONNECTED, OUT_OF_RANGE, STUCK };
const char* toString(SensorHealth h);

enum class ClassResult : uint8_t {
  NO_EVENT,
  BITE_CANDIDATE_CONFIRMED,
  REJECTED_TRANSIENT,
  REJECTED_NOISE,
  REJECTED_VIBRATION,
  REJECTED_MANUAL_HANDLING,
  REJECTED_SENSOR_FAULT
};
const char* toString(ClassResult r);

// ---- Fault codes (bit mask). Strings match packages/shared-types FaultCode ----
enum Fault : uint32_t {
  FAULT_NONE = 0,
  FAULT_E_STOP = 1u << 0,
  FAULT_OVER_TENSION = 1u << 1,
  FAULT_SENSOR_DISCONNECTED = 1u << 2,
  FAULT_SENSOR_OUT_OF_RANGE = 1u << 3,
  FAULT_SENSOR_STUCK = 1u << 4,
  FAULT_MOTOR_STALL = 1u << 5,
  FAULT_ACTUATOR_TIMEOUT = 1u << 6,
  FAULT_TRAVEL_LIMIT = 1u << 7,
  FAULT_COMM_LOSS = 1u << 8,
  FAULT_UNDERVOLTAGE = 1u << 9,
  FAULT_WATCHDOG_RESET = 1u << 10,
  FAULT_OVER_TEMP = 1u << 11,
  FAULT_INTERLOCK = 1u << 12,  // hook-set and retrieval observed active together (must never happen)
};
constexpr int kFaultBitCount = 13;
const char* faultName(uint32_t single_bit);
std::vector<std::string> faultList(uint32_t mask);

// ---- Sensor ----
struct TensionReading {
  double newtons = 0.0;   // calibrated value; NaN allowed when invalid
  int32_t raw_counts = 0; // ADC counts (0 for mocks)
  bool valid = false;     // driver-level validity (timeout, saturation, NaN)
  SensorHealth health = SensorHealth::DISCONNECTED;
};

struct CalibrationInfo {
  std::string calibration_version = "uncalibrated";
  double scale_n_per_count = 0.0;
  double offset_counts = 0.0;
  std::string calibrated_utc;  // empty -> null
};

// Output of the SignalProcessor for one sample.
struct ProcessedSample {
  uint32_t t_ms = 0;
  double raw = 0.0;         // reading as received (may be NaN)
  bool valid = false;       // passed validity checks
  SensorHealth health = SensorHealth::DISCONNECTED;
  double x = 0.0;           // last valid value (held while invalid)
  double smoothed = 0.0;    // EMA used for rate
  double baseline = 0.0;    // EMA baseline (frozen during candidate / cycle)
  double delta = 0.0;       // x - baseline
  double rate = 0.0;        // N/s, from smoothed signal over rate_lag
  double variance = 0.0;    // short-term variance over window
  double detrended = 0.0;   // x - trailing moving average
  bool baseline_frozen = false;
};

struct Features {
  double baseline_n = 0.0;
  double peak_delta_n = 0.0;
  double max_rate_n_per_s = 0.0;
  double duration_above_ms = 0.0;    // total ms with delta >= trigger inside the window
  double continuous_above_ms = 0.0;  // run length from the trigger sample without dropping below trigger
  double short_term_variance = 0.0;
  int oscillation_count = 0;
  SensorHealth sensor_health = SensorHealth::OK;
};

struct Classification {
  ClassResult result = ClassResult::NO_EVENT;
  std::string reason;
  std::vector<std::string> rule_trace;
  Features features;
};

// ---- Actuators ----
enum class ActuatorId : uint8_t { HOOK_SET, RETRIEVAL };
const char* toString(ActuatorId a);

struct ActuatorFeedback {
  double position = 0.0;  // hook set: mm of stroke; retrieval: m of line retrieved
  double current_a = 0.0;
  double speed = 0.0;     // mm/s or m/s
  bool active = false;    // energised and moving toward a target
  bool at_target = false;
  bool position_valid = true;
  bool current_valid = true;
  std::string status = "idle";
};

struct ActuatorRequest {
  ActuatorId actuator = ActuatorId::HOOK_SET;
  std::string command;      // "stroke_to", "return_to_rest", "retrieve"
  double target = 0.0;      // mm or m
  double speed = 0.0;       // mm/s or m/s
  uint32_t t_ms = 0;
};

enum class Decision : uint8_t { PERMIT, DENY, SAFE_STOP };
const char* toString(Decision d);

struct SafetyDecision {
  bool permitted = false;
  std::string reason;
};

// Everything the SafetyController needs, gathered by the controller each tick.
// The safety controller never sees classifier output.
struct SafetyInputs {
  uint32_t now_ms = 0;
  ProcessedSample sample;
  ActuatorFeedback hook;
  ActuatorFeedback retrieval;
  double bus_voltage_v = 0.0;
  double temperature_c = 25.0;
  bool estop_active = false;
  bool comm_link_ok = true;
  uint32_t last_heartbeat_ms = 0;
};

}  // namespace hp
