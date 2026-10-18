#include "hookpilot/safety_controller.h"

#include <cmath>
#include <cstdio>
#include <string>

namespace hp {

namespace {
std::string joinFaults(uint32_t m) {
  std::string s;
  for (const auto& f : faultList(m)) {
    if (!s.empty()) s += ",";
    s += f;
  }
  return s;
}
std::string num(double v) {
  char b[48];
  std::snprintf(b, sizeof(b), "%.3f", v);
  return b;
}
}  // namespace

uint32_t DefaultSafetyController::evaluate(const SafetyInputs& in) {
  uint32_t c = 0;
  const auto& s = in.sample;

  if (in.estop_active) c |= FAULT_E_STOP;

  // Sensor validity (debounced)
  if (!s.valid) {
    ++sensor_bad_count_;
    last_bad_health_ = s.health;
  } else {
    sensor_bad_count_ = 0;
  }
  if (sensor_bad_count_ >= lim_.sensor_fault_samples) {
    switch (last_bad_health_) {
      case SensorHealth::OUT_OF_RANGE: c |= FAULT_SENSOR_OUT_OF_RANGE; break;
      case SensorHealth::STUCK: c |= FAULT_SENSOR_STUCK; break;
      default: c |= FAULT_SENSOR_DISCONNECTED; break;
    }
  }

  // Force limits (only on valid readings)
  bool over = false;
  if (s.valid) {
    if (s.raw > lim_.hard_max_tension_n) over = true;
    if (in.retrieval.active && s.raw > lim_.retrieval_force_limit_n) over = true;
  }
  over_tension_count_ = over ? over_tension_count_ + 1 : 0;
  if (over_tension_count_ >= lim_.over_tension_samples) c |= FAULT_OVER_TENSION;

  // Actuator timeouts
  if (in.hook.active) {
    if (!hook_was_active_) hook_since_ = in.now_ms;
    if (in.now_ms - hook_since_ > lim_.hook_set_timeout_ms) c |= FAULT_ACTUATOR_TIMEOUT;
  } else if (hook_was_active_) {
    hook_ran_ = true;
    hook_idle_since_ = in.now_ms;
  }
  hook_was_active_ = in.hook.active;
  if (in.retrieval.active) {
    if (!retr_was_active_) {
      retr_since_ = in.now_ms;
      hist_n_ = hist_head_ = 0;
    }
    if (in.now_ms - retr_since_ > lim_.retrieval_timeout_ms) c |= FAULT_ACTUATOR_TIMEOUT;
  }
  retr_was_active_ = in.retrieval.active;

  // Travel limits (position feedback)
  if (in.hook.position_valid &&
      (in.hook.position > lim_.hook_set_travel_limit_mm || in.hook.position < lim_.hook_set_travel_min_mm - 1.0))
    c |= FAULT_TRAVEL_LIMIT;
  if (in.retrieval.position_valid && in.retrieval.position > lim_.retrieval_travel_limit_m) c |= FAULT_TRAVEL_LIMIT;

  // Stall detection: little progress over stall_window while energised and drawing high current
  if (in.retrieval.active) {
    hist_[hist_head_] = {in.now_ms, in.retrieval.position};
    hist_head_ = (hist_head_ + 1) % kHist;
    if (hist_n_ < kHist) ++hist_n_;
    if (in.now_ms - retr_since_ >= lim_.stall_grace_ms) {
      // find oldest sample within window
      double progress = -1;
      double progress_long = -1;
      for (int k = 0; k < hist_n_; ++k) {
        int idx = (hist_head_ - 1 - k + kHist) % kHist;
        const uint32_t age = in.now_ms - hist_[idx].t;
        if (age >= lim_.stall_window_ms && progress < 0) progress = in.retrieval.position - hist_[idx].pos;
        if (age >= 3 * lim_.stall_window_ms && progress_long < 0)
          progress_long = in.retrieval.position - hist_[idx].pos;
      }
      const bool high_current = !in.retrieval.current_valid || in.retrieval.current_a >= lim_.stall_current_a;
      if (progress >= 0 && progress < lim_.stall_min_progress_m && high_current) c |= FAULT_MOTOR_STALL;
      if (progress_long >= 0 && progress_long < lim_.stall_min_progress_m) c |= FAULT_MOTOR_STALL;
    }
  }

  // Mutual exclusion must hold physically too
  if (in.hook.active && in.retrieval.active) c |= FAULT_INTERLOCK;

  // Comm heartbeat
  if (!in.comm_link_ok || in.now_ms - in.last_heartbeat_ms > lim_.comm_timeout_ms) c |= FAULT_COMM_LOSS;

  // Undervoltage (debounced)
  if (in.bus_voltage_v < lim_.undervoltage_v) {
    if (!uv_pending_) {
      uv_pending_ = true;
      uv_since_ = in.now_ms;
    }
    if (in.now_ms - uv_since_ >= lim_.undervoltage_debounce_ms) c |= FAULT_UNDERVOLTAGE;
  } else {
    uv_pending_ = false;
  }

  if (in.temperature_c > lim_.over_temp_c) c |= FAULT_OVER_TEMP;
  return c;
}

void DefaultSafetyController::stampLatches(uint32_t newly, uint32_t now) {
  for (int b = 0; b < kFaultBitCount; ++b)
    if (newly & (1u << b)) latch_t_[b] = now;
}

uint32_t DefaultSafetyController::latchTimeMs(uint32_t bit) const {
  for (int b = 0; b < kFaultBitCount; ++b)
    if (bit == (1u << b)) return latch_t_[b];
  return 0;
}

uint32_t DefaultSafetyController::update(const SafetyInputs& in) {
  last_now_ = in.now_ms;
  active_ = evaluate(in);
  uint32_t latch = active_;
  if (!armed_) latch &= ~FAULT_COMM_LOSS;  // comm loss is only critical while armed (documented)
  stampLatches(latch & ~latched_, in.now_ms);
  latched_ |= latch;
  if (latched_) armed_ = false;
  return latched_;
}

SafetyDecision DefaultSafetyController::permit(const ActuatorRequest& req, const SafetyInputs& in) {
  SafetyDecision d;
  auto deny = [&](const std::string& why) {
    d.permitted = false;
    d.reason = why;
    return d;
  };
  if (!armed_) return deny("deny: not armed");
  if (latched_) return deny("deny: latched faults " + joinFaults(latched_));
  if (active_) return deny("deny: active conditions " + joinFaults(active_));
  if (!in.sample.valid) return deny(std::string("deny: sensor not valid (") + toString(in.sample.health) + ")");
  if (in.sample.raw > lim_.hard_max_tension_n) return deny("deny: tension above hard_max_tension_n");

  if (req.actuator == ActuatorId::HOOK_SET) {
    if (in.retrieval.active) return deny("deny: mutual exclusion (retrieval active)");
    if (!(req.target >= lim_.hook_set_travel_min_mm && req.target <= lim_.hook_set_travel_limit_mm))
      return deny("deny: hook_set target " + num(req.target) + " mm outside travel limits");
    if (!(req.speed > 0 && req.speed <= lim_.hook_set_max_speed_mm_s))
      return deny("deny: hook_set speed " + num(req.speed) + " mm/s outside limits");
  } else {
    if (in.hook.active) return deny("deny: mutual exclusion (hook_set active)");
    if (hook_ran_ && in.now_ms - hook_idle_since_ < lim_.hook_to_retrieval_gap_ms)
      return deny("deny: hook_set stopped " + std::to_string(in.now_ms - hook_idle_since_) +
                  " ms ago (< hook_to_retrieval_gap_ms)");
    if (!(req.target > 0 && req.target <= lim_.retrieval_travel_limit_m))
      return deny("deny: retrieval target " + num(req.target) + " m outside travel limits");
    if (!(req.speed > 0 && req.speed <= lim_.retrieval_max_speed_m_s))
      return deny("deny: retrieval speed " + num(req.speed) + " m/s outside limits");
    if (in.sample.raw > lim_.retrieval_force_limit_n)
      return deny("deny: tension " + num(in.sample.raw) + " N above retrieval_force_limit_n");
  }
  d.permitted = true;
  d.reason = "permit: all interlocks clear";
  return d;
}

bool DefaultSafetyController::arm(const SafetyInputs& in, std::string* why) {
  auto no = [&](const std::string& r) {
    if (why) *why = r;
    return false;
  };
  if (latched_) return no("latched faults " + joinFaults(latched_) + " (explicit reset required)");
  if (active_) return no("active conditions " + joinFaults(active_));
  if (!in.sample.valid) return no("sensor not valid");
  if (in.bus_voltage_v < lim_.undervoltage_rearm_v) return no("bus voltage below undervoltage_rearm_v");
  if (in.estop_active) return no("e-stop active");
  if (in.hook.active || in.retrieval.active) return no("actuator active");
  armed_ = true;
  if (why) *why = "armed";
  return true;
}

bool DefaultSafetyController::clearFaults(const SafetyInputs& in) {
  (void)in;
  // WATCHDOG_RESET is an acknowledgement-only fault; everything else must be physically gone.
  latched_ &= active_ & ~FAULT_WATCHDOG_RESET;
  return latched_ == 0;
}

SafetyDecision ActuatorGate::request(const ActuatorRequest& req, const SafetyInputs& in) {
  SafetyDecision d = safety_.permit(req, in);
  // Defense in depth: re-check mutual exclusion from live feedback
  if (d.permitted) {
    if (req.actuator == ActuatorId::HOOK_SET && retr_.feedback().active) {
      d = {false, "deny: gate mutual exclusion (retrieval active)"};
    } else if (req.actuator == ActuatorId::RETRIEVAL && hook_.feedback().active) {
      d = {false, "deny: gate mutual exclusion (hook_set active)"};
    }
  }
  if (!d.permitted) {
    ++denied_;
    return d;
  }
  ++issued_;
  if (req.actuator == ActuatorId::HOOK_SET) hook_.moveTo(req.target, req.speed);
  else retr_.start(req.target, req.speed);
  return d;
}

void ActuatorGate::stopAll() {
  hook_.stop();
  retr_.stop();
}

}  // namespace hp
