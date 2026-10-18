// HOST MOCKS - SIMULATION ONLY. None of these classes controls any hardware.
// Every mock reports controlsHardware() == false and prints a one-time banner to stderr.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <vector>

#include "hookpilot/interfaces.h"
#include "hookpilot/scenarios.h"

namespace hp {
namespace mock {

void banner(const char* cls);  // prints once per class: "[MOCK] <cls>: SIMULATION ONLY - controls no hardware"
void setBannerEnabled(bool on);

class MockClock : public Clock {
 public:
  uint32_t nowMs() const override { return now_; }
  void set(uint32_t t) { now_ = t; }
  void advance(uint32_t dt) { now_ += dt; }

 private:
  uint32_t now_ = 0;
};

class MockWatchdog : public Watchdog {
 public:
  explicit MockWatchdog(bool last_reset_was_wdt = false) : wdt_reset_(last_reset_was_wdt) { banner("MockWatchdog"); }
  void begin(uint32_t timeout_ms) override { timeout_ = timeout_ms; began_ = true; }
  void feed() override { ++feeds_; }
  bool lastResetWasWatchdog() const override { return wdt_reset_; }
  void setLastResetWasWatchdog(bool v) { wdt_reset_ = v; }
  uint32_t feeds() const { return feeds_; }
  uint32_t timeout() const { return timeout_; }
  bool began() const { return began_; }

 private:
  bool wdt_reset_;
  uint32_t timeout_ = 0, feeds_ = 0;
  bool began_ = false;
};

// Replays a synthetic signal (hookpilot-synth-v1) sample by sample, with injectable faults.
class MockTensionSensor : public TensionSensor {
 public:
  MockTensionSensor() { banner("MockTensionSensor"); }
  explicit MockTensionSensor(const SynthSignal& sig) : sig_(sig) { banner("MockTensionSensor"); }
  bool begin() override { return true; }
  TensionReading read() override;
  CalibrationInfo calibration() const override;
  bool controlsHardware() const override { return false; }

  void load(const SynthSignal& sig) { sig_ = sig; idx_ = 0; }
  void setConstant(double n) { constant_ = true; const_value_ = n; }  // replaces replay; tiny dither added
  void setAdditiveLoad(double n) { additive_ = n; }
  // fault injection
  void injectDisconnect(bool on) { disconnect_ = on; }
  void injectNaN(bool on) { nan_ = on; }
  void injectOutOfRange(bool on, double value = 60.0) { oor_ = on; oor_value_ = value; }
  void injectStuck(bool on) { stuck_ = on; have_stuck_ = false; }
  void injectOverride(bool on, double value) { override_ = on; override_value_ = value; }
  size_t index() const { return idx_; }
  bool exhausted() const { return !constant_ && idx_ >= sig_.tension_n.size(); }

 private:
  SynthSignal sig_;
  size_t idx_ = 0;
  bool constant_ = false;
  double const_value_ = 0;
  uint32_t dither_ = 0;
  double additive_ = 0;
  bool disconnect_ = false, nan_ = false, oor_ = false, stuck_ = false, override_ = false;
  double oor_value_ = 60.0, override_value_ = 0;
  bool have_stuck_ = false;
  double stuck_value_ = 0;
};

// Servo hook-set actuator model: position (mm), slew at commanded speed, current feedback.
class MockHookActuator : public HookSettingActuator {
 public:
  MockHookActuator() { banner("MockHookActuator"); }
  bool begin() override { return true; }
  void moveTo(double position_mm, double speed_mm_s) override;
  void stop() override;
  void update(uint32_t now_ms) override;
  ActuatorFeedback feedback() const override { return fb_; }
  bool controlsHardware() const override { return false; }

  double tolerance_mm = 0.5;
  // fault injection
  void injectJam(bool on) { jam_ = on; }
  void injectOvershoot(double mm) { overshoot_ = mm; }
  void injectFeedbackLoss(bool on) { fb_loss_ = on; }
  uint32_t moveCommands() const { return moves_; }

 private:
  ActuatorFeedback fb_;
  double target_ = 0, speed_ = 0;
  uint32_t last_ms_ = 0;
  bool have_last_ = false;
  bool jam_ = false, fb_loss_ = false;
  double overshoot_ = 0;
  uint32_t moves_ = 0;
};

// DC motor + encoder + current-sense retrieval model: position (m of line), current (A).
class MockRetrieval : public RetrievalController {
 public:
  MockRetrieval() { banner("MockRetrieval"); }
  bool begin() override { return true; }
  void start(double target_m, double speed_m_s) override;
  void stop() override;
  void update(uint32_t now_ms) override;
  ActuatorFeedback feedback() const override { return fb_; }
  void resetPosition() override { fb_.position = 0; }
  bool controlsHardware() const override { return false; }

  void setLoad(double tension_n) { load_n_ = tension_n; }
  // fault injection
  void injectStall(bool on) { stall_ = on; }
  void injectEncoderLoss(bool on) { enc_loss_ = on; }
  void injectRunaway(bool on) { runaway_ = on; }  // ignores target (tests travel limit)
  uint32_t startCommands() const { return starts_; }

 private:
  ActuatorFeedback fb_;
  double target_ = 0, speed_ = 0, load_n_ = 0;
  uint32_t last_ms_ = 0;
  bool have_last_ = false;
  bool stall_ = false, enc_loss_ = false, runaway_ = false;
  uint32_t starts_ = 0;
};

class MockHealthMonitor : public DeviceHealthMonitor {
 public:
  MockHealthMonitor() { banner("MockHealthMonitor"); }
  bool begin() override { return true; }
  double busVoltageV() override { return voltage_; }
  double temperatureC() override { return temp_; }
  bool estopActive() override { return estop_; }
  void setVoltage(double v) { voltage_ = v; }
  void setTemperature(double c) { temp_ = c; }
  void setEstop(bool on) { estop_ = on; }

 private:
  double voltage_ = 11.4, temp_ = 25.0;
  bool estop_ = false;
};

// Heartbeat arrives on every poll while the link is up.
class MockComm : public CommunicationAdapter {
 public:
  MockComm() { banner("MockComm"); }
  bool begin() override { return true; }
  void poll(uint32_t now_ms) override {
    if (up_) last_hb_ = now_ms;
  }
  bool sendLine(const std::string& line) override {
    if (!up_) return false;
    sent_.push_back(line);
    return true;
  }
  bool linkUp() const override { return up_; }
  uint32_t lastHeartbeatMs() const override { return last_hb_; }
  void setLinkUp(bool up) { up_ = up; }
  const std::vector<std::string>& sent() const { return sent_; }

 private:
  bool up_ = true;
  uint32_t last_hb_ = 0;
  std::vector<std::string> sent_;
};

class VectorSink : public LineSink {
 public:
  void writeLine(const std::string& line) override { lines.push_back(line); }
  std::vector<std::string> lines;
};

class StdoutSink : public LineSink {
 public:
  void writeLine(const std::string& line) override {
    std::fputs(line.c_str(), stdout);
    std::fputc('\n', stdout);
  }
};

}  // namespace mock
}  // namespace hp
