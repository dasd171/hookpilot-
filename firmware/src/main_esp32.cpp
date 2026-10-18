// ESP32 entry point (Arduino framework) - PROVISIONAL, never compiled or flashed in this repository.
// Host builds ignore this file (guarded). Serial protocol (115200 8N1, newline-terminated):
//   host -> device: HB (heartbeat, >= 10 Hz), ARM, DISARM, RESET, TARE, CAL <kg>
//   device -> host: one JSON event line per record (schema 1.0.0), lines starting with '#' are logs.
#if defined(ARDUINO) && (defined(ESP_PLATFORM) || defined(ESP32))

#include <Arduino.h>

#include "hookpilot/bite_detector.h"
#include "hookpilot/controller.h"
#include "hookpilot/event_logger.h"
#include "hookpilot/safety_controller.h"
#include "hookpilot/signal_processor.h"
#include "hw/esp32_drivers.h"

namespace {
hp::ControllerConfig g_cfg = [] {
  hp::ControllerConfig c;
  c.identity.device_id = "hookpilot-proto-001";
  c.identity.hardware_revision = "proto-A (PROVISIONAL pinout)";
  return c;
}();
hp::hw::Esp32Clock g_clock;
hp::hw::Esp32Watchdog g_wdt;
hp::hw::Hx711TensionSensor g_sensor;
hp::hw::ServoHookActuator g_hook;
hp::hw::DcMotorRetrieval g_motor;
hp::hw::Esp32HealthMonitor g_health;
hp::hw::SerialCommAdapter g_comm;
hp::hw::SerialLineSink g_sink;
hp::BaselineSignalProcessor g_signal(g_cfg.classifier, g_cfg.safety);
hp::DeterministicBaselineV1 g_detector(g_cfg.classifier);
hp::DefaultSafetyController g_safety(g_cfg.safety);
hp::JsonLinesEventLogger g_logger(&g_sink);
hp::HookPilotController* g_ctl = nullptr;
uint32_t g_next_tick = 0;
}  // namespace

void setup() {
  g_comm.begin();
  static hp::HookPilotController ctl(
      g_cfg, hp::ControllerDeps{g_clock, g_wdt, g_sensor, g_signal, g_detector, g_safety, g_hook, g_motor, g_logger,
                                g_health, g_comm});
  hp::SourceInfo src;
  src.mode = "bench";                 // never "simulation" on real hardware
  src.utc_epoch_ms_at_boot = 0;       // no NTP/RTC yet -> 1970-based timestamps
  src.clock_quality = "rtc_unsynced";
  src.simulated_actuators = false;
  ctl.setSource(src);
  ctl.setIdRandom([] { return esp_random(); });
  g_ctl = &ctl;
  ctl.begin();  // IDLE, disarmed, outputs off; watchdog-reset boot is logged
  Serial.printf("# HookPilot firmware %s - PROVISIONAL, thresholds not validated\n", hp::kFirmwareVersion);
  g_next_tick = millis();
}

void loop() {
  const uint32_t now = millis();
  if (static_cast<int32_t>(now - g_next_tick) < 0) return;
  g_next_tick += hp::kDtMs;
  g_ctl->tick();
  std::string cmd, why;
  if (g_comm.takeCommand(&cmd)) {
    bool ok = true;
    if (cmd == "ARM") ok = g_ctl->requestArm(&why);
    else if (cmd == "DISARM") g_ctl->requestDisarm();
    else if (cmd == "RESET") ok = g_ctl->requestReset(&why);
    else if (cmd == "TARE" && g_ctl->state() == hp::State::IDLE) ok = g_sensor.tare(40, &why);
    else if (cmd.rfind("CAL ", 0) == 0 && g_ctl->state() == hp::State::IDLE) {
      const double kg = atof(cmd.c_str() + 4);
      ok = g_sensor.calibrateKnownMass(kg, 40, &why) && g_sensor.saveCalibration(g_cfg.identity.device_id, "", kg);
    } else ok = false, why = "unknown command or not IDLE";
    Serial.printf("# %s -> %s %s\n", cmd.c_str(), ok ? "ok" : "refused:", why.c_str());
  }
}

#endif
