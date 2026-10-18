// Synthetic line-tension generator "hookpilot-synth-v1" (SIMULATION ONLY).
//
// C++ port of services/evidence-service/evidence_service/synth.py. The draw order is the
// specification; following it yields the same samples as the Python/TS ports up to libm
// last-ulp differences in log/cos/sin.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hp {

constexpr const char* kSynthGeneratorId = "hookpilot-synth-v1";
constexpr double kSynthNoiseSigmaN = 0.02;
constexpr double kSynthSensorSaturationN = 49.99;

struct SynthParams {
  double base_n = 0, onset_ms = 0;
  double amp_n = 0, rise_ms = 0, hold_ms = 0, fall_ms = 0;
  double shake_hz = 0, shake_amp_n = 0, shake_phase = 0;
  double ramp_ms = 0, freq_hz = 0, dur_ms = 0;
  double pre_ms = 0, over_rate_n_per_s = 0, cap_n = 0;
  std::string mode;  // sensor_failure: "stuck" | "out_of_range"
  double fault_dur_ms = 0;
  double fault_after_retrieval_start_ms = -1;  // motor_stall / comm_loss, -1 = none
};

struct SynthSignal {
  std::string scenario;
  uint32_t seed = 0;
  double sample_rate_hz = 50.0;
  int duration_ms = 8000;
  std::vector<double> t_ms;
  std::vector<double> tension_n;
  SynthParams params;
};

const std::vector<std::string>& synthScenarios();
bool isKnownScenario(const std::string& s);
std::string groundTruthLabel(const std::string& scenario);
std::string expectedClassification(const std::string& scenario);

// Throws std::invalid_argument for an unknown scenario.
SynthSignal generateSynth(const std::string& scenario, uint32_t seed, double sample_rate_hz = 50.0,
                          int duration_ms = 8000);

}  // namespace hp
