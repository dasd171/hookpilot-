// Baseline signal processor: validity checks + EMA baseline (freezable) + smoothing, rate,
// short-term variance, detrended signal. Deterministic, allocation-free after construction.
#pragma once

#include <array>
#include <cstdint>

#include "hookpilot/config.h"
#include "hookpilot/interfaces.h"

namespace hp {

class BaselineSignalProcessor : public SignalProcessor {
 public:
  BaselineSignalProcessor(const ClassifierThresholds& th, const SafetyLimits& lim);
  void reset() override;
  ProcessedSample process(const TensionReading& r, uint32_t t_ms) override;
  void freezeBaseline(bool frozen) override { frozen_ = frozen; }
  bool baselineFrozen() const override { return frozen_; }

  static constexpr int kMaxWindow = 64;

 private:
  SensorHealth classify(const TensionReading& r);

  ClassifierThresholds th_;
  SafetyLimits lim_;
  bool initialised_ = false;
  bool frozen_ = false;
  double x_ = 0, smoothed_ = 0, baseline_ = 0;
  // ring buffers of valid samples
  std::array<double, kMaxWindow> xs_{};       // raw valid values
  std::array<double, kMaxWindow> ss_{};       // smoothed values
  int count_ = 0, head_ = 0;
  // stuck detection
  double last_raw_ = 0;
  bool have_last_raw_ = false;
  int identical_ = 0;
};

}  // namespace hp
