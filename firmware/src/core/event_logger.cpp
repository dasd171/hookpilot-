#include "hookpilot/event_logger.h"

#include <cmath>
#include <cstdio>

#include "hookpilot/sha256.h"

namespace hp {

namespace {
// Round for logging (keeps lines short and avoids exponent notation for tiny values).
double rnd(double v, int decimals = 4) {
  if (!std::isfinite(v)) return 0.0;
  const double m = std::pow(10.0, decimals);
  double r = std::round(v * m) / m;
  if (r == 0) r = 0;  // no -0
  return r;
}

// Days-from-civil inverse (H. Hinnant), proleptic Gregorian, UTC.
void civilFromDays(int64_t z, int& y, unsigned& m, unsigned& d) {
  z += 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = static_cast<unsigned>(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const int64_t yy = static_cast<int64_t>(yoe) + era * 400;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp < 10 ? mp + 3 : mp - 9;
  y = static_cast<int>(yy + (m <= 2));
}
}  // namespace

std::string formatUtc(int64_t epoch_ms) {
  int64_t days = epoch_ms / 86400000;
  int64_t rem = epoch_ms % 86400000;
  if (rem < 0) {
    rem += 86400000;
    --days;
  }
  int y;
  unsigned mo, d;
  civilFromDays(days, y, mo, d);
  const int h = static_cast<int>(rem / 3600000), mi = static_cast<int>((rem / 60000) % 60),
            s = static_cast<int>((rem / 1000) % 60), ms = static_cast<int>(rem % 1000);
  char buf[40];
  std::snprintf(buf, sizeof(buf), "%04d-%02u-%02uT%02d:%02d:%02d.%03dZ", y, mo, d, h, mi, s, ms);
  return buf;
}

Json thresholdsJson(const ClassifierThresholds& t) {
  Json j = Json::object();
  j["label"] = kProvisionalLabel;
  j["trigger_delta_n"] = t.trigger_delta_n;
  j["confirm_min_ms"] = t.confirm_min_ms;
  j["min_rate_n_per_s"] = t.min_rate_n_per_s;
  j["max_osc"] = t.max_osc;
  j["manual_handling_delta_n"] = t.manual_handling_delta_n;
  j["max_duration_ms"] = t.max_duration_ms;
  j["decision_window_ms"] = t.decision_window_ms;
  j["osc_band_n"] = t.osc_band_n;
  j["baseline_alpha"] = t.baseline_alpha;
  j["smoothing_alpha"] = t.smoothing_alpha;
  j["rate_lag_samples"] = t.rate_lag_samples;
  j["variance_window_samples"] = t.variance_window_samples;
  j["detrend_window_samples"] = t.detrend_window_samples;
  j["release_ratio"] = t.release_ratio;
  j["rearm_quiet_ms"] = t.rearm_quiet_ms;
  return j;
}

Json recordToJson(const EventRecord& r) {
  Json j = Json::object();
  j["schema_version"] = "1.0.0";
  j["event_id"] = r.event_id;

  Json dev = Json::object();
  dev["device_id"] = r.device_id;
  dev["firmware_version"] = r.firmware_version;
  if (!r.hardware_revision.empty()) dev["hardware_revision"] = r.hardware_revision;
  j["device"] = dev;

  Json src = Json::object();
  src["mode"] = r.mode;
  if (!r.scenario.empty()) src["scenario"] = r.scenario;
  if (r.has_seed) src["seed"] = static_cast<long long>(r.seed);
  if (r.has_ground_truth)
    src["ground_truth_label"] = r.ground_truth_label.empty() ? Json() : Json(r.ground_truth_label);
  if (!r.generator.empty()) src["generator"] = r.generator;
  j["source"] = src;

  Json ts = Json::object();
  ts["start_utc"] = formatUtc(r.utc_epoch_ms_at_boot + r.start_ms);
  ts["end_utc"] = formatUtc(r.utc_epoch_ms_at_boot + r.end_ms);
  ts["monotonic_ms"] = r.start_ms;
  ts["clock_quality"] = r.clock_quality;
  j["timestamp"] = ts;

  Json cal = Json::object();
  cal["calibration_version"] = r.calibration.calibration_version;
  cal["scale_n_per_count"] = r.calibration.scale_n_per_count;
  cal["offset_counts"] = r.calibration.offset_counts;
  cal["calibrated_utc"] = r.calibration.calibrated_utc.empty() ? Json() : Json(r.calibration.calibrated_utc);
  j["calibration"] = cal;

  Json sd = Json::object();
  sd["sample_rate_hz"] = r.sample_rate_hz;
  sd["units"] = "N";
  Json samples = Json::array();
  for (const auto& s : r.samples) {
    Json pair = Json::array();
    pair.push(s.first);
    pair.push(rnd(s.second));
    samples.push(pair);
  }
  sd["samples"] = samples;
  sd["raw_data_ref"] = Json();
  sd["raw_data_sha256"] = Json();
  if (r.invalid_samples_omitted > 0) sd["invalid_samples_omitted"] = r.invalid_samples_omitted;
  j["sensor_data"] = sd;

  const Features& f = r.features;
  Json fe = Json::object();
  fe["baseline_n"] = rnd(f.baseline_n);
  fe["peak_delta_n"] = rnd(f.peak_delta_n);
  fe["max_rate_n_per_s"] = rnd(f.max_rate_n_per_s);
  fe["duration_above_ms"] = rnd(f.duration_above_ms, 0);
  fe["continuous_above_ms"] = rnd(f.continuous_above_ms, 0);
  fe["short_term_variance"] = rnd(f.short_term_variance);
  fe["oscillation_count"] = f.oscillation_count;
  fe["sensor_health"] = toString(f.sensor_health);
  j["features"] = fe;

  Json cl = Json::object();
  cl["result"] = toString(r.classification.result);
  cl["reason"] = r.classification.reason;
  Json trace = Json::array();
  for (const auto& t : r.classification.rule_trace) trace.push(t);
  cl["rule_trace"] = trace;
  cl["classifier"] = r.classifier;
  cl["thresholds"] = thresholdsJson(r.thresholds);
  j["classification"] = cl;

  Json sf = Json::object();
  sf["decision"] = toString(r.decision);
  sf["armed"] = r.armed;
  sf["interlocks"] = r.interlocks;
  Json faults = Json::array();
  for (const auto& s : faultList(r.faults)) faults.push(s);
  sf["faults"] = faults;
  j["safety"] = sf;

  Json act = Json::object();
  Json req = Json::array();
  for (const auto& e : r.requested) {
    Json q = Json::object();
    q["actuator"] = toString(e.req.actuator);
    q["command"] = e.req.command;
    Json lim = Json::object();
    lim[e.req.actuator == ActuatorId::HOOK_SET ? "target_mm" : "target_m"] = rnd(e.req.target);
    lim[e.req.actuator == ActuatorId::HOOK_SET ? "speed_mm_per_s" : "speed_m_per_s"] = rnd(e.req.speed);
    q["limit"] = lim;
    q["t_ms"] = e.req.t_ms;
    q["decision"] = e.permitted ? "permit" : "deny";
    q["decision_reason"] = e.decision_reason;
    req.push(q);
  }
  act["requested"] = req;
  Json fb = Json::array();
  for (const auto& e : r.feedback) {
    Json q = Json::object();
    q["actuator"] = e.actuator;
    q["t_ms"] = e.t_ms;
    q["position"] = e.position_valid ? Json(rnd(e.position)) : Json();
    q["current_a"] = e.current_valid ? Json(rnd(e.current_a)) : Json();
    q["tension_n"] = e.tension_valid ? Json(rnd(e.tension_n)) : Json();
    q["status"] = e.status;
    fb.push(q);
  }
  act["feedback"] = fb;
  act["simulated"] = r.simulated;
  j["actuation"] = act;

  Json oc = Json::object();
  oc["final_state"] = toString(r.final_state);
  oc["stop_reason"] = r.stop_reason;
  Json ms = Json::object();
  ms["bite_detected"] = r.bite_detected;
  ms["hook_set_completed"] = r.hook_set_completed;
  ms["retrieval_completed"] = r.retrieval_completed;
  ms["fish_landed"] = r.fish_landed;
  oc["milestones"] = ms;
  oc["test_outcome"] = r.test_outcome;
  j["outcome"] = oc;

  Json in = Json::object();
  in["hash_algorithm"] = "sha256";
  in["record_sha256"] = r.record_sha256;
  in["prev_record_sha256"] = r.prev_record_sha256.empty() ? Json() : Json(r.prev_record_sha256);
  in["signature"] = Json();  // prototype: no secure element yet
  j["integrity"] = in;
  return j;
}

std::string computeRecordHash(Json record) {
  record["integrity"]["record_sha256"] = "";
  return sha256Hex(record.dump());
}

std::string JsonLinesEventLogger::log(EventRecord& rec) {
  rec.fish_landed = rec.fish_landed.empty() ? "unknown" : rec.fish_landed;
  rec.prev_record_sha256 = last_hash_;
  rec.record_sha256.clear();
  Json j = recordToJson(rec);
  rec.record_sha256 = computeRecordHash(j);
  j["integrity"]["record_sha256"] = rec.record_sha256;
  const std::string line = j.dump();
  last_hash_ = rec.record_sha256;
  ++count_;
  if (sink_) sink_->writeLine(line);
  return line;
}

}  // namespace hp
