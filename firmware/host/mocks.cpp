// HOST MOCKS - SIMULATION ONLY. No hardware is touched by anything in this file.
#include "mocks.h"

#include <algorithm>
#include <set>

namespace hp {
namespace mock {

static bool g_banner = true;
void setBannerEnabled(bool on) { g_banner = on; }

void banner(const char* cls) {
  static std::set<std::string> shown;
  if (!g_banner || shown.count(cls)) return;
  shown.insert(cls);
  std::fprintf(stderr, "[MOCK] %s: SIMULATION ONLY - controls no hardware\n", cls);
}

// ---------------- MockTensionSensor ----------------
TensionReading MockTensionSensor::read() {
  TensionReading r;
  double v;
  if (constant_) {
    // tiny deterministic dither so the stuck-sensor detector does not fire on a constant mock
    dither_ = dither_ * 1103515245u + 12345u;
    v = const_value_ + (static_cast<int>((dither_ >> 16) % 7u) - 3) * 1e-4;
  } else if (idx_ < sig_.tension_n.size()) {
    v = sig_.tension_n[idx_++];
  } else {
    r.newtons = std::numeric_limits<double>::quiet_NaN();
    r.valid = false;
    r.health = SensorHealth::DISCONNECTED;
    return r;
  }
  v += additive_;
  if (override_) v = override_value_;
  if (stuck_) {
    if (!have_stuck_) {
      stuck_value_ = v;
      have_stuck_ = true;
    }
    v = stuck_value_;
  }
  if (disconnect_) {
    r.newtons = std::numeric_limits<double>::quiet_NaN();
    r.valid = false;
    r.health = SensorHealth::DISCONNECTED;
    return r;
  }
  if (nan_) {
    r.newtons = std::numeric_limits<double>::quiet_NaN();
    r.valid = true;  // a driver bug: claims valid but delivers NaN -> processor must reject
    r.health = SensorHealth::OK;
    return r;
  }
  if (oor_) v = oor_value_;
  r.newtons = v;
  r.valid = true;
  r.health = SensorHealth::OK;
  return r;
}

CalibrationInfo MockTensionSensor::calibration() const {
  CalibrationInfo c;
  c.calibration_version = "SIMULATION-no-calibration (synthetic newtons from hookpilot-synth-v1)";
  c.scale_n_per_count = 1.0;
  c.offset_counts = 0.0;
  return c;
}

// ---------------- MockHookActuator ----------------
void MockHookActuator::moveTo(double position_mm, double speed_mm_s) {
  target_ = position_mm;
  speed_ = speed_mm_s;
  fb_.active = true;
  fb_.at_target = false;
  fb_.status = "moving";
  ++moves_;
}

void MockHookActuator::stop() {
  fb_.active = false;
  fb_.speed = 0;
  fb_.current_a = 0;
  if (fb_.status != "idle") fb_.status = "stopped";
}

void MockHookActuator::update(uint32_t now_ms) {
  const double dt = have_last_ ? (now_ms - last_ms_) / 1000.0 : 0.0;
  last_ms_ = now_ms;
  have_last_ = true;
  fb_.position_valid = !fb_loss_;
  if (!fb_.active) {
    fb_.current_a = 0.05;  // de-energised / holding nothing
    fb_.speed = 0;
    return;
  }
  if (jam_) {
    fb_.speed = 0;
    fb_.current_a = 1.6;
    fb_.status = "jammed";
    return;
  }
  const double dir = target_ >= fb_.position ? 1.0 : -1.0;
  const double step = speed_ * dt;
  const double remaining = std::fabs(target_ - fb_.position);
  if (overshoot_ > 0 && target_ > 0) {
    fb_.position += step;  // runaway past the target (fault injection)
    if (fb_.position >= target_ + overshoot_) fb_.position = target_ + overshoot_;
  } else if (step >= remaining) {
    fb_.position = target_;
  } else {
    fb_.position += dir * step;
  }
  fb_.speed = speed_;
  fb_.current_a = 0.8;
  if (std::fabs(fb_.position - target_) <= tolerance_mm && overshoot_ <= 0) {
    fb_.active = false;
    fb_.at_target = true;
    fb_.speed = 0;
    fb_.current_a = 0.1;
    fb_.status = "at_target";
  }
}

// ---------------- MockRetrieval ----------------
void MockRetrieval::start(double target_m, double speed_m_s) {
  target_ = target_m;
  speed_ = speed_m_s;
  fb_.active = true;
  fb_.at_target = false;
  fb_.status = "retrieving";
  ++starts_;
}

void MockRetrieval::stop() {
  fb_.active = false;
  fb_.speed = 0;
  fb_.current_a = 0;
  if (fb_.status != "idle" && fb_.status != "at_target") fb_.status = "stopped";
}

void MockRetrieval::update(uint32_t now_ms) {
  const double dt = have_last_ ? (now_ms - last_ms_) / 1000.0 : 0.0;
  last_ms_ = now_ms;
  have_last_ = true;
  fb_.position_valid = !enc_loss_;
  if (!fb_.active) {
    fb_.current_a = 0;
    fb_.speed = 0;
    return;
  }
  if (stall_) {
    fb_.speed = 0;
    fb_.current_a = 2.5;  // stall current
    fb_.status = "stalled";
    return;
  }
  const double step = speed_ * dt;
  if (!runaway_ && fb_.position + step >= target_) {
    fb_.position = target_;
    fb_.active = false;
    fb_.at_target = true;
    fb_.speed = 0;
    fb_.current_a = 0;
    fb_.status = "at_target";
    return;
  }
  if (!enc_loss_) fb_.position += step;
  fb_.speed = speed_;
  fb_.current_a = 0.3 + 0.1 * std::max(0.0, load_n_);
  fb_.status = "retrieving";
}

}  // namespace mock
}  // namespace hp
