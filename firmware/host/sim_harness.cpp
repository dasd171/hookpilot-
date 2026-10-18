// SIMULATION ONLY.
#include "sim_harness.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace hp {
namespace sim {

namespace {
const char* const kInjectionKinds[] = {"sensor_disconnect", "sensor_nan",  "sensor_out_of_range", "sensor_stuck",
                                       "over_tension",      "motor_stall", "encoder_loss",        "hook_jam",
                                       "comm_loss",         "link_down",   "undervoltage",        "estop",
                                       "over_temp",         "watchdog_reset"};

Json feedbackJson(const ActuatorFeedback& f) {
  Json j = Json::object();
  j["pos"] = f.position;
  j["cur"] = f.current_a;
  j["active"] = f.active;
  j["at_target"] = f.at_target;
  j["pos_valid"] = f.position_valid;
  j["status"] = f.status;
  return j;
}

Json faultsJson(uint32_t m) {
  Json a = Json::array();
  for (const auto& f : faultList(m)) a.push(f);
  return a;
}
}  // namespace

bool isKnownInjection(const std::string& kind) {
  for (const char* k : kInjectionKinds)
    if (kind == k) return true;
  return false;
}

bool parseInjection(const std::string& spec, Injection* out, std::string* err) {
  Injection inj;
  const size_t at = spec.find('@');
  inj.kind = spec.substr(0, at);
  if (!isKnownInjection(inj.kind)) {
    if (err) *err = "unknown injection kind '" + inj.kind + "'";
    return false;
  }
  if (at != std::string::npos) {
    std::string rest = spec.substr(at + 1);
    const size_t colon = rest.find(':');
    std::string when = rest.substr(0, colon);
    if (colon != std::string::npos) {
      inj.has_value = true;
      inj.value = std::strtod(rest.c_str() + colon + 1, nullptr);
    }
    const std::string rel = "retrieval+";
    if (when.compare(0, rel.size(), rel) == 0) {
      inj.rel_retrieval = true;
      when = when.substr(rel.size());
    }
    char* end = nullptr;
    inj.at_ms = std::strtod(when.c_str(), &end);
    if (when.empty() || (end && *end)) {
      if (err) *err = "bad time in injection '" + spec + "'";
      return false;
    }
  }
  *out = inj;
  return true;
}

void applyInjection(SimRig& rig, const Injection& inj) {
  const std::string& k = inj.kind;
  if (k == "sensor_disconnect") rig.sensor.injectDisconnect(true);
  else if (k == "sensor_nan") rig.sensor.injectNaN(true);
  else if (k == "sensor_out_of_range") rig.sensor.injectOutOfRange(true, inj.has_value ? inj.value : 60.0);
  else if (k == "sensor_stuck") rig.sensor.injectStuck(true);
  else if (k == "over_tension") rig.sensor.injectOverride(true, inj.has_value ? inj.value : 25.0);
  else if (k == "motor_stall") rig.retrieval.injectStall(true);
  else if (k == "encoder_loss") rig.retrieval.injectEncoderLoss(true);
  else if (k == "hook_jam") rig.hook.injectJam(true);
  else if (k == "comm_loss") rig.comm.injectHeartbeatLoss(true);
  else if (k == "link_down") rig.comm.setLinkUp(false);
  else if (k == "undervoltage") rig.health.setVoltage(inj.has_value ? inj.value : 10.2);
  else if (k == "estop") rig.health.setEstop(true);
  else if (k == "over_temp") rig.health.setTemperature(inj.has_value ? inj.value : 75.0);
}

SimRig::SimRig(const RigOptions& o) : watchdog(o.watchdog_reset_at_boot), opt(o) {
  signal.reset(new BaselineSignalProcessor(opt.cfg.classifier, opt.cfg.safety));
  detector.reset(new DeterministicBaselineV1(opt.cfg.classifier));
  safety.reset(new DefaultSafetyController(opt.cfg.safety));
  tee_.sinks = {&sink, opt.extra_sink};
  logger.reset(new JsonLinesEventLogger(&tee_));
  retrieval.current_base_a = opt.coupling.retrieval_current_base_a;
  retrieval.current_per_n_a = opt.coupling.retrieval_current_per_n_a;
  hook.tolerance_mm = opt.cfg.actuators.hook_set_position_tol_mm;
  ControllerDeps deps{clock, watchdog, sensor, *signal, *detector, *safety, hook, retrieval, *logger, health, comm};
  ctl.reset(new HookPilotController(opt.cfg, deps));
}

void SimRig::boot() { ctl->begin(); }

void SimRig::step() {
  if (!first_step_) clock.advance(kDtMs);
  first_step_ = false;
  // Line load and motor load are decided from the actuator state at the END of the previous tick.
  double load = 0;
  if (opt.simulate_line_load) {
    if (hook.feedback().active) load += opt.coupling.hook_set_line_load_n;
    if (retrieval.feedback().active) load += opt.coupling.retrieval_line_load_n;
    sensor.setAdditiveLoad(load);
  }
  last_load_ = load;
  retrieval.setLoad(ctl->lastSample().x);
  ctl->tick();
}

std::string traceLine(SimRig& rig, int k, const char* phase, size_t* tc, size_t* rc, size_t* ec,
                      const std::vector<std::string>& injected) {
  const ProcessedSample& s = rig.ctl->lastSample();
  Json j = Json::object();
  j["k"] = k;
  j["phase"] = phase;
  j["t"] = rig.clock.nowMs();
  j["state"] = toString(rig.ctl->state());
  j["armed"] = rig.safety->armed();
  j["raw"] = std::isfinite(s.raw) ? Json(s.raw) : Json();
  j["valid"] = s.valid;
  j["health"] = toString(s.health);
  j["x"] = s.x;
  j["baseline"] = s.baseline;
  j["delta"] = s.delta;
  j["load_n"] = rig.lastLoad();
  j["hook"] = feedbackJson(rig.hook.feedback());
  j["retr"] = feedbackJson(rig.retrieval.feedback());
  j["hb_ms"] = rig.comm.lastHeartbeatMs();
  j["latched"] = faultsJson(rig.safety->faults());
  j["active"] = faultsJson(rig.safety->activeConditions());
  Json cmds = Json::array();
  const auto& rl = rig.ctl->requestLog();
  for (; *rc < rl.size(); ++*rc) {
    const RequestEntry& e = rl[*rc];
    Json c = Json::object();
    c["actuator"] = toString(e.req.actuator);
    c["command"] = e.req.command;
    c["target"] = e.req.target;
    c["speed"] = e.req.speed;
    c["t_ms"] = e.req.t_ms;
    c["permitted"] = e.permitted;
    c["reason"] = e.decision_reason;
    cmds.push(c);
  }
  j["cmds"] = cmds;
  Json trans = Json::array();
  const auto& tr = rig.ctl->transitions();
  for (; *tc < tr.size(); ++*tc) {
    Json t = Json::object();
    t["t"] = tr[*tc].t_ms;
    t["from"] = toString(tr[*tc].from);
    t["to"] = toString(tr[*tc].to);
    t["reason"] = tr[*tc].reason;
    trans.push(t);
  }
  j["trans"] = trans;
  Json inj = Json::array();
  for (const auto& i : injected) inj.push(i);
  j["injected"] = inj;
  Json recs = Json::array();
  for (; *ec < rig.sink.lines.size(); ++*ec) {
    Json r;
    parseJson(rig.sink.lines[*ec], r);
    recs.push(Json(r.find("integrity")->find("record_sha256")->str()));
  }
  j["records"] = recs;
  return j.dump();
}

ScenarioRunResult runScenario(const std::string& scenario, uint32_t seed, int duration_ms, const RigOptions& o) {
  RigOptions opt = o;
  for (const auto& inj : opt.injections)
    if (inj.kind == "watchdog_reset") opt.watchdog_reset_at_boot = true;
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
  src.generator = kSynthGeneratorId;
  rig.ctl->setSource(src);
  // event-id randomness: deterministic per seed (SIMULATION)
  auto idrng = std::make_shared<Mulberry32>(seed ^ 0x9E3779B9u);
  rig.ctl->setIdRandom([idrng]() { return idrng->nextU32(); });

  // The scenario's own actuation fault is an ordinary retrieval-relative injection, applied first.
  std::vector<Injection> injections;
  const double fa = sig.params.fault_after_retrieval_start_ms;
  if ((scenario == "motor_stall" || scenario == "comm_loss") && fa >= 0) {
    Injection si;
    si.kind = scenario;
    si.rel_retrieval = true;
    si.at_ms = fa;
    injections.push_back(si);
  }
  for (const auto& inj : opt.injections)
    if (inj.kind != "watchdog_reset") injections.push_back(inj);
  std::vector<bool> done(injections.size(), false);

  rig.boot();
  ScenarioRunResult res;
  size_t tc = 0, rc = 0, ec = 0;
  const size_t n = sig.tension_n.size();
  bool retrieval_seen = false;
  uint32_t retrieval_start = 0;
  for (size_t i = 0; i < n; ++i) {
    rig.step();
    if (i == 0 && opt.arm_at_start) {
      std::string why;
      if (!rig.arm(&why)) std::fprintf(stderr, "[SIMULATION] arm refused: %s\n", why.c_str());
    }
    const uint32_t now = rig.clock.nowMs();
    if (rig.ctl->state() == State::RETRIEVE && !retrieval_seen) {
      retrieval_seen = true;
      retrieval_start = now;
      res.reached_retrieve = true;
    }
    std::vector<std::string> applied;
    for (size_t q = 0; q < injections.size(); ++q) {
      if (done[q]) continue;
      const Injection& inj = injections[q];
      bool fire;
      if (inj.rel_retrieval) fire = retrieval_seen && static_cast<double>(now - retrieval_start) >= inj.at_ms;
      else fire = static_cast<double>(now) >= inj.at_ms;
      if (!fire) continue;
      done[q] = true;
      applyInjection(rig, inj);
      applied.push_back(inj.kind);
    }
    if (opt.trace_sink) opt.trace_sink->writeLine(traceLine(rig, static_cast<int>(i), "tick", &tc, &rc, &ec, applied));
  }
  // End of the synthetic window: stop and disarm if an event is open (closes it as IDLE); a run that
  // logged nothing gets one no_event summary record.
  if (rig.ctl->eventOpen()) rig.ctl->requestDisarm();
  if (rig.ctl->eventsLogged() == 0) rig.ctl->logNoEventSummary();
  if (opt.trace_sink) opt.trace_sink->writeLine(traceLine(rig, static_cast<int>(n), "end", &tc, &rc, &ec, {}));
  res.lines = rig.sink.lines;
  res.final_state = rig.ctl->state();
  res.transitions = rig.ctl->transitions();
  res.faults = rig.safety->faults();
  return res;
}

}  // namespace sim
}  // namespace hp
