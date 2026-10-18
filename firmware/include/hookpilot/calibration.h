// Load-cell calibration math (hardware independent, host-tested).
// Procedure (see README): tare with no load -> offset; hang a known mass -> scale; record a
// calibration_version that is stored with the constants and written into every event record.
#pragma once

#include <cstdint>
#include <string>

namespace hp {

constexpr double kStandardGravity = 9.80665;  // m/s^2

struct LoadCellCalibration {
  double offset_counts = 0.0;
  double scale_n_per_count = 0.0;  // 0 => uncalibrated
  std::string calibration_version = "uncalibrated";
  std::string calibrated_utc;      // empty => null (clock not synced)
};

// HX711: 24-bit two's complement, saturates at 0x7FFFFF / 0x800000.
int32_t hx711SignExtend(uint32_t raw24);
bool hx711Saturated(int32_t counts);

double countsToNewtons(double counts, const LoadCellCalibration& c);

struct CalStats {
  double mean = 0, stddev = 0;
  int n = 0;
};
CalStats sampleStats(const int32_t* samples, int n);

// Tare: requires n >= 10 and stddev <= max_stddev_counts (load must be still).
bool computeTare(const int32_t* samples, int n, double max_stddev_counts, double* offset_out, std::string* why);
// Known mass: scale = m*g / (mean - offset). Requires |mean - offset| >= min_span_counts.
bool computeScale(const int32_t* samples, int n, double offset, double known_mass_kg, double min_span_counts,
                  double max_stddev_counts, double* scale_out, std::string* why);
// "cal-<device>-<utc compact or 'unsynced'>-<first 8 hex of sha256(offset|scale|mass)>"
std::string makeCalibrationVersion(const std::string& device_id, const std::string& utc, double offset,
                                   double scale, double known_mass_kg);

}  // namespace hp
