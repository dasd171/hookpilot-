// ESP32 DevKitC (ESP32-WROOM-32) pin assignment - PROVISIONAL.
// Not validated on a real board. Chosen to avoid strapping pins (0, 2*, 5, 12, 15), flash pins
// (6-11) and ADC2 (unusable with Wi-Fi). Analog inputs are on ADC1 only. GPIO 34-39 are input-only.
// (*GPIO 2 drives the on-board LED on most DevKits; used only as a status LED output.)
// Pure constants: compiled on host too, where a unit test checks for conflicts.
#pragma once

#include <cstdint>

namespace hp {
namespace pins {

constexpr bool kProvisional = true;

// HX711 load-cell amplifier (bit-banged)
constexpr int HX711_DOUT = 16;   // input  (data ready / data)
constexpr int HX711_SCK = 17;    // output (clock; held high >60 us = power down)

// Hook-set servo (LEDC PWM 50 Hz) + optional analog position feedback (feedback servo / pot)
constexpr int SERVO_PWM = 18;        // output, LEDC channel 0
constexpr int SERVO_FEEDBACK = 36;   // ADC1_CH0 (VP), input-only
constexpr int SERVO_PWR_EN = 19;     // output, high = servo rail enabled (via load switch / MOSFET)

// DC retrieval motor via H-bridge (e.g. DRV8871 / BTS7960 class), 20 kHz LEDC PWM
constexpr int MOTOR_IN1 = 25;        // output, LEDC channel 2
constexpr int MOTOR_IN2 = 26;        // output, LEDC channel 3
constexpr int MOTOR_EN = 23;         // output, driver enable/sleep; LOW = disabled (default)
constexpr int ENCODER_A = 32;        // input, quadrature A (interrupt)
constexpr int ENCODER_B = 33;        // input, quadrature B
constexpr int MOTOR_CURRENT = 39;    // ADC1_CH3 (VN), input-only; current-sense amp output

// Power / safety
constexpr int BATTERY_DIVIDER = 34;  // ADC1_CH6, input-only; pack voltage via resistor divider
constexpr int ESTOP_IN = 27;         // input w/ pull-up; NC e-stop to GND -> HIGH (open) = STOP
constexpr int STATUS_LED = 2;        // output

// Analog front-end assumptions (PROVISIONAL)
constexpr double kBatteryDividerRatio = 22.0 / (100.0 + 22.0);  // 100k over 22k: 12.6 V -> 2.27 V
constexpr double kCurrentSenseVPerA = 0.185 * (2.0 / 3.0);       // ACS712-05B (185 mV/A) behind 2:3 divider
constexpr double kCurrentSenseZeroV = 2.5 * (2.0 / 3.0);         // ACS712 zero-current output after divider
constexpr double kServoMmPerDeg = 0.2;                           // linkage: 60 deg -> 12 mm (assumed)
constexpr double kServoRestDeg = 10.0;
constexpr double kSpoolCircumferenceM = 0.0942;                  // 30 mm spool diameter (assumed)
constexpr double kEncoderCountsPerRev = 1400.0;                  // 7 PPR x4 x 50:1 gearbox (assumed)

}  // namespace pins
}  // namespace hp
