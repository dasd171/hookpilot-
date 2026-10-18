// Calibration math and PROVISIONAL pin-map sanity (host only; no hardware involved).
#include <set>
#include <vector>

#include "hookpilot/board_pins.h"
#include "hookpilot/calibration.h"
#include "test_framework.h"

using namespace hp;

TEST(calibration, hx711_decode_and_saturation) {
  CHECK_EQ(hx711SignExtend(0x000001), 1);
  CHECK_EQ(hx711SignExtend(0xFFFFFF), -1);
  CHECK_EQ(hx711SignExtend(0x800000), -8388608);
  CHECK_EQ(hx711SignExtend(0x7FFFFF), 8388607);
  CHECK(hx711Saturated(0x7FFFFF));
  CHECK(hx711Saturated(-0x800000));
  CHECK(!hx711Saturated(123456));
}

TEST(calibration, tare_then_known_mass) {
  std::vector<int32_t> tare, loaded;
  for (int i = 0; i < 40; ++i) {
    tare.push_back(84000 + (i % 5) - 2);
    loaded.push_back(84000 + 213000 + (i % 7) - 3);  // 1.000 kg hung
  }
  double offset = 0, scale = 0;
  std::string why;
  CHECK(computeTare(tare.data(), (int)tare.size(), 50.0, &offset, &why));
  CHECK_NEAR(offset, 84000.0, 1.0);
  CHECK(computeScale(loaded.data(), (int)loaded.size(), offset, 1.0, 1000.0, 50.0, &scale, &why));
  LoadCellCalibration c;
  c.offset_counts = offset;
  c.scale_n_per_count = scale;
  CHECK_NEAR(countsToNewtons(84000 + 213000, c), 9.80665, 1e-3);
  CHECK_NEAR(countsToNewtons(offset, c), 0.0, 1e-12);
  const std::string v = makeCalibrationVersion("hp-001", "2026-10-18T12:00:00.000Z", offset, scale, 1.0);
  CHECK(v.rfind("cal-hp-001-20261018T120000000Z-", 0) == 0);
  CHECK_EQ(v, makeCalibrationVersion("hp-001", "2026-10-18T12:00:00.000Z", offset, scale, 1.0));
  CHECK(v != makeCalibrationVersion("hp-001", "2026-10-18T12:00:00.000Z", offset, scale * 1.01, 1.0));
  CHECK(makeCalibrationVersion("hp-001", "", offset, scale, 1.0).find("-unsynced-") != std::string::npos);
}

TEST(calibration, rejects_bad_conditions) {
  std::vector<int32_t> noisy, sat, flat;
  for (int i = 0; i < 20; ++i) {
    noisy.push_back(84000 + (i % 2 ? 900 : -900));
    sat.push_back(0x7FFFFF);
    flat.push_back(84010);
  }
  double o = 0, s = 0;
  std::string why;
  CHECK(!computeTare(noisy.data(), 20, 50.0, &o, &why));
  CHECK(!computeTare(sat.data(), 20, 50.0, &o, &why));
  CHECK(!computeTare(flat.data(), 5, 50.0, &o, &why));
  CHECK(!computeScale(flat.data(), 20, 84000.0, 1.0, 1000.0, 50.0, &s, &why));  // span too small
  CHECK(!computeScale(flat.data(), 20, 84000.0, 0.0, 1000.0, 50.0, &s, &why));  // no mass
}

TEST(pins, provisional_map_has_no_conflicts) {
  using namespace pins;
  const std::vector<int> all = {HX711_DOUT, HX711_SCK, SERVO_PWM, SERVO_FEEDBACK, SERVO_PWR_EN, MOTOR_IN1, MOTOR_IN2,
                                MOTOR_EN,   ENCODER_A, ENCODER_B, MOTOR_CURRENT, BATTERY_DIVIDER, ESTOP_IN, STATUS_LED};
  CHECK_EQ(std::set<int>(all.begin(), all.end()).size(), all.size());
  for (int p : all) CHECK(!(p >= 6 && p <= 11));  // SPI flash pins
  const std::vector<int> outputs = {HX711_SCK, SERVO_PWM, SERVO_PWR_EN, MOTOR_IN1, MOTOR_IN2, MOTOR_EN, STATUS_LED};
  for (int p : outputs) CHECK(p < 34);             // 34-39 are input-only
  const std::vector<int> adc = {SERVO_FEEDBACK, MOTOR_CURRENT, BATTERY_DIVIDER};
  for (int p : adc) CHECK(p >= 32 && p <= 39);     // ADC1 only (ADC2 unusable with Wi-Fi)
  CHECK(kProvisional);
  CHECK(12.6 * kBatteryDividerRatio < 3.1);        // within ADC 11 dB range
}
