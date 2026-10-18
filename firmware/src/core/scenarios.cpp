// SIMULATION ONLY - port of services/evidence-service/evidence_service/synth.py (hookpilot-synth-v1).
#include "hookpilot/scenarios.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "hookpilot/prng.h"

namespace hp {

const std::vector<std::string>& synthScenarios() {
  static const std::vector<std::string> k = {"idle",           "bite",          "noise_current",
                                             "rod_vibration",  "manual_handling", "sensor_failure",
                                             "excessive_tension", "motor_stall", "comm_loss"};
  return k;
}

bool isKnownScenario(const std::string& s) {
  const auto& k = synthScenarios();
  return std::find(k.begin(), k.end(), s) != k.end();
}

std::string groundTruthLabel(const std::string& s) {
  if (s == "idle") return "no_bite";
  if (s == "bite") return "bite";
  if (s == "noise_current") return "no_bite_current_drift";
  if (s == "rod_vibration") return "no_bite_vibration";
  if (s == "manual_handling") return "no_bite_manual_handling";
  if (s == "sensor_failure") return "sensor_fault";
  if (s == "excessive_tension") return "bite_then_over_tension";
  if (s == "motor_stall") return "bite_then_motor_stall";
  if (s == "comm_loss") return "bite_then_comm_loss";
  return "";
}

std::string expectedClassification(const std::string& s) {
  if (s == "idle") return "no_event";
  if (s == "noise_current") return "rejected_noise";
  if (s == "rod_vibration") return "rejected_vibration";
  if (s == "manual_handling") return "rejected_manual_handling";
  if (s == "sensor_failure") return "rejected_sensor_fault";
  return "bite_candidate_confirmed";  // bite, excessive_tension, motor_stall, comm_loss
}

static double snap(double ms, double dt) { return dt * std::floor(ms / dt); }

static double biteShape(double tau, const SynthParams& p) {
  const double A = p.amp_n, rise = p.rise_ms, hold = p.hold_ms, fall = p.fall_ms;
  if (tau < 0) return 0.0;
  if (tau < rise) return A * tau / rise;
  if (tau < rise + hold)
    return A + p.shake_amp_n * std::sin(2.0 * kPi * p.shake_hz * (tau - rise) / 1000.0 + p.shake_phase);
  if (tau < rise + hold + fall) return A * (1.0 - (tau - rise - hold) / fall);
  return 0.0;
}

SynthSignal generateSynth(const std::string& scenario, uint32_t seed, double sample_rate_hz, int duration_ms) {
  if (!isKnownScenario(scenario)) throw std::invalid_argument("unknown scenario: " + scenario);
  Mulberry32 rng(seed);
  const double dt = 1000.0 / sample_rate_hz;
  const int n = static_cast<int>(std::lround(duration_ms / dt));
  SynthSignal sig;
  sig.scenario = scenario;
  sig.seed = seed;
  sig.sample_rate_hz = sample_rate_hz;
  sig.duration_ms = duration_ms;
  sig.t_ms.resize(n);
  for (int i = 0; i < n; ++i) sig.t_ms[i] = i * dt;

  SynthParams& p = sig.params;
  // 1) common draws
  p.base_n = rng.uniform(0.35, 0.50);
  p.onset_ms = snap(rng.uniform(1500.0, 3000.0), dt);

  // 2) scenario draws (order is normative)
  if (scenario == "bite") {
    p.amp_n = rng.uniform(0.7, 2.6);
    p.rise_ms = rng.uniform(60, 200);
    p.hold_ms = rng.uniform(150, 1500);
    p.shake_hz = rng.uniform(1.5, 3.0);
    p.shake_amp_n = rng.uniform(0.03, 0.10);
    p.shake_phase = rng.uniform(0, 2 * kPi);
    p.fall_ms = rng.uniform(150, 400);
  } else if (scenario == "noise_current") {
    p.amp_n = rng.uniform(0.9, 1.7);
    p.ramp_ms = rng.uniform(2500, 4000);
  } else if (scenario == "rod_vibration") {
    p.freq_hz = rng.uniform(6, 12);
    p.amp_n = rng.uniform(0.9, 1.5);
    p.dur_ms = rng.uniform(1000, 2000);
  } else if (scenario == "manual_handling") {
    p.amp_n = rng.uniform(6.5, 12.0);
    p.rise_ms = rng.uniform(300, 800);
    p.hold_ms = rng.uniform(1000, 3000);
    p.fall_ms = 300.0;
  } else if (scenario == "sensor_failure") {
    p.mode = rng.next() < 0.5 ? "stuck" : "out_of_range";
    p.fault_dur_ms = rng.uniform(200, 1000);
  } else if (scenario == "excessive_tension") {
    p.amp_n = rng.uniform(1.2, 2.2);
    p.rise_ms = rng.uniform(60, 200);
    p.pre_ms = rng.uniform(600, 1000);
    p.over_rate_n_per_s = rng.uniform(8, 16);
    p.cap_n = 15.0;
  } else if (scenario == "motor_stall" || scenario == "comm_loss") {
    p.amp_n = rng.uniform(1.2, 2.2);
    p.rise_ms = rng.uniform(60, 200);
    p.hold_ms = rng.uniform(2000, 3000);
    p.shake_hz = rng.uniform(1.5, 3.0);
    p.shake_amp_n = rng.uniform(0.03, 0.10);
    p.shake_phase = rng.uniform(0, 2 * kPi);
    p.fall_ms = 300.0;
    p.fault_after_retrieval_start_ms = rng.uniform(200, 800);
  }

  // 3) noise: one gauss() (two draws) per sample, always, for every sample
  std::vector<double> noise(n);
  for (int i = 0; i < n; ++i) noise[i] = kSynthNoiseSigmaN * rng.gauss();

  // 4) compose
  sig.tension_n.resize(n);
  const bool biteLike = scenario == "bite" || scenario == "motor_stall" || scenario == "comm_loss";
  for (int i = 0; i < n; ++i) {
    const double tau = sig.t_ms[i] - p.onset_ms;
    double s = 0.0;
    if (biteLike) {
      s = biteShape(tau, p);
    } else if (scenario == "noise_current") {
      s = tau < 0 ? 0.0 : (tau < p.ramp_ms ? p.amp_n * tau / p.ramp_ms : p.amp_n);
    } else if (scenario == "rod_vibration") {
      if (tau >= 0 && tau < p.dur_ms) {
        const double env = std::min({1.0, tau / 100.0, (p.dur_ms - tau) / 100.0});
        s = p.amp_n * env * std::sin(2 * kPi * p.freq_hz * tau / 1000.0);
      }
    } else if (scenario == "manual_handling") {
      const double A = p.amp_n, r = p.rise_ms, h = p.hold_ms, f = p.fall_ms;
      if (tau >= 0 && tau < r) s = A * tau / r;
      else if (tau >= r && tau < r + h) s = A;
      else if (tau >= r + h && tau < r + h + f) s = A * (1 - (tau - r - h) / f);
    } else if (scenario == "excessive_tension") {
      const double A = p.amp_n, r = p.rise_ms, pre = p.pre_ms;
      if (tau >= 0 && tau < r) s = A * tau / r;
      else if (tau >= r && tau < r + pre) s = A;
      else if (tau >= r + pre) s = std::min(p.cap_n, A + p.over_rate_n_per_s * (tau - r - pre) / 1000.0);
    }
    sig.tension_n[i] = p.base_n + s + noise[i];
  }

  // 5) sensor fault post-processing
  if (scenario == "sensor_failure") {
    const int i0 = static_cast<int>(std::lround(p.onset_ms / dt));
    if (p.mode == "stuck") {
      const double frozen = sig.tension_n[i0];
      for (int i = i0; i < n; ++i) sig.tension_n[i] = frozen;
    } else {
      const int i1 = std::min(n, i0 + static_cast<int>(std::lround(p.fault_dur_ms / dt)));
      for (int i = i0; i < i1; ++i) sig.tension_n[i] = kSynthSensorSaturationN;
    }
  }
  return sig;
}

}  // namespace hp
