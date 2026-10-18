// State transitions, safety limits, timeouts, fault injection and fault recovery.
#include <limits>

#include "test_framework.h"
#include "test_helpers.h"

using namespace hp;

namespace {
SynthSignal biteSignal(double total_ms = 8000) { return th::stepSignal(0.4, 2.0, 1000, 100, total_ms); }
SynthSignal quietSignal(double total_ms = 8000) { return th::stepSignal(0.4, 0.0, 1e9, 100, total_ms); }

std::vector<State> path(const HookPilotController& c) {
  std::vector<State> p;
  for (const auto& t : c.transitions()) p.push_back(t.to);
  return p;
}
}  // namespace

TEST(state, boots_idle_disarmed_and_arms_only_from_idle) {
  th::Rig g;
  g.r.sensor.load(quietSignal());
  g.r.boot();
  CHECK(g.r.ctl->state() == State::IDLE);
  CHECK(!g.r.safety->armed());
  CHECK(g.r.watchdog.began());
  g.r.step();
  CHECK(g.r.ctl->state() == State::IDLE);  // never arms by itself
  CHECK(g.r.arm());
  CHECK(g.r.ctl->state() == State::MONITOR);
  std::string why;
  CHECK(!g.r.ctl->requestArm(&why));  // already armed / not IDLE
  g.r.stepN(10);
  CHECK(g.r.watchdog.feeds() >= 11);  // fed every tick
}

TEST(state, full_bite_cycle_transitions_and_record) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::IDLE, 400));
  const std::vector<State> expect = {State::MONITOR, State::CANDIDATE, State::CONFIRM, State::HOOK_SET,
                                     State::RETRIEVE, State::VERIFY,   State::IDLE};
  CHECK(path(*g.r.ctl) == expect);
  CHECK(!g.r.safety->armed());  // disarmed after a completed cycle
  CHECK_EQ(g.r.sink.lines.size(), size_t(1));
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"classification", "result"}), std::string("bite_candidate_confirmed"));
  CHECK_EQ(th::field(rec, {"outcome", "milestones", "hook_set_completed"}), std::string("true"));
  CHECK_EQ(th::field(rec, {"outcome", "milestones", "retrieval_completed"}), std::string("true"));
  CHECK_EQ(th::field(rec, {"outcome", "milestones", "fish_landed"}), std::string("unknown"));
  CHECK_EQ(th::field(rec, {"safety", "decision"}), std::string("permit"));
  CHECK_EQ(th::field(rec, {"outcome", "final_state"}), std::string("IDLE"));
  CHECK_EQ(th::field(rec, {"classification", "classifier"}), std::string("deterministic-baseline-v1"));
  CHECK_EQ(th::field(rec, {"classification", "thresholds", "label"}),
           std::string("provisional - derive from calibration + labeled data"));
  // integrity
  CHECK_EQ(computeRecordHash(rec), th::field(rec, {"integrity", "record_sha256"}));
  CHECK_EQ(th::field(rec, {"integrity", "prev_record_sha256"}), std::string("null"));
  CHECK_EQ(rec.dump(), g.r.sink.lines.back());  // emitted line is canonical
}

TEST(state, hook_set_and_retrieval_never_simultaneous) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  bool both = false;
  for (int i = 0; i < 400; ++i) {
    g.r.step();
    if (g.r.hook.feedback().active && g.r.retrieval.feedback().active) both = true;
  }
  CHECK(!both);
  CHECK_EQ(g.r.retrieval.startCommands(), 1u);
  CHECK_EQ(g.r.hook.moveCommands(), 2u);  // stroke + return to rest
}

TEST(safety, gate_denies_everything_when_disarmed_classifier_cannot_bypass) {
  DefaultSafetyController sc{SafetyLimits{}};
  mock::MockHookActuator hook;
  mock::MockRetrieval ret;
  ActuatorGate gate(sc, hook, ret);
  SafetyInputs in;
  in.sample.valid = true;
  in.sample.health = SensorHealth::OK;
  in.sample.raw = 1.0;
  in.bus_voltage_v = 11.4;
  ActuatorRequest req{ActuatorId::HOOK_SET, "stroke_to", 12.0, 60.0, 0};
  SafetyDecision d = gate.request(req, in);
  CHECK(!d.permitted);
  CHECK_EQ(hook.moveCommands(), 0u);
  sc.update(in);
  std::string why;
  CHECK(sc.arm(in, &why));
  CHECK(gate.request(req, in).permitted);
  CHECK_EQ(hook.moveCommands(), 1u);
  // mutual exclusion: retrieval while hook active -> deny
  in.hook.active = true;
  ActuatorRequest rr{ActuatorId::RETRIEVAL, "retrieve", 1.0, 0.5, 0};
  CHECK(!gate.request(rr, in).permitted);
  CHECK_EQ(ret.startCommands(), 0u);
  in.hook.active = false;
  // travel / speed limits
  CHECK(!gate.request({ActuatorId::HOOK_SET, "stroke_to", 15.5, 60.0, 0}, in).permitted);
  CHECK(!gate.request({ActuatorId::HOOK_SET, "stroke_to", 12.0, 500.0, 0}, in).permitted);
  CHECK(!gate.request({ActuatorId::RETRIEVAL, "retrieve", 2.0, 0.5, 0}, in).permitted);
  // force limit at retrieval request
  in.sample.raw = 8.5;
  CHECK(!gate.request(rr, in).permitted);
  CHECK(gate.commandsDenied() >= 5u);
}

TEST(safety, misconfigured_hook_target_denied_nothing_moves) {
  sim::RigOptions o;
  o.cfg.actuators.hook_set_target_mm = 20.0;  // beyond the 15 mm travel limit
  th::Rig g(o);
  CHECK(g.start(biteSignal()));
  g.r.stepN(150);
  CHECK_EQ(g.r.hook.moveCommands(), 0u);
  CHECK_EQ(g.r.retrieval.startCommands(), 0u);
  CHECK(g.r.ctl->state() == State::MONITOR);
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"classification", "result"}), std::string("bite_candidate_confirmed"));
  CHECK_EQ(th::field(rec, {"safety", "decision"}), std::string("deny"));
}

TEST(safety, force_limit_during_retrieval) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::RETRIEVE, 200));
  g.r.sensor.injectOverride(true, 8.3);  // > retrieval_force_limit_n (8.0), < hard max
  g.r.step();
  CHECK(g.r.ctl->state() == State::RETRIEVE);  // debounce: needs 2 consecutive samples
  g.r.step();
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_OVER_TENSION);
  CHECK(!g.r.retrieval.feedback().active);
  CHECK(th::hasFault(g.lastRecord(), "OVER_TENSION"));
}

TEST(safety, hard_max_tension_any_state) {
  th::Rig g;
  CHECK(g.start(quietSignal()));
  g.r.stepN(5);
  g.r.sensor.injectOverride(true, 20.5);
  g.r.stepN(2);
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_OVER_TENSION);
}

TEST(safety, hook_set_timeout_on_jam) {
  th::Rig g;
  g.r.hook.injectJam(true);
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::HOOK_SET, 200));
  int steps = 0;
  while (g.r.ctl->state() == State::HOOK_SET && steps < 100) {
    g.r.step();
    ++steps;
  }
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_ACTUATOR_TIMEOUT);
  CHECK(steps * 20 > static_cast<int>(SafetyLimits{}.hook_set_timeout_ms) - 40);
  CHECK(steps * 20 <= static_cast<int>(SafetyLimits{}.hook_set_timeout_ms) + 40);
}

TEST(safety, retrieval_timeout) {
  sim::RigOptions o;
  o.cfg.actuators.retrieval_speed_m_s = 0.2;  // 1.0 m would take 5 s > 4 s timeout
  th::Rig g(o);
  CHECK(g.start(biteSignal(12000)));
  CHECK(g.runUntil(State::SAFE_STOP, 500));
  CHECK(g.r.safety->faults() & FAULT_ACTUATOR_TIMEOUT);
  CHECK(!(g.r.safety->faults() & FAULT_MOTOR_STALL));
}

TEST(safety, travel_limits_hook_overshoot_and_retrieval_runaway) {
  {
    th::Rig g;
    g.r.hook.injectOvershoot(5.0);  // runs to 17 mm > 15 mm limit
    CHECK(g.start(biteSignal()));
    CHECK(g.runUntil(State::SAFE_STOP, 300));
    CHECK(g.r.safety->faults() & FAULT_TRAVEL_LIMIT);
  }
  {
    sim::RigOptions o;
    o.cfg.safety.retrieval_timeout_ms = 10000;
    th::Rig g(o);
    g.r.retrieval.injectRunaway(true);  // ignores target, keeps winding
    CHECK(g.start(biteSignal(12000)));
    CHECK(g.runUntil(State::SAFE_STOP, 600));
    CHECK(g.r.safety->faults() & FAULT_TRAVEL_LIMIT);
  }
}

TEST(faults, motor_stall_detected_and_motor_stopped) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::RETRIEVE, 200));
  g.r.stepN(15);  // past stall grace
  g.r.retrieval.injectStall(true);
  int steps = 0;
  while (g.r.ctl->state() == State::RETRIEVE && steps < 50) {
    g.r.step();
    ++steps;
  }
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_MOTOR_STALL);
  CHECK(steps * 20 <= static_cast<int>(SafetyLimits{}.stall_window_ms) + 60);
  CHECK(!g.r.retrieval.feedback().active);
}

TEST(faults, encoder_loss_detected_as_stall_without_high_current) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::RETRIEVE, 200));
  g.r.stepN(15);
  g.r.retrieval.injectEncoderLoss(true);  // motor runs, position frozen, normal current
  CHECK(g.runUntil(State::SAFE_STOP, 60));
  CHECK(g.r.safety->faults() & FAULT_MOTOR_STALL);
}

TEST(faults, sensor_disconnect_in_monitor_debounced) {
  th::Rig g;
  CHECK(g.start(quietSignal()));
  g.r.stepN(10);
  g.r.sensor.injectDisconnect(true);
  g.r.stepN(2);
  CHECK(g.r.ctl->state() == State::MONITOR);  // 2 bad samples < sensor_fault_samples (3)
  g.r.step();
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_SENSOR_DISCONNECTED);
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"classification", "result"}), std::string("rejected_sensor_fault"));
  CHECK_EQ(th::field(rec, {"safety", "decision"}), std::string("safe_stop"));
  CHECK_EQ(th::field(rec, {"outcome", "final_state"}), std::string("SAFE_STOP"));
}

TEST(faults, nan_readings_from_driver_claiming_valid) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::CANDIDATE, 100));
  g.r.sensor.injectNaN(true);
  g.r.stepN(4);
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.hook.moveCommands() == 0u);  // never actuated on invalid data
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"classification", "result"}), std::string("rejected_sensor_fault"));
  CHECK(th::field(rec, {"sensor_data", "invalid_samples_omitted"}) != "<missing>");
}

TEST(faults, out_of_range_and_stuck_sensor) {
  {
    th::Rig g;
    CHECK(g.start(quietSignal()));
    g.r.sensor.injectOutOfRange(true, 60.0);
    g.r.stepN(3);
    CHECK(g.r.ctl->state() == State::SAFE_STOP);
    CHECK(g.r.safety->faults() & FAULT_SENSOR_OUT_OF_RANGE);
  }
  {
    th::Rig g;
    CHECK(g.start(quietSignal()));
    g.r.sensor.injectStuck(true);
    g.r.stepN(30);
    CHECK(g.r.ctl->state() == State::SAFE_STOP);
    CHECK(g.r.safety->faults() & FAULT_SENSOR_STUCK);
  }
}

TEST(timeout, comm_loss_timeout_boundary) {
  th::Rig g;
  CHECK(g.start(quietSignal()));
  g.r.stepN(5);
  g.r.comm.setLinkUp(false);
  // link flag itself is a condition; use a fresh rig to test the pure heartbeat timeout below
  g.r.step();
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_COMM_LOSS);

  // heartbeat-only timeout: link reports up but heartbeats stop
  SafetyLimits lim;
  DefaultSafetyController sc(lim);
  SafetyInputs in;
  in.sample.valid = true;
  in.sample.health = SensorHealth::OK;
  in.bus_voltage_v = 11.4;
  in.last_heartbeat_ms = 1000;
  in.now_ms = 1000;
  sc.update(in);
  CHECK(sc.arm(in, nullptr));
  in.now_ms = 1000 + lim.comm_timeout_ms;  // exactly at timeout: still ok
  CHECK_EQ(sc.update(in), 0u);
  in.now_ms = 1000 + lim.comm_timeout_ms + 20;
  CHECK(sc.update(in) & FAULT_COMM_LOSS);
  CHECK(!sc.armed());
}

TEST(timeout, comm_loss_only_latches_when_armed) {
  SafetyLimits lim;
  DefaultSafetyController sc(lim);
  SafetyInputs in;
  in.sample.valid = true;
  in.sample.health = SensorHealth::OK;
  in.bus_voltage_v = 11.4;
  in.comm_link_ok = false;
  in.now_ms = 5000;
  CHECK_EQ(sc.update(in), 0u);          // disarmed: not latched
  CHECK(sc.activeConditions() & FAULT_COMM_LOSS);
  std::string why;
  CHECK(!sc.arm(in, &why));             // but arming is refused
}

TEST(faults, undervoltage_debounced) {
  th::Rig g;
  CHECK(g.start(quietSignal()));
  g.r.stepN(5);
  g.r.health.setVoltage(10.2);
  g.r.stepN(5);  // 80 ms below threshold
  CHECK(g.r.ctl->state() == State::MONITOR);
  g.r.stepN(10);  // > 200 ms
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_UNDERVOLTAGE);
  // a brief dip shorter than the debounce does not trip
  th::Rig h;
  CHECK(h.start(quietSignal()));
  h.r.health.setVoltage(10.2);
  h.r.stepN(5);
  h.r.health.setVoltage(11.4);
  h.r.stepN(20);
  CHECK(h.r.ctl->state() == State::MONITOR);
}

TEST(faults, estop_and_over_temp) {
  th::Rig g;
  CHECK(g.start(biteSignal()));
  CHECK(g.runUntil(State::RETRIEVE, 200));
  g.r.health.setEstop(true);
  g.r.step();
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  CHECK(g.r.safety->faults() & FAULT_E_STOP);
  CHECK(!g.r.retrieval.feedback().active);
  th::Rig h;
  CHECK(h.start(quietSignal()));
  h.r.health.setTemperature(75.0);
  h.r.step();
  CHECK(h.r.safety->faults() & FAULT_OVER_TEMP);
}

TEST(faults, watchdog_reset_boots_idle_disarmed_and_requires_ack) {
  sim::RigOptions o;
  o.watchdog_reset_at_boot = true;
  th::Rig g(o);
  g.r.sensor.load(quietSignal());
  g.r.boot();
  CHECK(g.r.ctl->state() == State::IDLE);
  CHECK(!g.r.safety->armed());
  CHECK(g.r.safety->faults() & FAULT_WATCHDOG_RESET);
  CHECK_EQ(g.r.sink.lines.size(), size_t(1));  // boot record
  Json rec = g.lastRecord();
  CHECK(th::hasFault(rec, "WATCHDOG_RESET"));
  CHECK_EQ(th::field(rec, {"outcome", "final_state"}), std::string("IDLE"));
  g.r.stepN(3);
  CHECK(g.r.ctl->state() == State::IDLE);  // not SAFE_STOP, but...
  std::string why;
  CHECK(!g.r.arm(&why));                   // ...arming refused until acknowledged
  CHECK(g.r.ctl->requestReset(&why));
  CHECK(g.r.arm(&why));
  CHECK(g.r.ctl->state() == State::MONITOR);
}

TEST(recovery, safe_stop_exits_only_via_reset_with_faults_cleared) {
  th::Rig g;
  CHECK(g.start(quietSignal(20000)));
  g.r.stepN(5);
  g.r.sensor.injectDisconnect(true);
  g.r.stepN(3);
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  std::string why;
  CHECK(!g.r.arm(&why));                   // cannot arm from SAFE_STOP
  CHECK(!g.r.ctl->requestReset(&why));     // fault still present
  CHECK(g.r.ctl->state() == State::SAFE_STOP);
  g.r.stepN(50);
  CHECK(g.r.ctl->state() == State::SAFE_STOP);  // no automatic exit
  g.r.sensor.injectDisconnect(false);
  g.r.step();
  CHECK(g.r.ctl->requestReset(&why));
  CHECK(g.r.ctl->state() == State::IDLE);
  CHECK(!g.r.safety->armed());             // reset lands in IDLE, disarmed
  g.r.step();
  CHECK(g.r.arm(&why));
  CHECK(g.r.ctl->state() == State::MONITOR);
}

TEST(recovery, after_motor_stall_reset_then_new_cycle_works) {
  th::Rig g;
  std::vector<double> v;
  SynthSignal a = biteSignal(5000), q = quietSignal(1500), b = biteSignal(8000);
  v.insert(v.end(), a.tension_n.begin(), a.tension_n.end());
  v.insert(v.end(), q.tension_n.begin(), q.tension_n.end());
  v.insert(v.end(), b.tension_n.begin(), b.tension_n.end());
  CHECK(g.start(th::fromValues(v)));
  CHECK(g.runUntil(State::RETRIEVE, 200));
  g.r.stepN(15);
  g.r.retrieval.injectStall(true);
  CHECK(g.runUntil(State::SAFE_STOP, 50));
  // advance into the quiet section, clear the stall, reset, re-arm
  while (g.r.sensor.index() < a.tension_n.size() + 10) g.r.step();
  g.r.retrieval.injectStall(false);
  std::string why;
  CHECK(g.r.ctl->requestReset(&why));
  g.r.step();
  CHECK(g.r.arm(&why));
  CHECK(g.runUntil(State::IDLE, 600));
  CHECK_EQ(g.r.sink.lines.size(), size_t(2));
  Json rec = g.lastRecord();
  CHECK_EQ(th::field(rec, {"outcome", "milestones", "retrieval_completed"}), std::string("true"));
  // hash chain across records
  Json first;
  parseJson(g.r.sink.lines[0], first);
  CHECK_EQ(th::field(rec, {"integrity", "prev_record_sha256"}), th::field(first, {"integrity", "record_sha256"}));
}

TEST(faults, scenario_fault_runs_end_in_safe_stop) {
  struct E { const char* sc; uint32_t fault; };
  const E es[] = {{"excessive_tension", FAULT_OVER_TENSION},
                  {"motor_stall", FAULT_MOTOR_STALL},
                  {"comm_loss", FAULT_COMM_LOSS}};
  for (const auto& e : es) {
    for (uint32_t seed : {1u, 42u, 1337u}) {
      auto r = sim::runScenario(e.sc, seed);
      CHECK(r.final_state == State::SAFE_STOP);
      CHECK(r.faults & e.fault);
      CHECK(r.reached_retrieve);
    }
  }
  auto sf = sim::runScenario("sensor_failure", 42);
  CHECK(sf.final_state == State::SAFE_STOP);
  CHECK(sf.faults & (FAULT_SENSOR_STUCK | FAULT_SENSOR_OUT_OF_RANGE));
}

TEST(determinism, same_scenario_seed_gives_identical_records) {
  for (const char* sc : {"bite", "motor_stall", "rod_vibration"}) {
    auto a = sim::runScenario(sc, 99), b = sim::runScenario(sc, 99);
    CHECK(a.lines == b.lines);
  }
}
