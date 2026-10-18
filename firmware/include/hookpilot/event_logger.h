// Event records (packages/event-schema/hookpilot-event.schema.json, schema_version 1.0.0) and the
// JSON-lines logger: canonical JSON, sha256 record hash, hash chain. fish_landed is never inferred.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "hookpilot/config.h"
#include "hookpilot/interfaces.h"
#include "hookpilot/json.h"
#include "hookpilot/types.h"

namespace hp {

struct RequestEntry {
  ActuatorRequest req;
  bool permitted = false;
  std::string decision_reason;
};

struct FeedbackEntry {
  std::string actuator;
  uint32_t t_ms = 0;
  double position = 0;
  bool position_valid = true;
  double current_a = 0;
  bool current_valid = true;
  double tension_n = 0;
  bool tension_valid = true;
  std::string status;
};

struct EventRecord {
  std::string event_id;
  std::string device_id;
  std::string firmware_version = kFirmwareVersion;
  std::string hardware_revision;
  // source
  std::string mode = "simulation";  // simulation | bench | field
  std::string scenario;             // empty -> omitted
  bool has_seed = false;
  int64_t seed = 0;
  bool has_ground_truth = false;
  std::string ground_truth_label;   // empty with has_ground_truth -> null
  std::string generator;            // source.generator, same field as Python/web records; empty -> omitted
  // timestamp
  uint32_t start_ms = 0, end_ms = 0;
  int64_t utc_epoch_ms_at_boot = 0;  // UTC of monotonic 0 (simulated or from NTP/RTC)
  std::string clock_quality = "host_clock_simulated";
  // calibration
  CalibrationInfo calibration;
  // sensor data
  double sample_rate_hz = kSampleRateHz;
  std::vector<std::pair<uint32_t, double>> samples;
  int invalid_samples_omitted = 0;
  // features / classification
  Features features;
  Classification classification;
  bool has_trigger = false;          // classification.trigger_t_ms (a candidate was opened)
  uint32_t trigger_t_ms = 0;
  bool has_decision = false;         // classification.decision_t_ms
  uint32_t decision_t_ms = 0;
  std::string classifier = kClassifierId;
  ClassifierThresholds thresholds;
  // safety
  Decision decision = Decision::DENY;
  bool armed = false;
  Json interlocks = Json::object();
  uint32_t faults = 0;
  Json fault_latched_t_ms = Json::object();  // fault -> t_ms it latched (latched faults only)
  // actuation
  std::vector<RequestEntry> requested;
  std::vector<FeedbackEntry> feedback;
  bool simulated = true;
  // outcome
  State final_state = State::MONITOR;
  std::string stop_reason;
  bool bite_detected = false, hook_set_completed = false, retrieval_completed = false;
  std::string fish_landed = "unknown";  // NEVER inferred; only an independent observer may change it
  std::string test_outcome = "not_evaluated";
  // integrity (filled by the logger)
  std::string prev_record_sha256;  // empty -> null
  std::string record_sha256;
};

std::string formatUtc(int64_t epoch_ms);                 // YYYY-MM-DDTHH:MM:SS.mmmZ
constexpr int64_t kSimulatedEpochMs = 1767225600000LL;   // 2026-01-01T00:00:00Z (simulated clock)

Json thresholdsJson(const ClassifierThresholds& t);
Json recordToJson(const EventRecord& r);  // integrity.record_sha256 as currently set
std::string computeRecordHash(Json record); // sha256 of canonical JSON with record_sha256 = ""

class JsonLinesEventLogger : public EventLogger {
 public:
  explicit JsonLinesEventLogger(LineSink* sink) : sink_(sink) {}
  std::string log(EventRecord& rec) override;
  std::string lastHash() const override { return last_hash_; }
  uint32_t count() const { return count_; }

 private:
  LineSink* sink_;
  std::string last_hash_;
  uint32_t count_ = 0;
};

}  // namespace hp
