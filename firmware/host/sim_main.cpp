// Host simulation runner - SIMULATION ONLY, controls no hardware.
//   ./build/hookpilot_sim --scenario bite --seed 42 [--duration 8000] [--quiet] [--disarmed]
//                         [--inject <kind>@<t_ms|retrieval+ms>[:value]]... [--set <param>=<value>]...
//                         [--trace <file>|-]
// Prints one JSON event line per record on stdout; diagnostics go to stderr. --trace writes one JSON
// line per tick (docs/engine-spec.md "Tick trace") to <file> ("-" = stderr).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "sim_harness.h"

namespace {

class FileSink : public hp::LineSink {
 public:
  explicit FileSink(std::FILE* f) : f_(f) {}
  void writeLine(const std::string& l) override {
    std::fputs(l.c_str(), f_);
    std::fputc('\n', f_);
  }

 private:
  std::FILE* f_;
};

// --set <param>=<value>: every tunable shared with the Python / TS ports (same names everywhere).
bool setParam(hp::sim::RigOptions& o, const std::string& kv) {
  const size_t eq = kv.find('=');
  if (eq == std::string::npos) return false;
  const std::string k = kv.substr(0, eq);
  const double v = std::strtod(kv.c_str() + eq + 1, nullptr);
  auto& c = o.cfg.classifier;
  auto& s = o.cfg.safety;
  auto& a = o.cfg.actuators;
  auto u = [&](uint32_t& f) { f = static_cast<uint32_t>(v); return true; };
  auto i = [&](int& f) { f = static_cast<int>(v); return true; };
  auto d = [&](double& f) { f = v; return true; };
  if (k == "trigger_delta_n") return d(c.trigger_delta_n);
  if (k == "confirm_min_ms") return u(c.confirm_min_ms);
  if (k == "min_rate_n_per_s") return d(c.min_rate_n_per_s);
  if (k == "max_osc") return i(c.max_osc);
  if (k == "manual_handling_delta_n") return d(c.manual_handling_delta_n);
  if (k == "max_duration_ms") return u(c.max_duration_ms);
  if (k == "decision_window_ms") return u(c.decision_window_ms);
  if (k == "release_ratio") return d(c.release_ratio);
  if (k == "rearm_quiet_ms") return u(c.rearm_quiet_ms);
  if (k == "retrieval_force_limit_n") return d(s.retrieval_force_limit_n);
  if (k == "hard_max_tension_n") return d(s.hard_max_tension_n);
  if (k == "over_tension_samples") return i(s.over_tension_samples);
  if (k == "sensor_fault_samples") return i(s.sensor_fault_samples);
  if (k == "hook_set_timeout_ms") return u(s.hook_set_timeout_ms);
  if (k == "retrieval_timeout_ms") return u(s.retrieval_timeout_ms);
  if (k == "hook_set_travel_limit_mm") return d(s.hook_set_travel_limit_mm);
  if (k == "retrieval_travel_limit_m") return d(s.retrieval_travel_limit_m);
  if (k == "stall_current_a") return d(s.stall_current_a);
  if (k == "stall_window_ms") return u(s.stall_window_ms);
  if (k == "stall_min_progress_m") return d(s.stall_min_progress_m);
  if (k == "stall_grace_ms") return u(s.stall_grace_ms);
  if (k == "comm_timeout_ms") return u(s.comm_timeout_ms);
  if (k == "hook_to_retrieval_gap_ms") return u(s.hook_to_retrieval_gap_ms);
  if (k == "undervoltage_v") return d(s.undervoltage_v);
  if (k == "undervoltage_debounce_ms") return u(s.undervoltage_debounce_ms);
  if (k == "hook_set_target_mm") return d(a.hook_set_target_mm);
  if (k == "hook_set_speed_mm_s") return d(a.hook_set_speed_mm_s);
  if (k == "retrieval_target_m") return d(a.retrieval_target_m);
  if (k == "retrieval_speed_m_s") return d(a.retrieval_speed_m_s);
  if (k == "verify_ms") return u(a.verify_ms);
  if (k == "hook_set_line_load_n") return d(o.coupling.hook_set_line_load_n);
  if (k == "retrieval_line_load_n") return d(o.coupling.retrieval_line_load_n);
  return false;
}

}  // namespace

int main(int argc, char** argv) {
  std::string scenario = "bite";
  long seed = 42;
  int duration = 8000;
  bool quiet = false;
  std::string trace_path;
  hp::sim::RigOptions opt;
  for (int i = 1; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--scenario") && i + 1 < argc) scenario = argv[++i];
    else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) seed = std::strtol(argv[++i], nullptr, 10);
    else if (!std::strcmp(argv[i], "--duration") && i + 1 < argc) duration = std::atoi(argv[++i]);
    else if (!std::strcmp(argv[i], "--quiet")) quiet = true;
    else if (!std::strcmp(argv[i], "--disarmed")) opt.arm_at_start = false;
    else if (!std::strcmp(argv[i], "--trace") && i + 1 < argc) trace_path = argv[++i];
    else if (!std::strcmp(argv[i], "--inject") && i + 1 < argc) {
      hp::sim::Injection inj;
      std::string err;
      if (!hp::sim::parseInjection(argv[++i], &inj, &err)) {
        std::fprintf(stderr, "%s\n", err.c_str());
        return 2;
      }
      opt.injections.push_back(inj);
    } else if (!std::strcmp(argv[i], "--set") && i + 1 < argc) {
      if (!setParam(opt, argv[++i])) {
        std::fprintf(stderr, "unknown or malformed --set '%s'\n", argv[i]);
        return 2;
      }
    } else if (!std::strcmp(argv[i], "--list")) {
      for (const auto& s : hp::synthScenarios()) std::printf("%s\n", s.c_str());
      return 0;
    } else {
      std::fprintf(stderr,
                   "usage: %s --scenario <name> --seed <int> [--duration ms] [--quiet] [--disarmed]\n"
                   "          [--inject kind@t|retrieval+ms[:value]]... [--set param=value]... [--trace file|-] | --list\n",
                   argv[0]);
      return 2;
    }
  }
  if (!hp::isKnownScenario(scenario)) {
    std::fprintf(stderr, "unknown scenario '%s' (use --list)\n", scenario.c_str());
    return 2;
  }
  if (quiet) hp::mock::setBannerEnabled(false);
  std::fprintf(stderr, "[SIMULATION] HookPilot firmware host sim: scenario=%s seed=%ld duration=%d ms armed=%s "
                       "(mocks only, no hardware, thresholds provisional)\n",
               scenario.c_str(), seed, duration, opt.arm_at_start ? "yes" : "no");
  hp::mock::StdoutSink out;
  opt.extra_sink = &out;
  std::FILE* tf = nullptr;
  if (!trace_path.empty()) {
    tf = trace_path == "-" ? stderr : std::fopen(trace_path.c_str(), "w");
    if (!tf) {
      std::fprintf(stderr, "cannot open trace file '%s'\n", trace_path.c_str());
      return 2;
    }
  }
  FileSink trace(tf);
  if (tf) opt.trace_sink = &trace;
  auto res = hp::sim::runScenario(scenario, static_cast<uint32_t>(seed), duration, opt);
  if (tf && tf != stderr) std::fclose(tf);
  if (!quiet) {
    for (const auto& t : res.transitions)
      std::fprintf(stderr, "  t=%6u ms  %-9s -> %-9s  %s\n", t.t_ms, hp::toString(t.from), hp::toString(t.to),
                   t.reason.c_str());
    std::fprintf(stderr, "[SIMULATION] records=%zu final_state=%s latched_faults=%u\n", res.lines.size(),
                 hp::toString(res.final_state), res.faults);
  }
  return 0;
}
