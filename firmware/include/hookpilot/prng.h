// mulberry32 PRNG - bit-identical to the TS (apps/web-demo/src/engine/prng.ts) and
// Python (services/evidence-service/evidence_service/prng.py) implementations.
//
//   a |= 0; a = a + 0x6D2B79F5 | 0;
//   t = Math.imul(a ^ a >>> 15, 1 | a);
//   t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
//   return ((t ^ t >>> 14) >>> 0) / 4294967296;
//
// uniform(lo,hi) consumes one draw; gauss() is Box-Muller cosine branch, no caching,
// consumes exactly two draws, u1 clamped to 1e-12.
#pragma once

#include <cmath>
#include <cstdint>

namespace hp {

constexpr double kPi = 3.141592653589793;  // Math.PI / math.pi

class Mulberry32 {
 public:
  explicit Mulberry32(uint32_t seed) : a_(seed) {}
  uint32_t nextU32() {
    a_ += 0x6D2B79F5u;
    uint32_t t = a_;
    t = (t ^ (t >> 15)) * (t | 1u);
    t = (t + (t ^ (t >> 7)) * (t | 61u)) ^ t;
    return t ^ (t >> 14);
  }
  double next() { return static_cast<double>(nextU32()) / 4294967296.0; }
  double uniform(double lo, double hi) { return lo + (hi - lo) * next(); }
  double gauss() {
    double u1 = next();
    double u2 = next();
    if (u1 < 1e-12) u1 = 1e-12;
    return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * kPi * u2);
  }
  uint32_t state() const { return a_; }

 private:
  uint32_t a_;
};

}  // namespace hp
