#include "hookpilot/types.h"

namespace hp {

const char* toString(State s) {
  switch (s) {
    case State::IDLE: return "IDLE";
    case State::MONITOR: return "MONITOR";
    case State::CANDIDATE: return "CANDIDATE";
    case State::CONFIRM: return "CONFIRM";
    case State::HOOK_SET: return "HOOK_SET";
    case State::RETRIEVE: return "RETRIEVE";
    case State::VERIFY: return "VERIFY";
    case State::SAFE_STOP: return "SAFE_STOP";
  }
  return "?";
}

const char* toString(SensorHealth h) {
  switch (h) {
    case SensorHealth::OK: return "ok";
    case SensorHealth::DEGRADED: return "degraded";
    case SensorHealth::DISCONNECTED: return "disconnected";
    case SensorHealth::OUT_OF_RANGE: return "out_of_range";
    case SensorHealth::STUCK: return "stuck";
  }
  return "?";
}

const char* toString(ClassResult r) {
  switch (r) {
    case ClassResult::NO_EVENT: return "no_event";
    case ClassResult::BITE_CANDIDATE_CONFIRMED: return "bite_candidate_confirmed";
    case ClassResult::REJECTED_TRANSIENT: return "rejected_transient";
    case ClassResult::REJECTED_NOISE: return "rejected_noise";
    case ClassResult::REJECTED_VIBRATION: return "rejected_vibration";
    case ClassResult::REJECTED_MANUAL_HANDLING: return "rejected_manual_handling";
    case ClassResult::REJECTED_SENSOR_FAULT: return "rejected_sensor_fault";
  }
  return "?";
}

const char* toString(ActuatorId a) { return a == ActuatorId::HOOK_SET ? "hook_set" : "retrieval"; }

const char* toString(Decision d) {
  switch (d) {
    case Decision::PERMIT: return "permit";
    case Decision::DENY: return "deny";
    case Decision::SAFE_STOP: return "safe_stop";
  }
  return "?";
}

const char* faultName(uint32_t bit) {
  switch (bit) {
    case FAULT_E_STOP: return "E_STOP";
    case FAULT_OVER_TENSION: return "OVER_TENSION";
    case FAULT_SENSOR_DISCONNECTED: return "SENSOR_DISCONNECTED";
    case FAULT_SENSOR_OUT_OF_RANGE: return "SENSOR_OUT_OF_RANGE";
    case FAULT_SENSOR_STUCK: return "SENSOR_STUCK";
    case FAULT_MOTOR_STALL: return "MOTOR_STALL";
    case FAULT_ACTUATOR_TIMEOUT: return "ACTUATOR_TIMEOUT";
    case FAULT_TRAVEL_LIMIT: return "TRAVEL_LIMIT";
    case FAULT_COMM_LOSS: return "COMM_LOSS";
    case FAULT_UNDERVOLTAGE: return "UNDERVOLTAGE";
    case FAULT_WATCHDOG_RESET: return "WATCHDOG_RESET";
    case FAULT_OVER_TEMP: return "OVER_TEMP";
    case FAULT_INTERLOCK: return "INTERLOCK";
    default: return "UNKNOWN";
  }
}

std::vector<std::string> faultList(uint32_t mask) {
  std::vector<std::string> out;
  for (int i = 0; i < kFaultBitCount; ++i) {
    const uint32_t b = 1u << i;
    if (mask & b) out.emplace_back(faultName(b));
  }
  return out;
}

}  // namespace hp
