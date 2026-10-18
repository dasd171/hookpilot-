// Shared helpers for host tests (SIMULATION ONLY).
#pragma once

#include <string>
#include <vector>

#include "../host/sim_harness.h"
#include "hookpilot/event_logger.h"
#include "hookpilot/json.h"
#include "hookpilot/prng.h"

namespace th {

// A hand-built signal: base + noise for pre_ms, linear ramp to base+amp over rise_ms, hold.
inline hp::SynthSignal stepSignal(double base, double amp, double pre_ms, double rise_ms, double total_ms,
                                  uint32_t seed = 7, double sigma = 0.02) {
  hp::SynthSignal s;
  s.scenario = "custom";
  hp::Mulberry32 r(seed);
  const int n = static_cast<int>(total_ms / 20.0);
  for (int i = 0; i < n; ++i) {
    const double t = i * 20.0;
    double v = base;
    if (t >= pre_ms) v += (t - pre_ms < rise_ms) ? amp * (t - pre_ms) / rise_ms : amp;
    v += sigma * r.gauss();
    s.t_ms.push_back(t);
    s.tension_n.push_back(v);
  }
  return s;
}

inline hp::SynthSignal fromValues(const std::vector<double>& v) {
  hp::SynthSignal s;
  s.scenario = "custom";
  for (size_t i = 0; i < v.size(); ++i) {
    s.t_ms.push_back(i * 20.0);
    s.tension_n.push_back(v[i]);
  }
  return s;
}

// Boots a rig, loads the signal, ticks once, arms.
struct Rig {
  hp::sim::SimRig r;
  explicit Rig(const hp::sim::RigOptions& o = hp::sim::RigOptions()) : r(o) {}
  bool start(const hp::SynthSignal& s) {
    r.sensor.load(s);
    r.boot();
    r.step();
    return r.arm();
  }
  bool runUntil(hp::State st, int max_steps = 1000) {
    for (int i = 0; i < max_steps; ++i) {
      if (r.ctl->state() == st) return true;
      r.step();
    }
    return r.ctl->state() == st;
  }
  hp::Json lastRecord() const {
    hp::Json j;
    if (!r.sink.lines.empty()) hp::parseJson(r.sink.lines.back(), j);
    return j;
  }
};

inline std::string field(const hp::Json& j, std::initializer_list<const char*> path) {
  const hp::Json* cur = &j;
  for (const char* p : path) {
    cur = cur->find(p);
    if (!cur) return "<missing>";
  }
  if (cur->type() == hp::Json::Type::Str) return cur->str();
  return cur->dump();
}

inline bool hasFault(const hp::Json& rec, const std::string& f) {
  const hp::Json* s = rec.find("safety");
  if (!s) return false;
  const hp::Json* fl = s->find("faults");
  if (!fl) return false;
  for (const auto& x : fl->items())
    if (x.str() == f) return true;
  return false;
}

}  // namespace th
