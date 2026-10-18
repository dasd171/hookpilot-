// Signal filtering, trigger thresholds and deterministic-baseline-v1 classification.
#include <cmath>
#include <limits>
#include <map>

#include "hookpilot/bite_detector.h"
#include "hookpilot/signal_processor.h"
#include "test_framework.h"
#include "test_helpers.h"

using namespace hp;

namespace {
TensionReading rd(double v) {
  TensionReading r;
  r.newtons = v;
  r.valid = std::isfinite(v);
  r.health = r.valid ? SensorHealth::OK : SensorHealth::DISCONNECTED;
  return r;
}
}  // namespace

TEST(signal, baseline_initialises_and_tracks_slowly) {
  ClassifierThresholds th;
  SafetyLimits lim;
  BaselineSignalProcessor sp(th, lim);
  ProcessedSample s = sp.process(rd(0.5), 0);
  CHECK(s.valid);
  CHECK_NEAR(s.baseline, 0.5, 1e-12);
  CHECK_NEAR(s.delta, 0.0, 1e-12);
  // step to 1.5: baseline moves by alpha per sample, delta stays ~1.0 at first
  s = sp.process(rd(1.5), 20);
  CHECK_NEAR(s.baseline, 0.5 + th.baseline_alpha * 1.0, 1e-12);
  CHECK_NEAR(s.delta, 1.0 - th.baseline_alpha, 1e-12);
  CHECK_NEAR(s.smoothed, 0.5 + th.smoothing_alpha * 1.0, 1e-12);
}

TEST(signal, baseline_freezes_during_candidate) {
  ClassifierThresholds th;
  SafetyLimits lim;
  BaselineSignalProcessor sp(th, lim);
  sp.process(rd(0.5), 0);
  sp.freezeBaseline(true);
  ProcessedSample s;
  for (int i = 1; i <= 50; ++i) s = sp.process(rd(2.5 + 0.001 * (i % 3)), i * 20);
  CHECK_NEAR(s.baseline, 0.5, 1e-12);
  CHECK(s.baseline_frozen);
  sp.freezeBaseline(false);
  s = sp.process(rd(2.5), 1020);
  CHECK(s.baseline > 0.5);
}

TEST(signal, rate_variance_and_detrend_on_ramp) {
  ClassifierThresholds th;
  SafetyLimits lim;
  BaselineSignalProcessor sp(th, lim);
  ProcessedSample s;
  for (int i = 0; i < 30; ++i) s = sp.process(rd(1.0 + 0.0001 * (i % 2)), i * 20);
  CHECK(std::fabs(s.rate) < 0.05);
  CHECK(s.variance < 1e-6);
  // 10 N/s ramp = 0.2 N per sample; smoothed-rate converges toward 10 N/s
  for (int i = 30; i < 60; ++i) s = sp.process(rd(1.0 + 0.2 * (i - 29)), i * 20);
  CHECK_NEAR(s.rate, 10.0, 0.5);
  CHECK(s.variance > 0.1);
  CHECK(s.detrended > 0.3);  // x is ahead of its trailing mean on a rising ramp
}

TEST(signal, invalid_readings_rejected_nan_oor_stuck) {
  ClassifierThresholds th;
  SafetyLimits lim;
  BaselineSignalProcessor sp(th, lim);
  sp.process(rd(0.4), 0);
  ProcessedSample s = sp.process(rd(std::numeric_limits<double>::quiet_NaN()), 20);
  CHECK(!s.valid);
  CHECK(s.health == SensorHealth::DISCONNECTED);
  CHECK_NEAR(s.x, 0.4, 1e-12);  // last valid value held
  // NaN even when a buggy driver claims valid
  TensionReading bad;
  bad.newtons = std::numeric_limits<double>::quiet_NaN();
  bad.valid = true;
  bad.health = SensorHealth::OK;
  CHECK(!sp.process(bad, 40).valid);
  s = sp.process(rd(49.99), 60);  // HX711-style saturation
  CHECK(!s.valid);
  CHECK(s.health == SensorHealth::OUT_OF_RANGE);
  CHECK(!sp.process(rd(-6.0), 80).valid);
  TensionReading degraded;  // finite value but driver flags it invalid (e.g. uncalibrated HX711)
  degraded.newtons = 0.5;
  degraded.valid = false;
  degraded.health = SensorHealth::DEGRADED;
  CHECK(!sp.process(degraded, 90).valid);
  // stuck: identical readings
  int first_stuck = -1;
  for (int i = 0; i < 30; ++i) {
    s = sp.process(rd(0.4321), 100 + i * 20);
    if (!s.valid && first_stuck < 0) first_stuck = i + 1;
  }
  CHECK_EQ(first_stuck, lim.stuck_samples);
  CHECK(s.health == SensorHealth::STUCK);
}

TEST(classifier, trigger_threshold_boundary) {
  ClassifierThresholds th;
  DeterministicBaselineV1 d(th);
  ProcessedSample s;
  s.valid = true;
  s.health = SensorHealth::OK;
  s.delta = th.trigger_delta_n;
  CHECK(d.shouldTrigger(s));
  s.delta = th.trigger_delta_n - 1e-9;
  CHECK(!d.shouldTrigger(s));
  s.delta = 5.0;
  s.valid = false;
  CHECK(!d.shouldTrigger(s));
}

namespace {
// Feeds a crafted delta sequence (1 sample per 20 ms) and returns the decision.
Classification feed(const ClassifierThresholds& th, const std::vector<double>& deltas, double rate, double detr_amp) {
  DeterministicBaselineV1 d(th);
  ProcessedSample s;
  s.valid = true;
  s.health = SensorHealth::OK;
  s.baseline = 0.4;
  for (size_t i = 0; i < deltas.size(); ++i) {
    s.t_ms = 1000 + static_cast<uint32_t>(i) * 20;
    s.delta = deltas[i];
    s.rate = rate;
    s.detrended = (detr_amp > 0) ? ((i % 2) ? detr_amp : -detr_amp) : 0.0;
    if (i == 0) d.beginCandidate(s);
    else if (d.update(s)) break;
  }
  return d.decide();
}
}  // namespace

TEST(classifier, confirm_min_ms_and_rate_boundaries) {
  ClassifierThresholds th;  // window 600 ms = 30 samples, confirm 300 ms = 15 samples
  std::vector<double> sustained(30, 2.0);
  CHECK(feed(th, sustained, 5.0, 0).result == ClassResult::BITE_CANDIDATE_CONFIRMED);
  // exactly confirm_min_ms above trigger, then drop -> still confirmed (>=)
  std::vector<double> exact(30, 0.2);
  for (int i = 0; i < 15; ++i) exact[i] = 2.0;
  CHECK(feed(th, exact, 5.0, 0).result == ClassResult::BITE_CANDIDATE_CONFIRMED);
  std::vector<double> short_(30, 0.2);
  for (int i = 0; i < 14; ++i) short_[i] = 2.0;
  Classification c = feed(th, short_, 5.0, 0);
  CHECK(c.result == ClassResult::REJECTED_TRANSIENT);
  CHECK(!c.rule_trace.empty());
  CHECK(!c.reason.empty());
  // rate exactly at min passes, just below -> rejected_noise
  CHECK(feed(th, sustained, th.min_rate_n_per_s, 0).result == ClassResult::BITE_CANDIDATE_CONFIRMED);
  CHECK(feed(th, sustained, th.min_rate_n_per_s - 1e-6, 0).result == ClassResult::REJECTED_NOISE);
}

TEST(classifier, oscillation_manual_and_configurable_thresholds) {
  ClassifierThresholds th;
  std::vector<double> sustained(30, 2.0);
  Classification c = feed(th, sustained, 5.0, 0.5);  // alternating detrended -> many crossings
  CHECK(c.result == ClassResult::REJECTED_VIBRATION);
  CHECK(c.features.oscillation_count > th.max_osc);
  std::vector<double> big(30, 2.0);
  big[5] = th.manual_handling_delta_n + 0.01;
  CHECK(feed(th, big, 5.0, 0).result == ClassResult::REJECTED_MANUAL_HANDLING);
  big[5] = th.manual_handling_delta_n;  // not strictly greater -> not manual
  CHECK(feed(th, big, 5.0, 0).result == ClassResult::BITE_CANDIDATE_CONFIRMED);
  // thresholds are configurable: raising min_rate turns the same signal into rejected_noise
  ClassifierThresholds th2 = th;
  th2.min_rate_n_per_s = 50.0;
  CHECK(feed(th2, sustained, 5.0, 0).result == ClassResult::REJECTED_NOISE);
  // max_duration rule reachable when configured below the window
  ClassifierThresholds th3 = th;
  th3.max_duration_ms = 100;
  CHECK(feed(th3, sustained, 5.0, 0).result == ClassResult::REJECTED_MANUAL_HANDLING);
}

TEST(classifier, sensor_fault_during_candidate) {
  ClassifierThresholds th;
  DeterministicBaselineV1 d(th);
  ProcessedSample s;
  s.valid = true;
  s.health = SensorHealth::OK;
  s.delta = 2.0;
  s.t_ms = 0;
  d.beginCandidate(s);
  s.valid = false;
  s.health = SensorHealth::DISCONNECTED;
  s.t_ms = 20;
  CHECK(d.update(s));
  Classification c = d.decide();
  CHECK(c.result == ClassResult::REJECTED_SENSOR_FAULT);
  CHECK(c.features.sensor_health == SensorHealth::DISCONNECTED);
}

namespace {
std::string firstResult(const sim::ScenarioRunResult& r) {
  if (r.lines.empty()) return "<none>";
  Json j;
  parseJson(r.lines.front(), j);
  return th::field(j, {"classification", "result"});
}
}  // namespace

// Fixed seeds: every scenario produces its expected classification.
TEST(classifier, each_scenario_fixed_seed) {
  for (const auto& sc : synthScenarios()) {
    if (sc == "idle") continue;
    auto res = sim::runScenario(sc, 42);
    CHECK_EQ(firstResult(res), expectedClassification(sc));
  }
  auto idle = sim::runScenario("idle", 42);
  // no candidate -> exactly one end-of-window no_event summary record (never one per tick)
  CHECK_EQ(idle.lines.size(), size_t(1));
  CHECK_EQ(firstResult(idle), std::string("no_event"));
  {
    Json j;
    parseJson(idle.lines.front(), j);
    CHECK_EQ(th::field(j, {"outcome", "stop_reason"}), std::string("no_event: nothing to actuate"));
    CHECK_EQ(th::field(j, {"outcome", "final_state"}), std::string("MONITOR"));
  }
  CHECK(idle.final_state == State::MONITOR);
  // a second fixed seed per scenario
  const std::map<std::string, uint32_t> seeds = {{"bite", 7},           {"noise_current", 7},  {"rod_vibration", 7},
                                                 {"manual_handling", 7}, {"sensor_failure", 7}, {"excessive_tension", 7},
                                                 {"motor_stall", 7},     {"comm_loss", 7}};
  for (const auto& kv : seeds) CHECK_EQ(firstResult(sim::runScenario(kv.first, kv.second)), expectedClassification(kv.first));
}

// Seed sweep 1..100: non-bite scenarios must NEVER confirm a bite; counts printed for the report.
TEST(classifier, seed_sweep_no_false_confirmations) {
  const char* nonbite[] = {"idle", "noise_current", "rod_vibration", "manual_handling", "sensor_failure"};
  for (const char* sc : nonbite) {
    int confirmed = 0, expected = 0;
    for (uint32_t seed = 1; seed <= 100; ++seed) {
      auto res = sim::runScenario(sc, seed);
      for (const auto& l : res.lines)
        if (l.find("\"bite_candidate_confirmed\"") != std::string::npos) ++confirmed;
      if (firstResult(res) == expectedClassification(sc)) ++expected;
    }
    std::printf("    sweep %-16s seeds 1-100: false bite confirmations=%d, expected-class=%d/100\n", sc, confirmed,
                expected);
    CHECK_EQ(confirmed, 0);
  }
  int bite_ok = 0;
  for (uint32_t seed = 1; seed <= 100; ++seed)
    if (firstResult(sim::runScenario("bite", seed)) == "bite_candidate_confirmed") ++bite_ok;
  std::printf("    sweep %-16s seeds 1-100: confirmed=%d/100 (misses = weak/short/slow synthetic bites)\n", "bite",
              bite_ok);
  CHECK(bite_ok >= 80);  // regression guard on SYNTHETIC data only; not a performance claim
}
