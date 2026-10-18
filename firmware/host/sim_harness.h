// Host simulation rig - SIMULATION ONLY. Wires the real control core (signal processor,
// deterministic-baseline-v1, safety controller, state machine, logger) to host mocks.
//
// The rig, the mocks and the run procedure below are the normative engine model shared with the
// Python port (services/evidence-service/evidence_service/engine.py) and the web demo
// (apps/web-demo/src/engine/engine.ts). See docs/engine-spec.md.
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

// Fault injection: "<kind>@<when>[:<value>]", when = "<t_ms>" (absolute) or "retrieval+<ms>"
// (relative to the tick that first observed RETRIEVE). Applied at the END of the first harness step
// whose condition holds (t >= when), so it takes effect from the next tick. Kinds:
//   sensor_disconnect, sensor_nan, sensor_out_of_range[:N=60], sensor_stuck, over_tension[:N=25]
//   (sensor override), motor_stall, encoder_loss, hook_jam, comm_loss (heartbeats stop, link still
//   reports up), link_down, undervoltage[:V=10.2], estop, over_temp[:C=75], watchdog_reset (boot cause;
//   <when> ignored).
struct Injection {
  std::string kind;
  bool rel_retrieval = false;
  double at_ms = 0;
  bool has_value = false;
  double value = 0;
};
bool parseInjection(const std::string& spec, Injection* out, std::string* err = nullptr);
bool isKnownInjection(const std::string& kind);

struct RigOptions {
  ControllerConfig cfg;
  bool watchdog_reset_at_boot = false;
  bool simulate_line_load = true;   // add actuator line loads to the sensor (SIMULATION coupling)
  SimCoupling coupling;
  LineSink* extra_sink = nullptr;   // e.g. stdout
  // runScenario only:
  bool arm_at_start = true;         // arm right after the first tick (false: disarmed observation run)
  std::vector<Injection> injections;
  LineSink* trace_sink = nullptr;   // one JSON line per harness step + one "end" line
};

class SimRig {
 public:
  explicit SimRig(const RigOptions& opt = RigOptions());

  void boot();                         // controller.begin()
  void step();                         // advance clock by kDtMs (except first step) and tick
  void stepN(int n) { for (int i = 0; i < n; ++i) step(); }
  bool arm(std::string* why = nullptr) { return ctl->requestArm(why); }
  double lastLoad() const { return last_load_; }

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
  double last_load_ = 0;
};

// Applies one injection to the rig (watchdog_reset is a boot option and is ignored here).
void applyInjection(SimRig& rig, const Injection& inj);

// One tick-trace JSON line (docs/engine-spec.md "Tick trace").
std::string traceLine(SimRig& rig, int k, const char* phase, size_t* trans_cursor, size_t* req_cursor,
                      size_t* rec_cursor, const std::vector<std::string>& injected);

struct ScenarioRunResult {
  std::vector<std::string> lines;   // emitted JSON event lines
  State final_state = State::IDLE;
  std::vector<Transition> transitions;
  uint32_t faults = 0;
  bool reached_retrieve = false;
};

// Runs (scenario, seed) end-to-end: boot, tick 0, arm (unless disarmed), replay the signal one sample
// per tick, apply injections (the scenario's own motor_stall / comm_loss fault is the injection
// "<kind>@retrieval+<fault_after_retrieval_start_ms>"), then at the end of the window: disarm if an
// event is still open, and log a no_event summary if the run logged nothing.
ScenarioRunResult runScenario(const std::string& scenario, uint32_t seed, int duration_ms = 8000,
                              const RigOptions& opt = RigOptions());

}  // namespace sim
}  // namespace hp
