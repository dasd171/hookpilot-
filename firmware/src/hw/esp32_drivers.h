// Real-hardware driver STUBS for the ESP32 target (Arduino framework).
//
// PROVISIONAL: written against the Arduino-ESP32 API but NEVER compiled or run on hardware in this
// repository. Pinout: include/hookpilot/board_pins.h (PROVISIONAL). Everything here is excluded from
// host builds by the guard below.
#pragma once

#if defined(ARDUINO) && (defined(ESP_PLATFORM) || defined(ESP32))

#include <Arduino.h>

#include "hookpilot/board_pins.h"
#include "hookpilot/calibration.h"
#include "hookpilot/interfaces.h"

namespace hp {
namespace hw {

class Esp32Clock : public Clock {
 public:
  uint32_t nowMs() const override { return millis(); }
};

// Task watchdog (esp_task_wdt) + reset-reason check.
class Esp32Watchdog : public Watchdog {
 public:
  void begin(uint32_t timeout_ms) override;
  void feed() override;
  bool lastResetWasWatchdog() const override;
};

// HX711 24-bit load-cell ADC, gain 128 (channel A), bit-banged. Calibration in NVS (Preferences).
class Hx711TensionSensor : public TensionSensor {
 public:
  Hx711TensionSensor(int dout = pins::HX711_DOUT, int sck = pins::HX711_SCK) : dout_(dout), sck_(sck) {}
  bool begin() override;
  TensionReading read() override;  // non-blocking: invalid if no conversion ready within ~1 ms
  CalibrationInfo calibration() const override;
  bool controlsHardware() const override { return true; }

  bool readRaw(int32_t* counts, uint32_t timeout_us);
  bool tare(int n, std::string* why);                         // calibration step 1
  bool calibrateKnownMass(double kg, int n, std::string* why);  // calibration step 2
  bool saveCalibration(const std::string& device_id, const std::string& utc, double kg);
  bool loadCalibration();

 private:
  int dout_, sck_;
  LoadCellCalibration cal_;
  uint32_t last_ok_ms_ = 0;
};

// Hook-set servo on LEDC (50 Hz). Position estimated from the analog feedback pin (if fitted).
class ServoHookActuator : public HookSettingActuator {
 public:
  bool begin() override;
  void moveTo(double position_mm, double speed_mm_s) override;  // slewed in update()
  void stop() override;                                         // PWM off + servo rail off
  void update(uint32_t now_ms) override;
  ActuatorFeedback feedback() const override { return fb_; }
  bool controlsHardware() const override { return true; }

 private:
  void writeAngle(double deg);
  ActuatorFeedback fb_;
  double cmd_mm_ = 0, target_mm_ = 0, speed_ = 0;
  uint32_t last_ms_ = 0;
  bool energised_ = false;
};

// DC motor through an H-bridge, quadrature encoder on interrupts, analog current sense.
class DcMotorRetrieval : public RetrievalController {
 public:
  bool begin() override;
  void start(double target_m, double speed_m_s) override;
  void stop() override;
  void update(uint32_t now_ms) override;
  ActuatorFeedback feedback() const override { return fb_; }
  void resetPosition() override;
  bool controlsHardware() const override { return true; }

 private:
  ActuatorFeedback fb_;
  double target_m_ = 0, speed_m_s_ = 0, duty_ = 0;
  int32_t last_count_ = 0;
  uint32_t last_ms_ = 0;
};

class Esp32HealthMonitor : public DeviceHealthMonitor {
 public:
  bool begin() override;
  double busVoltageV() override;
  double temperatureC() override;
  bool estopActive() override;
};

// Line-oriented serial link. Host must send "HB" lines (heartbeat) at >= 10 Hz.
class SerialCommAdapter : public CommunicationAdapter {
 public:
  bool begin() override;
  void poll(uint32_t now_ms) override;
  bool sendLine(const std::string& line) override;
  bool linkUp() const override { return true; }
  uint32_t lastHeartbeatMs() const override { return last_hb_; }
  bool takeCommand(std::string* cmd);  // "ARM", "DISARM", "RESET", "TARE", "CAL <kg>"

 private:
  std::string rx_, pending_;
  uint32_t last_hb_ = 0;
};

class SerialLineSink : public LineSink {
 public:
  void writeLine(const std::string& line) override;
};

}  // namespace hw
}  // namespace hp

#endif  // ARDUINO && ESP32
