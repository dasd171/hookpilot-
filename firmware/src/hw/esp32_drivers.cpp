// Real-hardware driver STUBS (ESP32 / Arduino). PROVISIONAL - never compiled or bench-tested here.
#include "esp32_drivers.h"

#if defined(ARDUINO) && (defined(ESP_PLATFORM) || defined(ESP32))

#include <Preferences.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include <algorithm>
#include <cmath>
#include <vector>

namespace hp {
namespace hw {

namespace {
constexpr int kServoCh = 0, kMotorCh1 = 2, kMotorCh2 = 3;
constexpr int kServoFreq = 50, kServoRes = 16;     // 20 ms frame, 16-bit duty
constexpr int kMotorFreq = 20000, kMotorRes = 10;  // 20 kHz, 10-bit duty
volatile int32_t g_enc_count = 0;

void IRAM_ATTR encoderIsr() {
  // x2 decoding on channel A edges; direction from channel B
  const int a = digitalRead(pins::ENCODER_A), b = digitalRead(pins::ENCODER_B);
  g_enc_count += (a == b) ? -1 : 1;
}

void pwmSetup(int pin, int ch, int freq, int res) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)ch;
  ledcAttach(pin, freq, res);  // core 3.x API (pin-based)
#else
  ledcSetup(ch, freq, res);    // core 2.x API (channel-based)
  ledcAttachPin(pin, ch);
#endif
}

void pwmWrite(int pin, int ch, uint32_t duty) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)ch;
  ledcWrite(pin, duty);
#else
  (void)pin;
  ledcWrite(ch, duty);
#endif
}
}  // namespace

// ---------------- Watchdog ----------------
void Esp32Watchdog::begin(uint32_t timeout_ms) {
#if defined(ESP_IDF_VERSION_MAJOR) && ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t cfg = {timeout_ms, 0, true};
  esp_task_wdt_reconfigure(&cfg);
#else
  esp_task_wdt_init((timeout_ms + 999) / 1000, true);
#endif
  esp_task_wdt_add(nullptr);  // current (loop) task
}
void Esp32Watchdog::feed() { esp_task_wdt_reset(); }
bool Esp32Watchdog::lastResetWasWatchdog() const {
  const esp_reset_reason_t r = esp_reset_reason();
  return r == ESP_RST_TASK_WDT || r == ESP_RST_INT_WDT || r == ESP_RST_WDT;
}

// ---------------- HX711 ----------------
bool Hx711TensionSensor::begin() {
  pinMode(dout_, INPUT);
  pinMode(sck_, OUTPUT);
  digitalWrite(sck_, LOW);
  loadCalibration();
  return true;
}

bool Hx711TensionSensor::readRaw(int32_t* counts, uint32_t timeout_us) {
  const uint32_t t0 = micros();
  while (digitalRead(dout_) == HIGH) {  // HIGH = conversion not ready
    if (micros() - t0 > timeout_us) return false;
  }
  uint32_t v = 0;
  noInterrupts();  // SCK high > 60 us would power the chip down
  for (int i = 0; i < 24; ++i) {
    digitalWrite(sck_, HIGH);
    delayMicroseconds(1);
    v = (v << 1) | (digitalRead(dout_) ? 1u : 0u);
    digitalWrite(sck_, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(sck_, HIGH);  // 25th pulse: channel A, gain 128
  delayMicroseconds(1);
  digitalWrite(sck_, LOW);
  interrupts();
  *counts = hx711SignExtend(v);
  return true;
}

TensionReading Hx711TensionSensor::read() {
  TensionReading r;
  int32_t c = 0;
  // HX711 at RATE=HIGH is 80 SPS; control loop is 50 Hz, so a sample is normally ready.
  if (!readRaw(&c, 1000)) {
    r.valid = false;
    r.newtons = NAN;
    r.health = (millis() - last_ok_ms_ > 200) ? SensorHealth::DISCONNECTED : SensorHealth::DEGRADED;
    return r;
  }
  r.raw_counts = c;
  if (hx711Saturated(c)) {
    r.valid = false;
    r.newtons = NAN;
    r.health = SensorHealth::OUT_OF_RANGE;
    return r;
  }
  if (cal_.scale_n_per_count == 0.0) {  // uncalibrated: never report newtons
    r.valid = false;
    r.newtons = NAN;
    r.health = SensorHealth::DEGRADED;
    return r;
  }
  last_ok_ms_ = millis();
  r.newtons = countsToNewtons(c, cal_);
  r.valid = true;
  r.health = SensorHealth::OK;
  return r;
}

CalibrationInfo Hx711TensionSensor::calibration() const {
  CalibrationInfo i;
  i.calibration_version = cal_.calibration_version;
  i.scale_n_per_count = cal_.scale_n_per_count;
  i.offset_counts = cal_.offset_counts;
  i.calibrated_utc = cal_.calibrated_utc;
  return i;
}

bool Hx711TensionSensor::tare(int n, std::string* why) {
  std::vector<int32_t> s;
  for (int i = 0; i < n; ++i) {
    int32_t c;
    if (readRaw(&c, 200000)) s.push_back(c);
  }
  double off = 0;
  if (!computeTare(s.data(), static_cast<int>(s.size()), 200.0, &off, why)) return false;  // 200 counts: PROVISIONAL
  cal_.offset_counts = off;
  return true;
}

bool Hx711TensionSensor::calibrateKnownMass(double kg, int n, std::string* why) {
  std::vector<int32_t> s;
  for (int i = 0; i < n; ++i) {
    int32_t c;
    if (readRaw(&c, 200000)) s.push_back(c);
  }
  double scale = 0;
  if (!computeScale(s.data(), static_cast<int>(s.size()), cal_.offset_counts, kg, 2000.0, 400.0, &scale, why))
    return false;
  cal_.scale_n_per_count = scale;
  return true;
}

bool Hx711TensionSensor::saveCalibration(const std::string& device_id, const std::string& utc, double kg) {
  cal_.calibration_version = makeCalibrationVersion(device_id, utc, cal_.offset_counts, cal_.scale_n_per_count, kg);
  cal_.calibrated_utc = utc;
  Preferences p;
  if (!p.begin("hp_cal", false)) return false;
  p.putDouble("offset", cal_.offset_counts);
  p.putDouble("scale", cal_.scale_n_per_count);
  p.putString("version", cal_.calibration_version.c_str());
  p.putString("utc", cal_.calibrated_utc.c_str());
  p.end();
  return true;
}

bool Hx711TensionSensor::loadCalibration() {
  Preferences p;
  if (!p.begin("hp_cal", true)) return false;
  cal_.offset_counts = p.getDouble("offset", 0.0);
  cal_.scale_n_per_count = p.getDouble("scale", 0.0);
  cal_.calibration_version = p.getString("version", "uncalibrated").c_str();
  cal_.calibrated_utc = p.getString("utc", "").c_str();
  p.end();
  return cal_.scale_n_per_count != 0.0;
}

// ---------------- Servo hook-set ----------------
bool ServoHookActuator::begin() {
  pinMode(pins::SERVO_PWR_EN, OUTPUT);
  digitalWrite(pins::SERVO_PWR_EN, LOW);  // rail off until commanded
  pwmSetup(pins::SERVO_PWM, kServoCh, kServoFreq, kServoRes);
  pwmWrite(pins::SERVO_PWM, kServoCh, 0);
  analogSetPinAttenuation(pins::SERVO_FEEDBACK, ADC_11db);
  fb_.status = "idle";
  return true;
}

void ServoHookActuator::writeAngle(double deg) {
  // 500..2500 us over 0..180 deg (PROVISIONAL; check the servo's datasheet)
  const double us = 500.0 + (deg / 180.0) * 2000.0;
  const uint32_t duty = static_cast<uint32_t>(us / 20000.0 * ((1 << kServoRes) - 1));
  pwmWrite(pins::SERVO_PWM, kServoCh, duty);
}

void ServoHookActuator::moveTo(double mm, double speed) {
  target_mm_ = mm;
  speed_ = speed;
  digitalWrite(pins::SERVO_PWR_EN, HIGH);
  energised_ = true;
  fb_.active = true;
  fb_.at_target = false;
  fb_.status = "moving";
  last_ms_ = millis();
}

void ServoHookActuator::stop() {
  pwmWrite(pins::SERVO_PWM, kServoCh, 0);
  digitalWrite(pins::SERVO_PWR_EN, LOW);
  energised_ = false;
  fb_.active = false;
  fb_.status = "stopped";
}

void ServoHookActuator::update(uint32_t now) {
  // feedback pot: 0..3.1 V over 0..180 deg (PROVISIONAL)
  const double mv = analogReadMilliVolts(pins::SERVO_FEEDBACK);
  const double deg = mv / 3100.0 * 180.0;
  fb_.position = (deg - pins::kServoRestDeg) * pins::kServoMmPerDeg;
  fb_.position_valid = mv > 50 && mv < 3150;  // rails => broken wire
  fb_.current_valid = false;                  // no servo current sense in this design
  if (!energised_ || !fb_.active) return;
  const double dt = (now - last_ms_) / 1000.0;
  last_ms_ = now;
  const double step = speed_ * dt;
  if (std::fabs(target_mm_ - cmd_mm_) <= step) cmd_mm_ = target_mm_;
  else cmd_mm_ += (target_mm_ > cmd_mm_ ? step : -step);
  writeAngle(pins::kServoRestDeg + cmd_mm_ / pins::kServoMmPerDeg);
  if (cmd_mm_ == target_mm_ && (!fb_.position_valid || std::fabs(fb_.position - target_mm_) <= 0.5)) {
    fb_.active = false;
    fb_.at_target = true;
    fb_.status = "at_target";
  }
}

// ---------------- DC motor retrieval ----------------
bool DcMotorRetrieval::begin() {
  pinMode(pins::MOTOR_EN, OUTPUT);
  digitalWrite(pins::MOTOR_EN, LOW);  // driver disabled by default
  pwmSetup(pins::MOTOR_IN1, kMotorCh1, kMotorFreq, kMotorRes);
  pwmSetup(pins::MOTOR_IN2, kMotorCh2, kMotorFreq, kMotorRes);
  pwmWrite(pins::MOTOR_IN1, kMotorCh1, 0);
  pwmWrite(pins::MOTOR_IN2, kMotorCh2, 0);
  pinMode(pins::ENCODER_A, INPUT_PULLUP);
  pinMode(pins::ENCODER_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pins::ENCODER_A), encoderIsr, CHANGE);
  analogSetPinAttenuation(pins::MOTOR_CURRENT, ADC_11db);
  fb_.status = "idle";
  return true;
}

void DcMotorRetrieval::start(double target_m, double speed_m_s) {
  target_m_ = target_m;
  speed_m_s_ = speed_m_s;
  duty_ = std::min(1.0, speed_m_s / 0.6);  // open-loop duty estimate (PROVISIONAL: 0.6 m/s at 100%)
  digitalWrite(pins::MOTOR_EN, HIGH);
  pwmWrite(pins::MOTOR_IN1, kMotorCh1, static_cast<uint32_t>(duty_ * ((1 << kMotorRes) - 1)));
  pwmWrite(pins::MOTOR_IN2, kMotorCh2, 0);
  fb_.active = true;
  fb_.at_target = false;
  fb_.status = "retrieving";
}

void DcMotorRetrieval::stop() {
  pwmWrite(pins::MOTOR_IN1, kMotorCh1, 0);
  pwmWrite(pins::MOTOR_IN2, kMotorCh2, 0);
  digitalWrite(pins::MOTOR_EN, LOW);
  fb_.active = false;
  if (fb_.status != "at_target") fb_.status = "stopped";
}

void DcMotorRetrieval::resetPosition() {
  noInterrupts();
  g_enc_count = 0;
  interrupts();
  last_count_ = 0;
  fb_.position = 0;
}

void DcMotorRetrieval::update(uint32_t now) {
  noInterrupts();
  const int32_t c = g_enc_count;
  interrupts();
  const double revs = c / (pins::kEncoderCountsPerRev / 2.0);  // x2 decoding
  fb_.position = revs * pins::kSpoolCircumferenceM;
  const uint32_t dt_ms = now - last_ms_;
  if (dt_ms > 0) fb_.speed = (c - last_count_) / (pins::kEncoderCountsPerRev / 2.0) * pins::kSpoolCircumferenceM / (dt_ms / 1000.0);
  last_count_ = c;
  last_ms_ = now;
  const double v = analogReadMilliVolts(pins::MOTOR_CURRENT) / 1000.0;
  fb_.current_a = (v - pins::kCurrentSenseZeroV) / pins::kCurrentSenseVPerA;
  fb_.current_valid = v > 0.05 && v < 3.1;
  if (fb_.active && fb_.position >= target_m_) {
    stop();
    fb_.at_target = true;
    fb_.status = "at_target";
  }
}

// ---------------- Health ----------------
bool Esp32HealthMonitor::begin() {
  pinMode(pins::ESTOP_IN, INPUT_PULLUP);
  analogSetPinAttenuation(pins::BATTERY_DIVIDER, ADC_11db);
  return true;
}
double Esp32HealthMonitor::busVoltageV() {
  return analogReadMilliVolts(pins::BATTERY_DIVIDER) / 1000.0 / pins::kBatteryDividerRatio;
}
double Esp32HealthMonitor::temperatureC() { return temperatureRead(); }  // on-die sensor, coarse
bool Esp32HealthMonitor::estopActive() { return digitalRead(pins::ESTOP_IN) == HIGH; }  // NC loop open/broken

// ---------------- Serial comm ----------------
bool SerialCommAdapter::begin() {
  Serial.begin(115200);
  return true;
}
void SerialCommAdapter::poll(uint32_t now) {
  while (Serial.available()) {
    const char ch = static_cast<char>(Serial.read());
    if (ch == '\n') {
      if (rx_ == "HB") last_hb_ = now;
      else if (!rx_.empty()) pending_ = rx_;
      rx_.clear();
    } else if (ch != '\r' && rx_.size() < 64) {
      rx_ += ch;
    }
  }
}
bool SerialCommAdapter::sendLine(const std::string& line) {
  Serial.println(line.c_str());
  return true;
}
bool SerialCommAdapter::takeCommand(std::string* cmd) {
  if (pending_.empty()) return false;
  *cmd = pending_;
  pending_.clear();
  return true;
}
void SerialLineSink::writeLine(const std::string& line) { Serial.println(line.c_str()); }

}  // namespace hw
}  // namespace hp

#endif  // ARDUINO && ESP32
