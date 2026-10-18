// SIMULATION ONLY.
#include "sim_harness.h"

#include <cstdio>

namespace hp {
namespace sim {

SimRig::SimRig(const RigOptions& o) : watchdog(o.watchdog_reset_at_boot), opt(o) {
  signal.reset(new BaselineSignalProcessor(opt.cfg.classifier, opt.cfg.safety));
  detector.reset(new DeterministicBaselineV1(opt.cfg.classifier));
  safety.reset(new DefaultSafetyController(opt.cfg.safety));
  tee_.sinks = {&sink, opt.extra_sink};
  logger.reset(new JsonLinesEventLogger(&tee_));
  ControllerDeps deps{clock, watchdog, sensor, *signal, *detector, *safety, hook, retrieval, *logger, health, comm};
  ctl.reset(new HookPilotController(opt.cfg, deps));
}

void SimRig::boot() { ctl->begin(); }

void SimRig::step() {
  if (!first_step_) clock.advance(kDtMs);
  first_step_ = false;
  if (opt.simulate_line_load) {
    double load = 0;
    if (hook.feedback().active) load += opt.hook_set_line_load_n;
    if (retrieval.feedback().active) load += opt.retrieval_line_load_n;
    sensor.setAdditiveLoad(load);
  }
  retrieval.setLoad(ctl->lastSample().x);
  ctl->tick();
}

ScenarioRunResult runScenario(const std::string& scenario, uint32_t seed, int duration_ms, const RigOptions& o) {
  RigOptions opt = o;
  SimRig rig(opt);
  SynthSignal sig = generateSynth(scenario, seed, kSampleRateHz, duration_ms);
  rig.sensor.load(sig);
  SourceInfo src;
  src.mode = "simulation";
  src.scenario = scenario;
  src.has_seed = true;
  src.seed = seed;
  src.has_ground_truth = true;
  src.ground_truth_label = groundTruthLabel(scenario);
  rig.ctl->setSource(src);
  // event-id randomness: deterministic per seed (SIMULATION)
  auto idrng = std::make_shared<Mulberry32>(seed ^ 0x9E3779B9u);
  rig.ctl->setIdRandom([idrng]() { return idrng->nextU32(); });

  rig.boot();
  ScenarioRunResult res;
  const size_t n = sig.tension_n.size();
  bool retrieval_seen = false;
  uint32_t retrieval_start = 0;
  bool fault_injected = false;
  for (size_t i = 0; i < n; ++i) {
    rig.step();
    if (i == 0) {
      std::string why;
      if (!rig.arm(&why)) std::fprintf(stderr, "[SIMULATION] arm refused: %s\n", why.c_str());
    }
    const State s = rig.ctl->state();
    if (s == State::RETRIEVE && !retrieval_seen) {
      retrieval_seen = true;
      retrieval_start = rig.clock.nowMs();
      res.reached_retrieve = true;
    }
    const double fa = sig.params.fault_after_retrieval_start_ms;
    if (retrieval_seen && !fault_injected && fa >= 0 && rig.clock.nowMs() - retrieval_start >= fa) {
      fault_injected = true;
      if (scenario == "motor_stall") rig.retrieval.injectStall(true);
      if (scenario == "comm_loss") rig.comm.setLinkUp(false);
    }
  }
  // End of the synthetic window: stop and disarm (closes any open event as IDLE).
  if (rig.ctl->eventOpen()) rig.ctl->requestDisarm();
  res.lines = rig.sink.lines;
  res.final_state = rig.ctl->state();
  res.transitions = rig.ctl->transitions();
  res.faults = rig.safety->faults();
  return res;
}

}  // namespace sim
}  // namespace hp
