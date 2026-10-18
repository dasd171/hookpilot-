// HookPilot deterministic state machine:
//   IDLE -> MONITOR -> CANDIDATE -> CONFIRM -> HOOK_SET -> RETRIEVE -> VERIFY -> IDLE
//   any state -> SAFE_STOP on a critical fault; SAFE_STOP -> IDLE (disarmed) only via explicit reset
//   with faults cleared. Boot (incl. after watchdog reset) is always IDLE, disarmed.
//
// The classifier (BiteDetector) only produces a Classification. Motion is REQUESTED through the
// ActuatorGate, which asks the independent SafetyController for every command.
#pragma once

#include <array>
#include <functional>
#include <string>
#include <vector>

#include "hookpilot/config.h"
#include "hookpilot/event_logger.h"
#include "hookpilot/interfaces.h"
#include "hookpilot/safety_controller.h"

namespace hp {

struct ControllerDeps {
  Clock& clock;
  Watchdog& watchdog;
  TensionSensor& sensor;
  SignalProcessor& signal;
  BiteDetector& detector;
  SafetyController& safety;
  HookSettingActuator& hook;
  RetrievalController& retrieval;
  EventLogger& logger;
  DeviceHealthMonitor& health;
  CommunicationAdapter& comm;
};

struct SourceInfo {
  std::string mode = "simulation";
  std::string scenario;
  bool has_seed = false;
  int64_t seed = 0;
  bool has_ground_truth = false;
  std::string ground_truth_label;
  int64_t utc_epoch_ms_at_boot = kSimulatedEpochMs;
  std::string clock_quality = "host_clock_simulated";
  bool simulated_actuators = true;
};

struct Transition {
  uint32_t t_ms;
  State from, to;
  std::string reason;
};

class HookPilotController {
 public:
  HookPilotController(const ControllerConfig& cfg, ControllerDeps deps);

  void setSource(const SourceInfo& s) { src_ = s; }
  void setIdRandom(std::function<uint32_t()> f) { rand32_ = std::move(f); }

  void begin();  // boot: outputs off, IDLE, disarmed, watchdog-reset check
  void tick();   // one control period (kDtMs)

  bool requestArm(std::string* why = nullptr);
  void requestDisarm();
  bool requestReset(std::string* why = nullptr);  // SAFE_STOP -> IDLE when faults cleared; IDLE: ack

  State state() const { return state_; }
  const ProcessedSample& lastSample() const { return sample_; }
  const SafetyInputs& lastSafetyInputs() const { return in_; }
  const std::vector<Transition>& transitions() const { return transitions_; }
  bool eventOpen() const { return ev_open_; }
  uint32_t eventsLogged() const { return events_logged_; }
  const ActuatorGate& gate() const { return gate_; }

 private:
  void transition(State to, const std::string& why);
  void gatherInputs(uint32_t now);
  void openEvent(uint32_t t_trigger);
  void closeEvent(State final_state, const std::string& stop_reason);
  void enterSafeStop(const std::string& why);
  void recordFeedback(const char* actuator, const ActuatorFeedback& fb);
  bool gatedRequest(ActuatorId a, const char* command, double target, double speed);
  void decideCandidate();
  Json interlocksJson() const;

  ControllerConfig cfg_;
  ControllerDeps d_;
  ActuatorGate gate_;
  SourceInfo src_;
  std::function<uint32_t()> rand32_;

  State state_ = State::IDLE;
  ProcessedSample sample_;
  SafetyInputs in_;
  std::vector<Transition> transitions_;

  // pre-roll ring buffer of (t, value, valid)
  struct Raw { uint32_t t; double v; bool valid; };
  static constexpr int kRing = 64;
  std::array<Raw, kRing> ring_{};
  int ring_n_ = 0, ring_head_ = 0;

  // open event
  bool ev_open_ = false;
  EventRecord ev_;
  bool ev_decided_ = false;
  bool ev_any_permit_ = false;
  bool awaiting_rearm_ = false;
  uint32_t state_since_ = 0;
  uint32_t ticks_ = 0;
  uint32_t event_counter_ = 0;
  uint32_t events_logged_ = 0;
  bool hook_returning_ = false;
};

}  // namespace hp
