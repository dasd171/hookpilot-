#include "hookpilot/signal_processor.h"

#include <algorithm>
#include <cmath>

namespace hp {

BaselineSignalProcessor::BaselineSignalProcessor(const ClassifierThresholds& th, const SafetyLimits& lim)
    : th_(th), lim_(lim) {
  th_.variance_window_samples = std::max(2, std::min(th_.variance_window_samples, kMaxWindow));
  th_.detrend_window_samples = std::max(1, std::min(th_.detrend_window_samples, kMaxWindow));
  th_.rate_lag_samples = std::max(1, std::min(th_.rate_lag_samples, kMaxWindow - 1));
  reset();
}

void BaselineSignalProcessor::reset() {
  initialised_ = false;
  frozen_ = false;
  x_ = smoothed_ = baseline_ = 0;
  xs_.fill(0);
  ss_.fill(0);
  count_ = head_ = 0;
  have_last_raw_ = false;
  identical_ = 0;
}

SensorHealth BaselineSignalProcessor::classify(const TensionReading& r) {
  if (!std::isfinite(r.newtons)) {
    have_last_raw_ = false;
    identical_ = 0;
    return r.health == SensorHealth::OUT_OF_RANGE ? SensorHealth::OUT_OF_RANGE : SensorHealth::DISCONNECTED;
  }
  if (!r.valid) {  // driver says invalid: never trust the value, whatever health it claims
    return (r.health == SensorHealth::OUT_OF_RANGE || r.health == SensorHealth::STUCK) ? r.health
                                                                                        : SensorHealth::DISCONNECTED;
  }
  if (r.newtons < lim_.sensor_min_n || r.newtons >= lim_.sensor_max_n) return SensorHealth::OUT_OF_RANGE;
  // Stuck: bit-identical consecutive readings. Real load cells always show some noise.
  if (have_last_raw_ && r.newtons == last_raw_) {
    ++identical_;
  } else {
    identical_ = 1;
  }
  last_raw_ = r.newtons;
  have_last_raw_ = true;
  if (identical_ >= lim_.stuck_samples) return SensorHealth::STUCK;
  return r.health == SensorHealth::DEGRADED ? SensorHealth::DEGRADED : SensorHealth::OK;
}

ProcessedSample BaselineSignalProcessor::process(const TensionReading& r, uint32_t t_ms) {
  ProcessedSample s;
  s.t_ms = t_ms;
  s.raw = r.newtons;
  s.health = classify(r);
  s.valid = (s.health == SensorHealth::OK || s.health == SensorHealth::DEGRADED);
  s.baseline_frozen = frozen_;

  if (s.valid) {
    const double x = r.newtons;
    if (!initialised_) {
      x_ = smoothed_ = baseline_ = x;
      initialised_ = true;
    } else {
      x_ = x;
      smoothed_ += th_.smoothing_alpha * (x - smoothed_);
      if (!frozen_) baseline_ += th_.baseline_alpha * (x - baseline_);
    }
    xs_[head_] = x_;
    ss_[head_] = smoothed_;
    head_ = (head_ + 1) % kMaxWindow;
    if (count_ < kMaxWindow) ++count_;
  }

  s.x = x_;
  s.smoothed = smoothed_;
  s.baseline = baseline_;
  s.delta = initialised_ ? x_ - baseline_ : 0.0;

  auto at = [&](int back) {  // back=0 -> newest
    int idx = (head_ - 1 - back) % kMaxWindow;
    if (idx < 0) idx += kMaxWindow;
    return idx;
  };
  // rate from smoothed signal
  const int lag = th_.rate_lag_samples;
  if (count_ > lag) {
    s.rate = (ss_[at(0)] - ss_[at(lag)]) / (lag * (kDtMs / 1000.0));
  }
  // short-term variance (population) over window of raw valid values
  const int vw = std::min(th_.variance_window_samples, count_);
  if (vw >= 2) {
    double m = 0;
    for (int k = 0; k < vw; ++k) m += xs_[at(k)];
    m /= vw;
    double v = 0;
    for (int k = 0; k < vw; ++k) v += (xs_[at(k)] - m) * (xs_[at(k)] - m);
    s.variance = v / vw;
  }
  // detrended: x - trailing moving average (including current)
  const int dw = std::min(th_.detrend_window_samples, count_);
  if (dw >= 1) {
    double m = 0;
    for (int k = 0; k < dw; ++k) m += xs_[at(k)];
    s.detrended = x_ - m / dw;
  }
  return s;
}

}  // namespace hp
