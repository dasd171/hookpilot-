# HookPilot firmware (ESP32) — foundation

**Status: prototype foundation. Everything here has run on a host computer only: simulation with
mock hardware. Nothing has been compiled for or flashed to an ESP32, and no real sensor, servo,
motor or battery has been connected. All thresholds and limits are configurable PLACEHOLDERS:
"provisional - derive from calibration + labeled data".**

Shared rules: `../docs/SHARED_CONTRACT.md`. Event format:
`../packages/event-schema/hookpilot-event.schema.json` (schema 1.0.0).

---

## 1. Architecture

```
            +-------------------+     ProcessedSample     +---------------------------+
 HX711 ---> |  TensionSensor    | --> SignalProcessor --> |  BiteDetector             |
 (or mock)  +-------------------+   (validity, EMA        |  deterministic-baseline-v1|
                                     baseline, rate,      |  -> Classification only   |
                                     variance, detrend)   +-------------+-------------+
                                                                        | result + reason + rule_trace
                                                                        v
 Clock, Watchdog, DeviceHealthMonitor,      +---------------------------------------------+
 CommunicationAdapter  ------------------>  | HookPilotController (state machine)         |
                                            | IDLE MONITOR CANDIDATE CONFIRM HOOK_SET      |
                                            | RETRIEVE VERIFY SAFE_STOP                    |
                                            +----------------------+-----------------------+
                                                                   | ActuatorRequest (REQUEST only)
                                                                   v
                         +-----------------------+   permit()  +---------------------------+
                         | ActuatorGate          | <---------> | SafetyController          |
                         | (only path to motion) |             | (independent; never sees  |
                         +-----+-----------+-----+             |  classifier output)       |
                               v           v                   +---------------------------+
                  HookSettingActuator  RetrievalController
                  (servo / mock)       (DC motor+encoder+current / mock)

 EventLogger: one JSON line per candidate cycle / fault -> canonical JSON, sha256, hash chain
```

* **Interfaces** (`include/hookpilot/interfaces.h`, pure virtual): `TensionSensor`, `SignalProcessor`,
  `BiteDetector`, `SafetyController`, `HookSettingActuator`, `RetrievalController`, `EventLogger`,
  `DeviceHealthMonitor`, `CommunicationAdapter`, `Clock`, `Watchdog` (+ `LineSink`).
* **Core** (`src/core`, hardware independent, C++17): signal processor, classifier, safety controller +
  actuator gate, state machine, JSON/sha256/event logger, calibration math, synthetic scenario generator.
* **ESP32 drivers** (`src/hw`, guarded by `#if defined(ARDUINO) && (ESP_PLATFORM || ESP32)`):
  HX711 load cell, LEDC servo hook-set, DC motor + quadrature encoder + analog current sense,
  battery divider / e-stop / die temperature, task watchdog, serial link. **Stubs, never compiled
  against the real Arduino-ESP32 core** (only syntax-checked on host against hand-written header stubs).
* **Host mocks** (`host/`): `MockTensionSensor` replays `hookpilot-synth-v1` signals; mock actuators model
  position and current with injectable faults (jam, overshoot, stall, encoder loss, runaway, sensor
  disconnect/NaN/out-of-range/stuck, comm loss, undervoltage, e-stop, over-temperature, watchdog reset).
  Every mock prints `[MOCK] <Class>: SIMULATION ONLY - controls no hardware` once and returns
  `controlsHardware() == false`.

### State machine

| From | Event | To |
|---|---|---|
| boot (any reset, incl. watchdog) | outputs off | `IDLE`, disarmed. After a watchdog reset `WATCHDOG_RESET` is latched and a boot record logged; arming is refused until an operator `RESET` acknowledges it. |
| `IDLE` | operator ARM + safety `arm()` ok | `MONITOR` |
| `MONITOR` | `delta >= trigger_delta_n` (and re-armed after the previous decision) | `CANDIDATE` (baseline frozen) |
| `CANDIDATE` | decision window elapsed, or early exit (delta > manual threshold) | `CONFIRM` |
| `CONFIRM` | `bite_candidate_confirmed` **and** safety permits hook set | `HOOK_SET` |
| `CONFIRM` | any rejection | `MONITOR` (record logged, baseline unfrozen, re-arm wait) |
| `CONFIRM` | `rejected_sensor_fault` | `SAFE_STOP` |
| `HOOK_SET` | stroke reached target; safety permits retrieval (hook idle → mutual exclusion) | `RETRIEVE` |
| `HOOK_SET` | retrieval denied | `SAFE_STOP` |
| `RETRIEVE` | travel target reached → motor stopped | `VERIFY` (hook returned to rest, gated) |
| `VERIFY` | `verify_ms` elapsed, hook idle | `IDLE` (disarmed; `fish_landed` stays `"unknown"`) |
| any | critical fault latched | `SAFE_STOP` (all actuators stopped, disarmed) |
| `SAFE_STOP` | operator RESET **and** all fault conditions gone | `IDLE` (disarmed). No automatic exit. |

### Safety controller (independent)

Evaluated every tick from raw inputs; latches faults and disarms. The classifier holds no reference to
any actuator; every motion command goes `controller → ActuatorGate → SafetyController::permit()`;
the gate re-checks mutual exclusion from live feedback (defense in depth). `stop()` is never gated.

| Interlock | Default (provisional) | Fault |
|---|---|---|
| E-stop input (NC loop, open = stop) | immediate | `E_STOP` |
| Hard tension limit, any state | > 20 N for 2 samples | `OVER_TENSION` |
| Retrieval force limit (while retrieving; also checked at the retrieval request) | > 8 N for 2 samples | `OVER_TENSION` |
| Sensor validity (NaN, timeout, saturation ≥ 49.9 N or < −5 N, 15 identical readings) | 3 consecutive invalid samples | `SENSOR_DISCONNECTED` / `SENSOR_OUT_OF_RANGE` / `SENSOR_STUCK` |
| Hook-set actuator timeout | 500 ms active | `ACTUATOR_TIMEOUT` |
| Retrieval timeout | 4000 ms active | `ACTUATOR_TIMEOUT` |
| Travel limits | hook 0–15 mm (commands outside denied; feedback outside → fault); retrieval ≤ 1.5 m | `TRAVEL_LIMIT` |
| Stall | < 5 mm progress in 200 ms with current ≥ 1.8 A (after 200 ms grace), or < 5 mm in 600 ms regardless of current (encoder loss) | `MOTOR_STALL` |
| Hook-set and retrieval active together | must never happen | `INTERLOCK` |
| Comm heartbeat | > 300 ms without heartbeat (latched only while armed; always blocks arming) | `COMM_LOSS` |
| Undervoltage (3S Li-ion) | < 10.5 V for 200 ms; arming needs ≥ 10.8 V | `UNDERVOLTAGE` |
| Over-temperature | > 70 °C | `OVER_TEMP` |
| Watchdog reset | boot cause | `WATCHDOG_RESET` (IDLE, needs acknowledgement) |

### deterministic-baseline-v1 (rule order, all thresholds in `config.h`)

Features over a 600 ms decision window from the trigger: frozen EMA baseline, peak delta, peak smoothed
rate, above-trigger duration, continuous run from trigger, short-term variance, Schmitt zero-crossings of
`x − MA5` (band 0.25 N), sensor health.

`R0` sensor health ok → else `rejected_sensor_fault` · `R1` peak delta ≤ 5 N → else
`rejected_manual_handling` · `R2` above-trigger duration ≤ 8000 ms → else `rejected_manual_handling` ·
`R3` oscillations ≤ 4 → else `rejected_vibration` · `R4` peak rate ≥ 2 N/s → else `rejected_noise`
(slow current drift) · `R5` continuous above trigger ≥ 300 ms → else `rejected_transient` · otherwise
`bite_candidate_confirmed`. Each step is written to `rule_trace`, and the thresholds in force go into
every record, labelled provisional.

Differences from `apps/web-demo/src/engine/config.ts`, chosen so the shared synthetic scenarios separate:
`min_rate_n_per_s` 2 (TS 5), `manual_handling_delta_n` 5 (TS 12; the synthetic manual-handling amplitude
is 6.5–12 N), `baseline_alpha` 0.002 (TS 0.02), and an explicit `decision_window_ms` = 600. They are all
still placeholders.

### Event records

`JsonLinesEventLogger` emits one line per candidate cycle, per fault while monitoring, and per
watchdog-reset boot. Each line is already **canonical JSON**: keys sorted recursively, `,`/`:`
separators, numbers formatted like ECMAScript `Number#toString`, strings UTF-8 (JSON.stringify
escaping). `integrity.record_sha256` = sha256 over the record with `record_sha256 = ""` (built-in
SHA-256, FIPS 180-4 vectors tested), `prev_record_sha256` chains records, `signature` = null
(there is no secure element). `fish_landed` is always `"unknown"`. Host output has `source.mode = "simulation"`,
`clock_quality = "host_clock_simulated"`, a simulated epoch of 2026-01-01T00:00:00Z, and `actuation.simulated = true`.
Samples are rounded to 0.1 mN. Non-finite readings are left out of `samples` and counted in
`sensor_data.invalid_samples_omitted`. `interlocks` is a snapshot from the tick the record closed,
taken before actuators were stopped.

---

## 2. Build, test, simulate (host)

Requirements: `make` and a C++17 compiler. The default is zig's clang (`pip install ziglang`) through
`CXX ?= python3 -m ziglang c++`. Any `g++`/`clang++` works with `make CXX=g++`. Without make, use
`sh tools/host_build.sh`.

```sh
cd firmware
make test                          # builds + runs all unit tests (exit code != 0 on failure)
make sim SCENARIO=bite SEED=42     # event JSON lines on stdout, state transitions on stderr
make sim-all SEED=7                # every scenario -> build/sim_<scenario>_7.jsonl
make verify SEED=42                # sim-all + schema + hash + chain check (python3, jsonschema)
./build/hookpilot_sim --list       # scenarios: idle bite noise_current rod_vibration manual_handling
                                   #   sensor_failure excessive_tension motor_stall comm_loss
./build/hookpilot_tests safety     # run only tests whose name contains "safety"
```

With zig the Makefile adds `-mcpu=baseline`. On the build VM, zig's default native-CPU code generation
caused `SIGILL` at `-O2`.

Synthetic signals come from `hookpilot-synth-v1` (C++ port of
`services/evidence-service/evidence_service/synth.py`), seeded with mulberry32. The PRNG matches the
TS/Python reference bit for bit (vectors are in the tests), and the generated samples match Python to 1e-9.
`motor_stall` / `comm_loss` faults are injected `fault_after_retrieval_start_ms` after RETRIEVE begins.

---

## 3. ESP32 target (PROVISIONAL, not compiled here)

`platformio.ini`: `esp32dev`, Arduino framework, `-std=gnu++17`. Layout: `include/` (core headers),
`src/core`, `src/hw`, `src/main_esp32.cpp`. Host-only code sits in `host/` and `test_host/`, outside
`src_dir`. **No PlatformIO toolchain was available, so this configuration has never been built.**
Expect API fixes on the first build. The LEDC code covers both the core 2.x and 3.x APIs, and the task-WDT code covers both IDF 4 and IDF 5.

Serial protocol (115200 8N1, newline-terminated). Host to device: `HB` (heartbeat, ≥ 10 Hz, required
while armed), `ARM`, `DISARM`, `RESET`, `TARE`, `CAL <kg>` (both only in IDLE). Device to host: JSON
event lines, plus `#` log lines.

### Pin assignment — PROVISIONAL (ESP32-WROOM-32 DevKitC)

Defined in `include/hookpilot/board_pins.h`. A unit test checks that no pin is used twice, that no flash
pins are used, that outputs avoid the input-only GPIOs, and that analog inputs are on ADC1.

| Function | GPIO | Dir | Notes |
|---|---|---|---|
| HX711 DOUT | 16 | in | load-cell data/ready |
| HX711 SCK | 17 | out | clock; > 60 µs high powers the HX711 down |
| Servo PWM (hook set) | 18 | out | LEDC ch0, 50 Hz, 500–2500 µs |
| Servo position feedback | 36 (VP) | in, ADC1_CH0 | analog-feedback servo / pot; 0–3.1 V |
| Servo rail enable | 19 | out | load switch/MOSFET; LOW = servo unpowered |
| Motor driver IN1 (PWM) | 25 | out | LEDC ch2, 20 kHz |
| Motor driver IN2 (PWM) | 26 | out | LEDC ch3, 20 kHz |
| Motor driver EN/SLEEP | 23 | out | LOW = driver disabled (default at boot) |
| Encoder A | 32 | in (pull-up) | interrupt, x2 decoding |
| Encoder B | 33 | in (pull-up) | direction |
| Motor current sense | 39 (VN) | in, ADC1_CH3 | ACS712-05B (185 mV/A) through a 2:3 divider, or the driver's current-sense output |
| Battery divider | 34 | in, ADC1_CH6 | 100 kΩ / 22 kΩ → 12.6 V = 2.27 V |
| E-stop input | 27 | in (pull-up) | NC switch to GND; open/broken wire = STOP |
| Status LED | 2 | out | on-board LED |

The e-stop must also **cut actuator power in hardware** (contactor/relay in series with the motor and
servo rails). The GPIO input is only a second channel. Firmware alone is not a safety function.

### Power requirements (assumed, to be confirmed by measurement)

| Rail | Source | Load (estimate) |
|---|---|---|
| Pack | 3S Li-ion, 9.0–12.6 V (10.8 V nominal), 7.5 A fuse, master switch | — |
| Motor | pack via H-bridge (e.g. BTS7960 / DRV8871 class) | 12 V gearmotor, ~0.3 A running, stall up to ~3–5 A (motor dependent) |
| Servo | 6 V buck/BEC ≥ 3 A, switched by `SERVO_PWR_EN` | high-torque servo, ~1 A moving, 2–3 A stall |
| Logic 5 V | buck from pack | ESP32 DevKit (~250 mA peak with Wi-Fi), ACS712 (~10 mA) |
| 3.3 V | DevKit regulator | HX711 (~1.5 mA) + load cell excitation (~3–4 mA for a 1 kΩ bridge) |

Undervoltage cutoff is 10.5 V (3.5 V/cell) and arming needs ≥ 10.8 V. Both are placeholders.
Connect all grounds at one point. Keep the load-cell wiring away from the motor leads.

### Calibration procedure (load cell)

1. Mount the load cell in its final orientation with the line attached but **slack**. Power it up and let it warm
   for 5–10 min (HX711 drift).
2. In `IDLE`, send `TARE`. The firmware averages 40 readings. It rejects the tare if the standard deviation is above
   200 counts (load moving) or if any reading saturates. The mean becomes `offset_counts`.
3. Hang a known, certified mass from the line axis, e.g. 1.000 kg (keep below about 50 % of the cell's rating).
   Wait for it to stop swinging, then send `CAL 1.000`. It averages 40 readings and computes
   `scale_n_per_count = m·9.80665 / (mean − offset)`. It rejects the result on saturation, swing, or a span under 2000 counts.
4. The firmware stores offset, scale, `calibration_version` and `calibrated_utc` in NVS (`Preferences`
   namespace `hp_cal`). The version is `cal-<device_id>-<UTC compact | unsynced>-<8 hex of sha256(offset|scale|mass)>`,
   so any change of constants changes the version. Every event record carries it in `calibration.*`.
5. Check with a second known mass (e.g. 0.5 kg) and record the error in the bench log. Repeat for several
   masses to check linearity. Recalibrate after any mechanical change.
6. Until calibrated, the HX711 driver reports readings as invalid/`degraded`, so the device cannot arm.

---

## 4. Tests

`test_host/` holds a self-contained framework (no external dependencies). Run it with `make test`. It covers:
signal filtering, trigger and confirm/rate/oscillation/manual threshold boundaries, the classification of every
scenario with fixed seeds (42 and 7), a seed sweep over 1–100 for all scenarios, state transitions, the gate's
deny paths (disarmed, mutual exclusion, travel/speed/force limits, misconfigured target), every safety
limit and timeout, fault injection (sensor disconnect, NaN while the driver reports valid, out-of-range, stuck,
excessive tension, motor stall, encoder loss, comm loss, undervoltage, e-stop, over-temperature,
watchdog reset), fault recovery (reset only when cleared, then a new successful cycle with a valid hash
chain), determinism, SHA-256/canonical-JSON/PRNG/synth reference vectors, calibration math, and pin-map sanity.

Last run on the build host: see the parent report. `make test` prints the real totals.

## 5. Hardware assumptions and what is NOT tested on real hardware

Not tested at all (simulation only):
* Any ESP32 compilation, flashing, timing (50 Hz loop jitter, HX711 at 80 SPS, ISR load), memory use
  (the logger builds `std::map`-based JSON on the heap; each record holds up to 400 samples).
* The HX711 bit-bang timing, noise and drift, load-cell creep, temperature effects, and the calibration procedure.
* Servo linkage geometry (0.2 mm/deg assumed), feedback-pot scaling, servo current.
* Motor/encoder constants (1400 counts/rev, 30 mm spool), H-bridge behaviour, the ACS712 scaling and noise,
  the real stall current, and the line-load coupling (the mocks use simple first-order models).
* Battery divider accuracy (ESP32 ADC non-linearity), e-stop wiring, the hardware power cut.
* The serial heartbeat protocol with a real host, Wi-Fi/BLE (not implemented), NTP/RTC time (records
  from the device say `rtc_unsynced`).
* Every threshold and limit. They are placeholders, not derived from calibration or labelled bite data.
  The synthetic scenarios show internal consistency only; they say nothing about real fish or real water.

Known limitations:
* Classifier misses on synthetic data: in seeds 1–100, 86/100 `bite` runs are confirmed. The misses are
  bites whose amplitude falls below the 1 N trigger, short holds, or slow rises, all within the generator's
  ranges. `noise_current` (26/100) and `rod_vibration` (18/100) sometimes never reach the trigger, so they
  produce no record instead of the expected rejection. There were zero false bite confirmations across the
  500 non-bite runs.
* `CONFIRM` is resolved in the same tick as the end of the decision window, so it is a transient state.
* The decision latency is 600 ms after the trigger (the observation window). This is a placeholder.
* No secure element: `integrity.signature` is null, and the hash chain lives only in RAM (it restarts on reboot).
* `idle` produces no records, because `no_event` is only logged when a fault occurs while monitoring.
* AddressSanitizer is not available in the zig toolchain used here. UBSan was run on the test suite
  without findings. Only the `printf` fallback of the shortest-number formatter has been exercised;
  this libc++ does not advertise floating `to_chars`.
