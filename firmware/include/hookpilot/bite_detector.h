// deterministic-baseline-v1 bite classifier.
//
// It can only classify and REQUEST actuation through the controller; it holds no reference to
// any actuator or to the safety controller. All thresholds are provisional placeholders.
#pragma once

#include "hookpilot/config.h"
#include "hookpilot/interfaces.h"

namespace hp {

class DeterministicBaselineV1 : public BiteDetector {
 public:
  explicit DeterministicBaselineV1(const ClassifierThresholds& th) : th_(th) {}
  const char* name() const override { return kClassifierId; }
  bool shouldTrigger(const ProcessedSample& s) const override;
  void beginCandidate(const ProcessedSample& trigger) override;
  bool update(const ProcessedSample& s) override;
  Classification decide() const override;
  bool rearmed(const ProcessedSample& s) override;
  const ClassifierThresholds& thresholds() const override { return th_; }

  const Features& features() const { return f_; }

 private:
  ClassifierThresholds th_;
  Features f_;
  uint32_t t_trigger_ = 0, t_last_ = 0;
  bool run_alive_ = false;  // continuous above-trigger run since the trigger is unbroken
  int schmitt_ = 0;         // -1 low, 0 unknown, +1 high
  bool early_manual_ = false, early_sensor_ = false;
  // re-arm tracking
  uint32_t quiet_since_ = 0;
  bool quiet_ = false;
};

}  // namespace hp
