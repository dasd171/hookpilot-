#include "hookpilot/bite_detector.h"

#include <algorithm>
#include <cstdio>
#include <string>

namespace hp {

static std::string fmt(const char* f, double a, double b) {
  char buf[160];
  std::snprintf(buf, sizeof(buf), f, a, b);
  return buf;
}

bool DeterministicBaselineV1::shouldTrigger(const ProcessedSample& s) const {
  return s.valid && s.delta >= th_.trigger_delta_n;
}

void DeterministicBaselineV1::beginCandidate(const ProcessedSample& trig) {
  f_ = Features{};
  f_.baseline_n = trig.baseline;
  f_.sensor_health = trig.health;
  t_trigger_ = t_last_ = trig.t_ms;
  run_alive_ = true;
  schmitt_ = 0;
  early_manual_ = early_sensor_ = false;
  quiet_ = false;
  char buf[200];
  std::snprintf(buf, sizeof(buf), "t=%.0fms TRIGGER delta=%.3fN >= trigger_delta_n=%.3fN (baseline frozen at %.3fN)",
                static_cast<double>(trig.t_ms), trig.delta, th_.trigger_delta_n, trig.baseline);
  trigger_line_ = buf;
  update(trig);
}

bool DeterministicBaselineV1::update(const ProcessedSample& s) {
  const double dt = static_cast<double>(kDtMs);
  if (!s.valid) {
    f_.sensor_health = s.health;
    early_sensor_ = true;
    return true;
  }
  f_.peak_delta_n = std::max(f_.peak_delta_n, s.delta);
  f_.max_rate_n_per_s = std::max(f_.max_rate_n_per_s, s.rate);
  f_.short_term_variance = std::max(f_.short_term_variance, s.variance);
  const bool above = s.delta >= th_.trigger_delta_n;
  if (above) f_.duration_above_ms += dt;
  if (run_alive_) {
    if (above) f_.continuous_above_ms = static_cast<double>(s.t_ms - t_trigger_) + dt;
    else run_alive_ = false;
  }
  // Schmitt-trigger zero crossings of the detrended signal
  if (s.detrended >= th_.osc_band_n) {
    if (schmitt_ == -1) ++f_.oscillation_count;
    schmitt_ = 1;
  } else if (s.detrended <= -th_.osc_band_n) {
    if (schmitt_ == 1) ++f_.oscillation_count;
    schmitt_ = -1;
  }
  t_last_ = s.t_ms;
  if (s.delta > th_.manual_handling_delta_n) {
    early_manual_ = true;
    return true;
  }
  return (s.t_ms - t_trigger_) + kDtMs >= th_.decision_window_ms;
}

Classification DeterministicBaselineV1::decide() const {
  Classification c;
  c.features = f_;
  auto& tr = c.rule_trace;
  const auto& t = th_;
  if (!trigger_line_.empty()) tr.push_back(trigger_line_);
  tr.push_back(fmt("window: %.0f ms observed after trigger (decision_window_ms=%.0f)",
                   static_cast<double>(t_last_ - t_trigger_) + kDtMs, t.decision_window_ms));

  // R0 sensor health
  if (f_.sensor_health != SensorHealth::OK && f_.sensor_health != SensorHealth::DEGRADED) {
    tr.push_back(std::string("R0 sensor_health=") + toString(f_.sensor_health) + " != ok -> FAIL");
    c.result = ClassResult::REJECTED_SENSOR_FAULT;
    c.reason = std::string("Sensor health '") + toString(f_.sensor_health) +
               "' during candidate window; no decision can be trusted.";
    return c;
  }
  tr.push_back("R0 sensor_health=ok -> pass");

  // R1 manual handling: very large delta
  if (f_.peak_delta_n > t.manual_handling_delta_n) {
    tr.push_back(fmt("R1 peak_delta %.3f N > manual_handling_delta_n %.3f N -> FAIL", f_.peak_delta_n,
                     t.manual_handling_delta_n));
    c.result = ClassResult::REJECTED_MANUAL_HANDLING;
    c.reason = fmt("Peak delta %.3f N exceeds manual-handling threshold %.3f N (line handled by a person).",
                   f_.peak_delta_n, t.manual_handling_delta_n);
    return c;
  }
  tr.push_back(fmt("R1 peak_delta %.3f N <= manual_handling_delta_n %.3f N -> pass", f_.peak_delta_n,
                   t.manual_handling_delta_n));

  // R2 manual handling: very long duration
  if (f_.duration_above_ms > t.max_duration_ms) {
    tr.push_back(fmt("R2 duration_above %.0f ms > max_duration_ms %.0f -> FAIL", f_.duration_above_ms,
                     t.max_duration_ms));
    c.result = ClassResult::REJECTED_MANUAL_HANDLING;
    c.reason = "Above-trigger duration longer than max_duration_ms (treated as manual handling).";
    return c;
  }
  tr.push_back(fmt("R2 duration_above %.0f ms <= max_duration_ms %.0f -> pass", f_.duration_above_ms,
                   t.max_duration_ms));

  // R3 vibration: many oscillations
  if (f_.oscillation_count > t.max_osc) {
    tr.push_back(fmt("R3 oscillation_count %.0f > max_osc %.0f -> FAIL", f_.oscillation_count, t.max_osc));
    c.result = ClassResult::REJECTED_VIBRATION;
    c.reason = fmt("Oscillation count %.0f exceeds max_osc %.0f (rod/wave vibration, low mean pull).",
                   f_.oscillation_count, t.max_osc);
    return c;
  }
  tr.push_back(fmt("R3 oscillation_count %.0f <= max_osc %.0f -> pass", f_.oscillation_count, t.max_osc));

  // R4 rate of change
  if (f_.max_rate_n_per_s < t.min_rate_n_per_s) {
    tr.push_back(fmt("R4 max_rate %.3f N/s < min_rate %.3f N/s -> FAIL", f_.max_rate_n_per_s, t.min_rate_n_per_s));
    c.result = ClassResult::REJECTED_NOISE;
    c.reason = fmt("Sustained but slow rise (peak rate %.3f N/s < %.3f N/s): current/drift, not a bite.",
                   f_.max_rate_n_per_s, t.min_rate_n_per_s);
    return c;
  }
  tr.push_back(fmt("R4 max_rate %.3f N/s >= min_rate %.3f N/s -> pass", f_.max_rate_n_per_s, t.min_rate_n_per_s));

  // R5 sustained pull
  if (f_.continuous_above_ms < t.confirm_min_ms) {
    tr.push_back(fmt("R5 continuous_above %.0f ms < confirm_min_ms %.0f -> FAIL", f_.continuous_above_ms,
                     t.confirm_min_ms));
    c.result = ClassResult::REJECTED_TRANSIENT;
    c.reason = fmt("Delta stayed above trigger for only %.0f ms (< %.0f ms): transient.", f_.continuous_above_ms,
                   t.confirm_min_ms);
    return c;
  }
  tr.push_back(fmt("R5 continuous_above %.0f ms >= confirm_min_ms %.0f -> pass", f_.continuous_above_ms,
                   t.confirm_min_ms));

  c.result = ClassResult::BITE_CANDIDATE_CONFIRMED;
  c.reason = fmt("Sustained pull (%.0f ms above trigger) with fast rise (%.3f N/s), low oscillation, healthy sensor.",
                 f_.continuous_above_ms, f_.max_rate_n_per_s);
  tr.push_back("ALL rules pass -> bite_candidate_confirmed (thresholds provisional)");
  return c;
}

bool DeterministicBaselineV1::rearmed(const ProcessedSample& s) {
  const bool below = s.valid && s.delta < th_.trigger_delta_n * th_.release_ratio;
  if (!below) {
    quiet_ = false;
    return false;
  }
  if (!quiet_) {
    quiet_ = true;
    quiet_since_ = s.t_ms;
  }
  return s.t_ms - quiet_since_ >= th_.rearm_quiet_ms;
}

}  // namespace hp
