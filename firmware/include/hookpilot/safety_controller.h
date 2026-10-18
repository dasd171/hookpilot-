// Independent safety controller + actuator gate.
//
// The SafetyController never sees classifier output. It evaluates interlocks from raw inputs
// (sensor validity, tension, actuator feedback, voltage, e-stop, comm heartbeat) every tick and
// latches faults. The ActuatorGate is the ONLY object holding actuator references used by the
// controller: every motion command passes SafetyController::permit() first. stop() is always allowed.
#pragma once

#include <array>

#include "hookpilot/config.h"
#include "hookpilot/interfaces.h"

namespace hp {

class DefaultSafetyController : public SafetyController {
 public:
  explicit DefaultSafetyController(const SafetyLimits& lim) : lim_(lim) {}
  uint32_t update(const SafetyInputs& in) override;
  SafetyDecision permit(const ActuatorRequest& req, const SafetyInputs& in) override;
  bool arm(const SafetyInputs& in, std::string* why_not) override;
  void disarm() override { armed_ = false; }
  bool armed() const override { return armed_; }
  uint32_t faults() const override { return latched_; }
  uint32_t activeConditions() const override { return active_; }
  bool clearFaults(const SafetyInputs& in) override;
  void latchFault(uint32_t f) override {
    latched_ |= f;
    armed_ = false;
  }
  const SafetyLimits& limits() const override { return lim_; }

 private:
  uint32_t evaluate(const SafetyInputs& in);  // conditions present now (all, regardless of armed)

  SafetyLimits lim_;
  bool armed_ = false;
  uint32_t latched_ = 0;
  uint32_t active_ = 0;
  int sensor_bad_count_ = 0;
  SensorHealth last_bad_health_ = SensorHealth::OK;
  int over_tension_count_ = 0;
  bool uv_pending_ = false;
  uint32_t uv_since_ = 0;
  bool hook_was_active_ = false, retr_was_active_ = false;
  uint32_t hook_since_ = 0, retr_since_ = 0;
  // retrieval progress history for stall detection
  struct PosSample { uint32_t t; double pos; };
  static constexpr int kHist = 64;
  std::array<PosSample, kHist> hist_{};
  int hist_n_ = 0, hist_head_ = 0;
};

class ActuatorGate {
 public:
  ActuatorGate(SafetyController& safety, HookSettingActuator& hook, RetrievalController& retr)
      : safety_(safety), hook_(hook), retr_(retr) {}
  // Asks the safety controller; issues the command only when permitted.
  SafetyDecision request(const ActuatorRequest& req, const SafetyInputs& in);
  void stopAll();  // always allowed, never gated
  void stopHook() { hook_.stop(); }
  void stopRetrieval() { retr_.stop(); }
  ActuatorFeedback hookFeedback() const { return hook_.feedback(); }
  ActuatorFeedback retrievalFeedback() const { return retr_.feedback(); }
  void update(uint32_t now_ms) {
    hook_.update(now_ms);
    retr_.update(now_ms);
  }
  void resetRetrievalPosition() { retr_.resetPosition(); }
  uint32_t commandsIssued() const { return issued_; }
  uint32_t commandsDenied() const { return denied_; }

 private:
  SafetyController& safety_;
  HookSettingActuator& hook_;
  RetrievalController& retr_;
  uint32_t issued_ = 0, denied_ = 0;
};

}  // namespace hp
