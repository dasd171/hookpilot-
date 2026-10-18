// HookPilot firmware - hardware-independent interfaces (pure virtual).
//
// Real drivers (src/hw, #ifdef ARDUINO / ESP_PLATFORM) and host mocks (host/) both implement these.
// The control core (state machine, classifier, safety) only ever talks to these interfaces.
#pragma once

#include <cstdint>
#include <string>

#include "hookpilot/config.h"
#include "hookpilot/types.h"

namespace hp {

class Clock {
 public:
  virtual ~Clock() = default;
  virtual uint32_t nowMs() const = 0;  // monotonic milliseconds since boot
};

class Watchdog {
 public:
  virtual ~Watchdog() = default;
  virtual void begin(uint32_t timeout_ms) = 0;
  virtual void feed() = 0;
  // True when the last reset was caused by a watchdog (task/int/RTC WDT).
  virtual bool lastResetWasWatchdog() const = 0;
};

class TensionSensor {
 public:
  virtual ~TensionSensor() = default;
  virtual bool begin() = 0;
  virtual TensionReading read() = 0;
  virtual CalibrationInfo calibration() const = 0;
  virtual bool controlsHardware() const = 0;  // false for mocks / simulation
};

class SignalProcessor {
 public:
  virtual ~SignalProcessor() = default;
  virtual void reset() = 0;
  virtual ProcessedSample process(const TensionReading& r, uint32_t t_ms) = 0;
  virtual void freezeBaseline(bool frozen) = 0;
  virtual bool baselineFrozen() const = 0;
};

class BiteDetector {
 public:
  virtual ~BiteDetector() = default;
  virtual const char* name() const = 0;
  // MONITOR: should this sample open a CANDIDATE?
  virtual bool shouldTrigger(const ProcessedSample& s) const = 0;
  // CANDIDATE: start a new observation window at the trigger sample.
  virtual void beginCandidate(const ProcessedSample& trigger) = 0;
  // Feed one sample; returns true when enough is known to decide.
  virtual bool update(const ProcessedSample& s) = 0;
  // CONFIRM: produce the decision (result + reason + rule trace + features).
  virtual Classification decide() const = 0;
  // After a decision: is the detector re-armed (signal quiet again)?
  virtual bool rearmed(const ProcessedSample& s) = 0;
  virtual const ClassifierThresholds& thresholds() const = 0;
};

class SafetyController {
 public:
  virtual ~SafetyController() = default;
  // Evaluate all interlocks for this tick; returns the latched fault mask.
  virtual uint32_t update(const SafetyInputs& in) = 0;
  // Every actuator command must pass through here. The classifier has no path around it.
  virtual SafetyDecision permit(const ActuatorRequest& req, const SafetyInputs& in) = 0;
  virtual bool arm(const SafetyInputs& in, std::string* why_not) = 0;
  virtual void disarm() = 0;
  virtual bool armed() const = 0;
  virtual uint32_t faults() const = 0;          // latched
  virtual uint32_t activeConditions() const = 0; // conditions present right now
  // Clears latched faults whose condition is gone. Returns true if no fault remains.
  virtual bool clearFaults(const SafetyInputs& in) = 0;
  virtual void latchFault(uint32_t fault) = 0;   // e.g. WATCHDOG_RESET at boot
  virtual const SafetyLimits& limits() const = 0;
};

class HookSettingActuator {
 public:
  virtual ~HookSettingActuator() = default;
  virtual bool begin() = 0;
  virtual void moveTo(double position_mm, double speed_mm_s) = 0;
  virtual void stop() = 0;  // de-energise; no holding torque
  virtual void update(uint32_t now_ms) = 0;
  virtual ActuatorFeedback feedback() const = 0;
  virtual bool controlsHardware() const = 0;
};

class RetrievalController {
 public:
  virtual ~RetrievalController() = default;
  virtual bool begin() = 0;
  virtual void start(double target_m, double speed_m_s) = 0;
  virtual void stop() = 0;  // brake / coast to zero, PWM off
  virtual void update(uint32_t now_ms) = 0;
  virtual ActuatorFeedback feedback() const = 0;
  virtual void resetPosition() = 0;
  virtual bool controlsHardware() const = 0;
};

class DeviceHealthMonitor {
 public:
  virtual ~DeviceHealthMonitor() = default;
  virtual bool begin() = 0;
  virtual double busVoltageV() = 0;
  virtual double temperatureC() = 0;
  virtual bool estopActive() = 0;
};

class CommunicationAdapter {
 public:
  virtual ~CommunicationAdapter() = default;
  virtual bool begin() = 0;
  virtual void poll(uint32_t now_ms) = 0;
  virtual bool sendLine(const std::string& line) = 0;
  virtual bool linkUp() const = 0;
  virtual uint32_t lastHeartbeatMs() const = 0;
};

// Destination for finished JSON lines (Serial, file, test buffer...).
class LineSink {
 public:
  virtual ~LineSink() = default;
  virtual void writeLine(const std::string& line) = 0;
};

struct EventRecord;  // event_logger.h

class EventLogger {
 public:
  virtual ~EventLogger() = default;
  // Seals (hash + chain) and emits the record. Returns the emitted JSON line.
  virtual std::string log(EventRecord& rec) = 0;
  virtual std::string lastHash() const = 0;
};

}  // namespace hp
