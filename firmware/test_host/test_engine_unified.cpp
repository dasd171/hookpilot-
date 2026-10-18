// Behaviours unified across firmware / Python / web demo on 2026-10-18 (docs/engine-spec.md).
#include "test_framework.h"
#include "test_helpers.h"

using namespace hp;

namespace {
SynthSignal biteSig(double total_ms = 8000) { return th::stepSignal(0.4, 2.0, 1000, 100, total_ms); }

std::vector<Json> parseLines(const std::vector<std::string>& lines) {
  std::vector<Json> out;
  for (const auto& l : lines) {
    Json j;
    parseJson(l, j);
    out.push_back(j);
  }
  return out;
}
}  // namespace

TEST(unified, hook_to_retrieval_gap_enforced_by_controller_and_safety) {
  th::Rig g;
  CHECK(g.start(biteSig()));
  CHECK(g.runUntil(State::HOOK_SET, 200));
  uint32_t done = 0;
  while (g.r.ctl->state() == State::HOOK_SET) {
    g.r.step();
    if (!done && g.r.hook.feedback().at_target) done = g.r.clock.nowMs();
  }
  CHECK(g.r.ctl->state() == State::RETRIEVE);
  CHECK_EQ(g.r.clock.nowMs() - done, SafetyLimits{}.hook_to_retrieval_gap_ms);
  // the safety controller itself denies a retrieval request inside the gap
  DefaultSafetyController sc{SafetyLimits{}};
  SafetyInputs in;
  in.sample.valid = true;
  in.sample.health = SensorHealth::OK;
  in.sample.raw = 1.0;
  in.bus_voltage_v = 11.4;
  sc.update(in);
  CHECK(sc.arm(in, nullptr));
  in.hook.active = true;
  sc.update(in);
  in.hook.active = false;
  in.now_ms = 20;
  sc.update(in);
  in.now_ms = 100;
  ActuatorRequest rr{ActuatorId::RETRIEVAL, "retrieve", 1.0, 0.5, 100};
  SafetyDecision d = sc.permit(rr, in);
  CHECK(!d.permitted);
  CHECK_EQ(d.reason, std::string("deny: hook_set stopped 80 ms ago (< hook_to_retrieval_gap_ms)"));
  in.now_ms = 120;
  CHECK(sc.permit(rr, in).permitted);
}

TEST(unified, disarmed_run_observes_and_never_requests) {
  sim::RigOptions o;
  o.arm_at_start = false;
  auto r = sim::runScenario("bite", 42, 8000, o);
  CHECK_EQ(r.lines.size(), size_t(1));
  Json rec;
  parseJson(r.lines[0], rec);
  CHECK_EQ(th::field(rec, {"classification", "result"}), std::string("bite_candidate_confirmed"));
  CHECK_EQ(th::field(rec, {"outcome", "stop_reason"}), std::string("disarmed_observation_only: no actuation requested"));
  CHECK_EQ(th::field(rec, {"outcome", "final_state"}), std::string("IDLE"));
  CHECK_EQ(th::field(rec, {"safety", "decision"}), std::string("deny"));
  CHECK_EQ(th::field(rec, {"actuation", "requested"}), std::string("[]"));
  CHECK_EQ(th::field(rec, {"actuation", "feedback"}), std::string("[]"));
  CHECK(r.final_state == State::IDLE);
}

TEST(unified, comm_loss_detected_after_heartbeat_timeout) {
  sim::RigOptions o;
  mock::VectorSink trace;
  o.trace_sink = &trace;
  sim::Injection inj;
  CHECK(sim::parseInjection("comm_loss@2000", &inj));
  o.injections.push_back(inj);
  auto r = sim::runScenario("idle", 3, 8000, o);
  CHECK(r.faults & FAULT_COMM_LOSS);
  // injected at the end of tick t=2000 (last heartbeat 2000) -> latched when age > 300 ms, i.e. t=2320
  auto lines = parseLines(trace.lines);
  CHECK_EQ(lines.size(), size_t(401));
  for (const auto& l : lines) {
    const uint32_t t = static_cast<uint32_t>(l.find("t")->num());
    const bool latched = l.find("latched")->size() > 0;
    if (t < 2320) CHECK(!latched);
    if (t == 2320) CHECK(latched);
  }
  Json rec;
  parseJson(r.lines.back(), rec);
  CHECK_EQ(th::field(rec, {"safety", "fault_latched_t_ms", "COMM_LOSS"}), std::string("2320"));
}

TEST(unified, injection_parser) {
  sim::Injection i;
  CHECK(sim::parseInjection("over_tension@retrieval+300:8.5", &i));
  CHECK(i.kind == "over_tension" && i.rel_retrieval && i.at_ms == 300 && i.has_value && i.value == 8.5);
  CHECK(sim::parseInjection("estop@1000", &i));
  CHECK(!i.rel_retrieval && i.at_ms == 1000 && !i.has_value);
  CHECK(sim::parseInjection("watchdog_reset", &i));
  std::string err;
  CHECK(!sim::parseInjection("meteor_strike@10", &i, &err));
  CHECK(!sim::parseInjection("estop@soon", &i, &err));
}

TEST(unified, rule_trace_starts_with_trigger_line) {
  auto r = sim::runScenario("bite", 42);
  Json rec;
  parseJson(r.lines[0], rec);
  const Json* tr = rec.find("classification")->find("rule_trace");
  const std::string first = tr->items()[0].str();
  CHECK(first.find("ms TRIGGER delta=") != std::string::npos);
  CHECK(first.find("trigger_delta_n=1.000N (baseline frozen at") != std::string::npos);
  CHECK_EQ(tr->items()[1].str().substr(0, 7), std::string("window:"));
  CHECK(rec.find("classification")->find("trigger_t_ms") != nullptr);
  CHECK(rec.find("classification")->find("decision_t_ms") != nullptr);
}

TEST(unified, end_of_window_disarm_decides_open_candidate) {
  // a step that triggers 300 ms before the end of the window: the candidate is still open at the end
  th::Rig g;
  SynthSignal s = th::stepSignal(0.4, 2.0, 2700, 60, 3000);
  CHECK(g.start(s));
  g.r.stepN(static_cast<int>(s.tension_n.size()) - 1);
  CHECK(g.r.ctl->state() == State::CANDIDATE);
  g.r.ctl->requestDisarm();
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"outcome", "stop_reason"}), std::string("operator_disarm"));
  const Json* tr = rec.find("classification")->find("rule_trace");
  CHECK_EQ(tr->items().back().str(), std::string("interrupted by operator_disarm"));
  CHECK(g.r.ctl->state() == State::IDLE);
}

TEST(unified, trace_records_every_tick_and_full_precision_records) {
  sim::RigOptions o;
  mock::VectorSink trace;
  o.trace_sink = &trace;
  auto r = sim::runScenario("motor_stall", 5, 8000, o);
  CHECK_EQ(trace.lines.size(), size_t(401));  // 400 ticks + end line
  int both_active = 0, cmds = 0;
  for (const auto& j : parseLines(trace.lines)) {
    if (j.find("hook")->find("active")->boolean() && j.find("retr")->find("active")->boolean()) ++both_active;
    cmds += static_cast<int>(j.find("cmds")->size());
  }
  CHECK_EQ(both_active, 0);
  CHECK_EQ(cmds, 2);  // stroke + retrieve; the stall stops the cycle before return_to_rest
  // records are no longer rounded to 4 decimals
  Json rec;
  parseJson(r.lines[0], rec);
  bool long_number = false;
  for (const auto& sm : rec.find("sensor_data")->find("samples")->items()) {
    const std::string d = jsNumber(sm.items()[1].num());
    if (d.size() > 8) long_number = true;
  }
  CHECK(long_number);
}
