#include "hookpilot/calibration.h"

#include <cmath>
#include <cstdio>

#include "hookpilot/sha256.h"

namespace hp {

int32_t hx711SignExtend(uint32_t raw24) {
  raw24 &= 0xFFFFFFu;
  if (raw24 & 0x800000u) return static_cast<int32_t>(raw24 | 0xFF000000u);
  return static_cast<int32_t>(raw24);
}

bool hx711Saturated(int32_t c) { return c >= 0x7FFFFF || c <= -0x800000; }

double countsToNewtons(double counts, const LoadCellCalibration& c) {
  return (counts - c.offset_counts) * c.scale_n_per_count;
}

CalStats sampleStats(const int32_t* s, int n) {
  CalStats st;
  st.n = n;
  if (n <= 0) return st;
  double m = 0;
  for (int i = 0; i < n; ++i) m += s[i];
  m /= n;
  double v = 0;
  for (int i = 0; i < n; ++i) v += (s[i] - m) * (s[i] - m);
  st.mean = m;
  st.stddev = n > 1 ? std::sqrt(v / (n - 1)) : 0.0;
  return st;
}

bool computeTare(const int32_t* s, int n, double max_sd, double* offset, std::string* why) {
  if (n < 10) {
    if (why) *why = "need at least 10 samples";
    return false;
  }
  for (int i = 0; i < n; ++i)
    if (hx711Saturated(s[i])) {
      if (why) *why = "saturated reading during tare";
      return false;
    }
  CalStats st = sampleStats(s, n);
  if (st.stddev > max_sd) {
    if (why) *why = "load not still during tare (stddev too high)";
    return false;
  }
  *offset = st.mean;
  return true;
}

bool computeScale(const int32_t* s, int n, double offset, double mass_kg, double min_span, double max_sd,
                  double* scale, std::string* why) {
  if (n < 10 || !(mass_kg > 0)) {
    if (why) *why = "need >= 10 samples and a positive known mass";
    return false;
  }
  for (int i = 0; i < n; ++i)
    if (hx711Saturated(s[i])) {
      if (why) *why = "saturated reading (mass too heavy for cell/gain)";
      return false;
    }
  CalStats st = sampleStats(s, n);
  if (st.stddev > max_sd) {
    if (why) *why = "load swinging during calibration (stddev too high)";
    return false;
  }
  const double span = st.mean - offset;
  if (std::fabs(span) < min_span) {
    if (why) *why = "span too small: check wiring / mass / gain";
    return false;
  }
  *scale = mass_kg * kStandardGravity / span;
  return true;
}

std::string makeCalibrationVersion(const std::string& device, const std::string& utc, double offset, double scale,
                                   double mass_kg) {
  char params[160];
  std::snprintf(params, sizeof(params), "%.6f|%.12g|%.6f", offset, scale, mass_kg);
  const std::string h = sha256Hex(params).substr(0, 8);
  std::string compact;
  for (char c : utc)
    if (c != '-' && c != ':' && c != '.') compact += c;
  if (compact.empty()) compact = "unsynced";
  return "cal-" + device + "-" + compact + "-" + h;
}

}  // namespace hp
