// Host simulation runner - SIMULATION ONLY, controls no hardware.
//   ./build/hookpilot_sim --scenario bite --seed 42 [--duration 8000] [--quiet]
// Prints one JSON event line per record on stdout; diagnostics go to stderr.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "sim_harness.h"

int main(int argc, char** argv) {
  std::string scenario = "bite";
  long seed = 42;
  int duration = 8000;
  bool quiet = false;
  for (int i = 1; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--scenario") && i + 1 < argc) scenario = argv[++i];
    else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) seed = std::strtol(argv[++i], nullptr, 10);
    else if (!std::strcmp(argv[i], "--duration") && i + 1 < argc) duration = std::atoi(argv[++i]);
    else if (!std::strcmp(argv[i], "--quiet")) quiet = true;
    else if (!std::strcmp(argv[i], "--list")) {
      for (const auto& s : hp::synthScenarios()) std::printf("%s\n", s.c_str());
      return 0;
    } else {
      std::fprintf(stderr, "usage: %s --scenario <name> --seed <int> [--duration ms] [--quiet] | --list\n", argv[0]);
      return 2;
    }
  }
  if (!hp::isKnownScenario(scenario)) {
    std::fprintf(stderr, "unknown scenario '%s' (use --list)\n", scenario.c_str());
    return 2;
  }
  if (quiet) hp::mock::setBannerEnabled(false);
  std::fprintf(stderr, "[SIMULATION] HookPilot firmware host sim: scenario=%s seed=%ld duration=%d ms "
                       "(mocks only, no hardware, thresholds provisional)\n",
               scenario.c_str(), seed, duration);
  hp::mock::StdoutSink out;
  hp::sim::RigOptions opt;
  opt.extra_sink = &out;
  auto res = hp::sim::runScenario(scenario, static_cast<uint32_t>(seed), duration, opt);
  if (!quiet) {
    for (const auto& t : res.transitions)
      std::fprintf(stderr, "  t=%6u ms  %-9s -> %-9s  %s\n", t.t_ms, hp::toString(t.from), hp::toString(t.to),
                   t.reason.c_str());
    std::fprintf(stderr, "[SIMULATION] records=%zu final_state=%s latched_faults=%u\n", res.lines.size(),
                 hp::toString(res.final_state), res.faults);
  }
  return 0;
}
