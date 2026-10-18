#include "hookpilot/controller.h"

#include <cmath>
#include <cstdio>

namespace hp {

namespace {
std::string joinFaults(uint32_t m) {
  std::string s;
  for (const auto& f : faultList(m)) {
    if (!s.empty()) s += ",";
    s += f;
  }
  return s.empty() ? "none" : s;
}
constexpr uint32_t kCriticalMask = ~static_cast<uint32_t>(FAULT_WATCHDOG_RESET);
}  // namespace

HookPilotController::HookPilotController(const ControllerConfig& cfg, ControllerDeps deps)
    : cfg_(cfg), d_(deps), gate_(deps.safety, deps.hook, deps.retrieval) {}

void HookPilotController::begin() {
  gate_.stopAll();  // outputs off before anything else
  state_ = State::IDLE;
  d_.safety.disarm();
  d_.watchdog.begin(1000);
  d_.sensor.begin();
  d_.hook.begin();
  d_.retrieval.begin();
  d_.health.begin();
  d_.comm.begin();
  d_.signal.reset();
  const uint32_t now = d_.clock.nowMs();
  state_since_ = now;
  if (d_.watchdog.lastResetWasWatchdog()) {
    d_.safety.latchFault(FAULT_WATCHDOG_RESET);
    // Boot record: device came back from a watchdog reset, disarmed, in IDLE.
    gatherInputs(now);
    openEvent(now);
    ev_.start_ms = now;
    ev_.samples.clear();
    ev_.classification.result = ClassResult::NO_EVENT;
    ev_.classification.reason =
        "Boot after watchdog reset: device is IDLE and disarmed; operator reset required before arming.";
    ev_.classification.rule_trace.push_back("boot: watchdog reset cause detected -> latch WATCHDOG_RESET");
    ev_decided_ = true;
    closeEvent(State::IDLE, "watchdog_reset_boot: disarmed, awaiting operator reset");
  }
}

void HookPilotController::transition(State to, const std::string& why) {
  if (to == state_) return;
  if (transitions_.size() < 512) transitions_.push_back({in_.now_ms, state_, to, why});
  state_ = to;
  state_since_ = in_.now_ms;
}

void HookPilotController::gatherInputs(uint32_t now) {
  in_.now_ms = now;
  in_.sample = sample_;
  in_.hook = gate_.hookFeedback();
  in_.retrieval = gate_.retrievalFeedback();
  in_.bus_voltage_v = d_.health.busVoltageV();
  in_.temperature_c = d_.health.temperatureC();
  in_.estop_active = d_.health.estopActive();
  in_.comm_link_ok = d_.comm.linkUp();
  in_.last_heartbeat_ms = d_.comm.lastHeartbeatMs();
}

void HookPilotController::tick() {
  const uint32_t now = d_.clock.nowMs();
  ++ticks_;
  d_.watchdog.feed();
  d_.comm.poll(now);
  gate_.update(now);

  const TensionReading r = d_.sensor.read();
  sample_ = d_.signal.process(r, now);
  ring_[ring_head_] = {now, sample_.raw, sample_.valid};
  ring_head_ = (ring_head_ + 1) % kRing;
  if (ring_n_ < kRing) ++ring_n_;
  if (ev_open_) {
    if (static_cast<int>(ev_.samples.size()) < cfg_.max_record_samples) {
      if (sample_.valid && std::isfinite(sample_.raw)) ev_.samples.emplace_back(now, sample_.raw);
      else if (!std::isfinite(sample_.raw)) ++ev_.invalid_samples_omitted;
      else ev_.samples.emplace_back(now, sample_.raw);  // finite but invalid (e.g. saturated): keep value
    }
  }

  gatherInputs(now);
  const uint32_t faults = d_.safety.update(in_);
  if ((faults & kCriticalMask) && state_ != State::SAFE_STOP) {
    enterSafeStop("safe_stop: " + joinFaults(faults & kCriticalMask));
    return;
  }

  switch (state_) {
    case State::IDLE:
      gate_.stopAll();
      if (cfg_.observe_when_disarmed) monitorStep(now);  // observation only: never requests actuation
      break;

    case State::SAFE_STOP:
      gate_.stopAll();
      break;

    case State::MONITOR:
      monitorStep(now);
      break;

    case State::CANDIDATE:
      if (d_.detector.update(sample_)) {
        transition(State::CONFIRM, "candidate window complete or early-exit rule hit");
        decideCandidate();
      }
      break;

    case State::CONFIRM:
      decideCandidate();
      break;

    case State::HOOK_SET: {
      const ActuatorFeedback fb = in_.hook;
      if (ticks_ % 5 == 0) recordFeedback("hook_set", fb);
      if (!hook_done_ && fb.at_target && !fb.active) {
        recordFeedback("hook_set", fb);
        ev_.hook_set_completed = true;
        hook_done_ = true;
        hook_done_ms_ = now;
      }
      // Minimum dead time between hook-set stop and retrieval start (hook_to_retrieval_gap_ms).
      if (hook_done_ && now - hook_done_ms_ >= cfg_.safety.hook_to_retrieval_gap_ms) {
        if (gatedRequest(ActuatorId::RETRIEVAL, "retrieve", cfg_.actuators.retrieval_target_m,
                         cfg_.actuators.retrieval_speed_m_s)) {
          transition(State::RETRIEVE, "hook set stroke complete; retrieval permitted");
        } else {
          enterSafeStop("safe_stop: retrieval denied after hook set (" + ev_.requested.back().decision_reason + ")");
        }
      }
      break;
    }

    case State::RETRIEVE: {
      const ActuatorFeedback fb = in_.retrieval;
      if (ticks_ % 5 == 0) recordFeedback("retrieval", fb);
      if (fb.at_target && !fb.active) {
        gate_.stopRetrieval();
        recordFeedback("retrieval", gate_.retrievalFeedback());
        ev_.retrieval_completed = true;
        transition(State::VERIFY, "retrieval travel reached");
        hook_returning_ = gatedRequest(ActuatorId::HOOK_SET, "return_to_rest", cfg_.safety.hook_set_travel_min_mm,
                                       cfg_.actuators.hook_set_speed_mm_s);
      }
      break;
    }

    case State::VERIFY: {
      const ActuatorFeedback fb = in_.hook;
      const bool hook_idle = !fb.active;
      if (now - state_since_ >= cfg_.actuators.verify_ms && hook_idle) {
        recordFeedback("hook_set", fb);
        closeEvent(State::IDLE,
                   "cycle_complete: actuators stopped, disarmed awaiting operator; fish_landed not inferred");
        d_.safety.disarm();
        d_.signal.freezeBaseline(false);
        awaiting_rearm_ = true;  // IDLE observation waits for a quiet line before the next candidate
        transition(State::IDLE, "cycle complete -> disarmed");
      }
      break;
    }
  }
}

void HookPilotController::monitorStep(uint32_t now) {
  if (awaiting_rearm_) {
    if (d_.detector.rearmed(sample_)) awaiting_rearm_ = false;
    return;
  }
  if (d_.detector.shouldTrigger(sample_)) {
    d_.signal.freezeBaseline(true);
    openEvent(now);
    ev_.has_trigger = true;
    ev_.trigger_t_ms = now;
    d_.detector.beginCandidate(sample_);
    transition(State::CANDIDATE, d_.safety.armed() ? "delta >= trigger_delta_n"
                                                   : "delta >= trigger_delta_n (observation only: disarmed)");
  }
}

void HookPilotController::decideCandidate() {
  Classification c = d_.detector.decide();
  ev_.classification = c;
  ev_.features = c.features;
  ev_decided_ = true;
  ev_.has_decision = true;
  ev_.decision_t_ms = in_.now_ms;
  const bool armed = d_.safety.armed();
  const State rest = armed ? State::MONITOR : State::IDLE;
  if (c.result == ClassResult::BITE_CANDIDATE_CONFIRMED) {
    ev_.bite_detected = true;
    if (!armed) {
      // Observation only (disarmed): nothing is requested, nothing can move.
      d_.signal.freezeBaseline(false);
      awaiting_rearm_ = true;
      closeEvent(State::IDLE, "disarmed_observation_only: no actuation requested");
      transition(State::IDLE, "observation only (disarmed): no actuation requested");
      return;
    }
    hook_done_ = false;
    if (gatedRequest(ActuatorId::HOOK_SET, "stroke_to", cfg_.actuators.hook_set_target_mm,
                     cfg_.actuators.hook_set_speed_mm_s)) {
      transition(State::HOOK_SET, "bite_candidate_confirmed; hook set permitted by safety");
    } else {
      // Nothing moved. Return to MONITOR with the denial on record.
      d_.signal.freezeBaseline(false);
      awaiting_rearm_ = true;
      closeEvent(State::MONITOR, "hook_set_denied: " + ev_.requested.back().decision_reason);
      transition(State::MONITOR, "hook set denied by safety");
    }
    return;
  }
  if (c.result == ClassResult::REJECTED_SENSOR_FAULT) {
    enterSafeStop(std::string("safe_stop: sensor fault during candidate (") + toString(c.features.sensor_health) + ")");
    return;
  }
  d_.signal.freezeBaseline(false);
  awaiting_rearm_ = true;
  closeEvent(rest, std::string("classifier_rejected: ") + toString(c.result));
  transition(rest, std::string("classifier: ") + toString(c.result));
}

bool HookPilotController::gatedRequest(ActuatorId a, const char* command, double target, double speed) {
  ActuatorRequest req;
  req.actuator = a;
  req.command = command;
  req.target = target;
  req.speed = speed;
  req.t_ms = in_.now_ms;
  in_.hook = gate_.hookFeedback();
  in_.retrieval = gate_.retrievalFeedback();
  const SafetyDecision dec = gate_.request(req, in_);
  if (request_log_.size() < 100000) request_log_.push_back({req, dec.permitted, dec.reason});
  if (ev_open_) {
    ev_.requested.push_back({req, dec.permitted, dec.reason});
    if (dec.permitted) {
      ev_any_permit_ = true;
      recordFeedback(toString(a), a == ActuatorId::HOOK_SET ? gate_.hookFeedback() : gate_.retrievalFeedback());
    }
  }
  return dec.permitted;
}

void HookPilotController::recordFeedback(const char* actuator, const ActuatorFeedback& fb) {
  if (!ev_open_ || static_cast<int>(ev_.feedback.size()) >= cfg_.max_record_feedback) return;
  FeedbackEntry e;
  e.actuator = actuator;
  e.t_ms = in_.now_ms;
  e.position = fb.position;
  e.position_valid = fb.position_valid;
  e.current_a = fb.current_a;
  e.current_valid = fb.current_valid;
  e.tension_n = sample_.raw;
  e.tension_valid = sample_.valid && std::isfinite(sample_.raw);
  e.status = fb.status;
  ev_.feedback.push_back(e);
}

void HookPilotController::openEvent(uint32_t t_trigger) {
  ev_ = EventRecord{};
  ev_open_ = true;
  ev_decided_ = false;
  ev_any_permit_ = false;
  ++event_counter_;
  char id[96];
  const uint32_t rnd = rand32_ ? rand32_() : (event_counter_ * 2654435761u);
  std::snprintf(id, sizeof(id), "%s-%06u-%08x", cfg_.identity.device_id, static_cast<unsigned>(event_counter_),
                static_cast<unsigned>(rnd));
  ev_.event_id = id;
  ev_.device_id = cfg_.identity.device_id;
  ev_.hardware_revision = cfg_.identity.hardware_revision;
  ev_.mode = src_.mode;
  ev_.scenario = src_.scenario;
  ev_.has_seed = src_.has_seed;
  ev_.seed = src_.seed;
  ev_.has_ground_truth = src_.has_ground_truth;
  ev_.ground_truth_label = src_.ground_truth_label;
  ev_.generator = src_.generator;
  ev_.utc_epoch_ms_at_boot = src_.utc_epoch_ms_at_boot;
  ev_.clock_quality = src_.clock_quality;
  ev_.simulated = src_.simulated_actuators;
  ev_.calibration = d_.sensor.calibration();
  ev_.thresholds = d_.detector.thresholds();
  ev_.classifier = d_.detector.name();
  ev_.armed = d_.safety.armed();
  ev_.start_ms = t_trigger;
  // pre-roll samples (including the trigger sample, which is the newest in the ring)
  const uint32_t from = t_trigger >= cfg_.preroll_ms ? t_trigger - cfg_.preroll_ms : 0;
  for (int k = ring_n_ - 1; k >= 0; --k) {
    const Raw& rr = ring_[(ring_head_ - 1 - k + kRing) % kRing];
    if (rr.t < from || rr.t > t_trigger) continue;
    if (std::isfinite(rr.v)) ev_.samples.emplace_back(rr.t, rr.v);
    else ++ev_.invalid_samples_omitted;
  }
  if (!ev_.samples.empty()) ev_.start_ms = ev_.samples.front().first;
}

Json HookPilotController::interlocksJson() const {
  Json j = Json::object();
  const auto& L = d_.safety.limits();
  j["estop_clear"] = !in_.estop_active;
  j["sensor_valid"] = in_.sample.valid;
  j["comm_link_ok"] = in_.comm_link_ok && (in_.now_ms - in_.last_heartbeat_ms <= L.comm_timeout_ms);
  j["voltage_ok"] = in_.bus_voltage_v >= L.undervoltage_v;
  j["bus_voltage_v"] = in_.bus_voltage_v;
  j["hook_set_active"] = in_.hook.active;
  j["retrieval_active"] = in_.retrieval.active;
  j["mutual_exclusion"] = "enforced";
  j["classifier_can_command_actuators"] = false;
  j["retrieval_force_limit_n"] = L.retrieval_force_limit_n;
  j["hard_max_tension_n"] = L.hard_max_tension_n;
  j["hook_set_travel_limit_mm"] = L.hook_set_travel_limit_mm;
  j["retrieval_travel_limit_m"] = L.retrieval_travel_limit_m;
  j["hook_set_timeout_ms"] = L.hook_set_timeout_ms;
  j["retrieval_timeout_ms"] = L.retrieval_timeout_ms;
  j["comm_timeout_ms"] = L.comm_timeout_ms;
  j["undervoltage_v"] = L.undervoltage_v;
  j["limits_label"] = kProvisionalLabel;
  return j;
}

void HookPilotController::closeEvent(State final_state, const std::string& stop_reason) {
  if (!ev_open_) return;
  ev_.end_ms = in_.now_ms;
  if (ev_.end_ms < ev_.start_ms) ev_.end_ms = ev_.start_ms;
  ev_.final_state = final_state;
  ev_.stop_reason = stop_reason;
  ev_.faults = d_.safety.faults() | (final_state == State::SAFE_STOP ? d_.safety.activeConditions() : 0);
  ev_.fault_latched_t_ms = Json::object();
  for (int b = 0; b < kFaultBitCount; ++b) {
    const uint32_t bit = 1u << b;
    if (d_.safety.faults() & bit) ev_.fault_latched_t_ms[faultName(bit)] = d_.safety.latchTimeMs(bit);
  }
  ev_.interlocks = interlocksJson();
  if (final_state == State::SAFE_STOP) ev_.decision = Decision::SAFE_STOP;
  else if (ev_any_permit_) ev_.decision = Decision::PERMIT;
  else ev_.decision = Decision::DENY;
  ev_.fish_landed = "unknown";  // never inferred from actuator completion
  d_.logger.log(ev_);
  ++events_logged_;
  ev_open_ = false;
}

void HookPilotController::enterSafeStop(const std::string& why) {
  gate_.stopAll();
  d_.safety.disarm();
  if (!ev_open_) {
    openEvent(in_.now_ms);
    ev_.features.baseline_n = sample_.baseline;
    ev_.features.sensor_health = sample_.health;
    ev_.features.short_term_variance = sample_.variance;
    if (!sample_.valid) {
      ev_.classification.result = ClassResult::REJECTED_SENSOR_FAULT;
      ev_.classification.reason = std::string("Sensor health '") + toString(sample_.health) +
                                  "' while " + toString(state_) + "; safety controller entered SAFE_STOP.";
    } else {
      ev_.classification.result = ClassResult::NO_EVENT;
      ev_.classification.reason = std::string("No bite candidate; safety fault while ") + toString(state_) + ".";
    }
    ev_.classification.rule_trace.push_back(std::string("state=") + toString(state_) + " -> " + why);
    ev_decided_ = true;
    ev_.has_decision = true;
    ev_.decision_t_ms = in_.now_ms;
  } else if (!ev_decided_) {
    Classification c = d_.detector.decide();
    ev_.classification = c;
    ev_.features = c.features;
    ev_decided_ = true;
    ev_.has_decision = true;
    ev_.decision_t_ms = in_.now_ms;
    ev_.classification.rule_trace.push_back("interrupted by safety: " + why);
  } else {
    ev_.classification.rule_trace.push_back(std::string("state=") + toString(state_) + " interrupted: " + why);
  }
  recordFeedback("hook_set", gate_.hookFeedback());
  recordFeedback("retrieval", gate_.retrievalFeedback());
  closeEvent(State::SAFE_STOP, why);
  d_.signal.freezeBaseline(false);
  awaiting_rearm_ = false;
  transition(State::SAFE_STOP, why);
}

bool HookPilotController::requestArm(std::string* why) {
  if (state_ != State::IDLE) {
    if (why) *why = std::string("arm only from IDLE (state=") + toString(state_) + ")";
    return false;
  }
  gatherInputs(d_.clock.nowMs());
  if (!d_.safety.arm(in_, why)) return false;
  awaiting_rearm_ = false;
  d_.signal.freezeBaseline(false);
  transition(State::MONITOR, "armed by operator");
  return true;
}

void HookPilotController::requestDisarm() {
  gate_.stopAll();
  d_.safety.disarm();
  gatherInputs(d_.clock.nowMs());
  if (state_ == State::SAFE_STOP) return;  // only reset leaves SAFE_STOP
  if (ev_open_) {
    if (!ev_decided_) {
      Classification c = d_.detector.decide();
      ev_.classification = c;
      ev_.features = c.features;
      ev_decided_ = true;
      ev_.has_decision = true;
      ev_.decision_t_ms = in_.now_ms;
      ev_.classification.rule_trace.push_back("interrupted by operator_disarm");
    } else {
      ev_.classification.rule_trace.push_back(std::string("state=") + toString(state_) +
                                              " interrupted: operator_disarm");
    }
    if (ev_any_permit_) {
      recordFeedback("hook_set", gate_.hookFeedback());
      recordFeedback("retrieval", gate_.retrievalFeedback());
    }
    closeEvent(State::IDLE, "operator_disarm");
    awaiting_rearm_ = true;  // IDLE observation waits for a quiet line before the next candidate
  }
  d_.signal.freezeBaseline(false);
  transition(State::IDLE, "disarmed by operator");
}

void HookPilotController::logNoEventSummary() {
  if (ev_open_) return;
  openEvent(in_.now_ms);
  ev_.features.baseline_n = sample_.baseline;
  ev_.features.sensor_health = sample_.health;
  ev_.features.short_term_variance = sample_.variance;
  ev_.classification.result = ClassResult::NO_EVENT;
  char b[200];
  std::snprintf(b, sizeof(b), "No candidate in the observation window: delta never reached trigger_delta_n %.3f N.",
                d_.detector.thresholds().trigger_delta_n);
  ev_.classification.reason = b;
  std::snprintf(b, sizeof(b), "t=%ums end of observation window: no candidate opened (state=%s)",
                static_cast<unsigned>(in_.now_ms), toString(state_));
  ev_.classification.rule_trace.push_back(b);
  ev_decided_ = true;
  closeEvent(state_, "no_event: nothing to actuate");
}

bool HookPilotController::requestReset(std::string* why) {
  gatherInputs(d_.clock.nowMs());
  if (state_ != State::SAFE_STOP && state_ != State::IDLE) {
    if (why) *why = "reset only from SAFE_STOP or IDLE";
    return false;
  }
  if (!d_.safety.clearFaults(in_)) {
    if (why) *why = "faults still present: " + joinFaults(d_.safety.faults());
    return false;
  }
  gate_.stopAll();
  gate_.resetRetrievalPosition();
  d_.signal.reset();
  awaiting_rearm_ = false;
  if (why) *why = "faults cleared";
  transition(State::IDLE, "explicit reset with faults cleared");
  return true;
}

}  // namespace hp
