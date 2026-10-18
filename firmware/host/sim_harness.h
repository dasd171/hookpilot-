// Host simulation rig - SIMULATION ONLY. Wires the real control core (signal processor,
// deterministic-baseline-v1, safety controller, state machine, logger) to host mocks.
#pragma once

#include <memory>
#include <string>
#include <vector>

#include "hookpilot/bite_detector.h"
#include "hookpilot/controller.h"
#include "hookpilot/event_logger.h"
#include "hookpilot/prng.h"
#include "hookpilot/safety_controller.h"
#include "hookpilot/scenarios.h"
#include "hookpilot/signal_processor.h"
#include "mocks.h"

namespace hp {
namespace sim {

struct RigOptions {
  ControllerConfig cfg;
  bool watchdog_reset_at_boot = false;
  bool simulate_line_load = true;   // add actuator line loads to the sensor (SIMULATION coupling)
  double hook_set_line_load_n = 1.5;
  double retrieval_line_load_n = 0.6;
  LineSink* extra_sink = nullptr;   // e.g. stdout
};

class SimRig {
 public:
  explicit SimRig(const RigOptions& opt = RigOptions());

  void boot();                         // controller.begin()
  void step();                         // advance clock by kDtMs (except first step) and tick
  void stepN(int n) { for (int i = 0; i < n; ++i) step(); }
  bool arm(std::string* why = nullptr) { return ctl->requestArm(why); }

  mock::MockClock clock;
  mock::MockWatchdog watchdog;
  mock::MockTensionSensor sensor;
  mock::MockHookActuator hook;
  mock::MockRetrieval retrieval;
  mock::MockHealthMonitor health;
  mock::MockComm comm;
  mock::VectorSink sink;

  std::unique_ptr<BaselineSignalProcessor> signal;
  std::unique_ptr<DeterministicBaselineV1> detector;
  std::unique_ptr<DefaultSafetyController> safety;
  std::unique_ptr<JsonLinesEventLogger> logger;
  std::unique_ptr<HookPilotController> ctl;
  RigOptions opt;

 private:
  class TeeSink : public LineSink {
   public:
    std::vector<LineSink*> sinks;
    void writeLine(const std::string& l) override {
      for (auto* s : sinks) if (s) s->writeLine(l);
    }
  } tee_;
  bool first_step_ = true;
};

struct ScenarioRunResult {
  std::vector<std::string> lines;   // emitted JSON event lines
  State final_state = State::IDLE;
  std::vector<Transition> transitions;
  uint32_t faults = 0;
  bool reached_retrieve = false;
};

// Runs (scenario, seed) end-to-end: boot, arm, replay, inject the scenario's fault schedule
// (motor_stall / comm_loss after retrieval start), extra settle time, then close any open event.
ScenarioRunResult runScenario(const std::string& scenario, uint32_t seed, int duration_ms = 8000,
                              const RigOptions& opt = RigOptions());

}  // namespace sim
}  // namespace hp
