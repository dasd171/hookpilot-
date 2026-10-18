# Safety

Status: hazard analysis and design rules for a **bench prototype**. Nothing has been built. The
firmware safety controller (`firmware/src/core/safety_controller.cpp`) and its two ports (Python
`services/evidence-service/evidence_service/safety.py`, web demo `apps/web-demo/src/engine/safety.ts`) are
simulations of the logic, not a validated safety function. Since 2026-10-18 all three run the same safety
controller, debounce rules, actuator models and fault-injection timing, specified in `docs/engine-spec.md`
and cross-checked tick by tick (docs/testing.md).

## 1. Ground rules
1. **Bench only first.** Phases 1–2 (roadmap) use dead weights, a pulley rig and a motorised load —
   no water, no people near the rod tip, no live fish.
2. **No live fish until** bench and controlled-load-rig results meet the TARGETS in testing.md, the
   e-stop and fault table below are verified on hardware, and pond trials have an independent observer.
3. **Classifier cannot actuate.** It requests; the firmware safety controller permits/denies; the
   hardware chain can still cut actuator power regardless of firmware.
4. **Fail to safe:** any fault → SAFE_STOP (actuators de-energised: servo rail and motor VM cut, motor
   driver asleep). Reset only by explicit operator action with faults cleared → IDLE **disarmed**.
5. **Arming is a deliberate act** (removable key). Disarm before touching rod, line or reel.
6. **Never infer catch outcome:** `fish_landed` stays `unknown` unless an independent observer
   confirms it.

## 2. Independent safety controller
- Separate MCU (XIAO RP2040 class) with its own clock and power branch, plus a hard-wired series chain:
  e-stop NC (latching) → hook-lever over-travel NC → supervisor-controlled MOSFET → enable relay coil.
- Supervisor drops the enable on: ESP32 heartbeat missing > 300 ms, analogue motor over-current, its own
  e-stop sense, or its own watchdog. It boots with enable OFF and needs a manual reset button.
- Firmware safety controller (inside the ESP32) implements the contract interlocks; the hardware layer
  covers the cases where the ESP32 itself is wrong (hung, brown-out, bad code).

## 3. Hazard analysis (preliminary, qualitative)

S = severity (1 minor … 4 serious injury), L = likelihood before mitigation (1 rare … 4 likely).

| # | Hazard | Cause | S | L | Mitigations | Residual / verification |
|---|---|---|---|---|---|---|
| H1 | Rod / hook strikes a person during hook-set | unexpected actuation near people; false trigger | 4 | 2 | arming key; travel ≤ 15.3° hard stop; bench-only phases; keep-out zone in front of tip; disarm before handling; classifier manual-handling rule (heuristic only) | barbless/no hook on bench; verify hard stop and over-travel switch |
| H2 | Hook / lure flies back (line snaps under hook-set) | high tension + fast sweep | 4 | 2 | OVER_TENSION trips; hook-set speed limited; no hook on bench; eye protection in pond tests | measure peak tension during hook-set on rig |
| H3 | Pinch / crush at pivot or pushrod | fingers near linkage | 3 | 2 | guard over pushrod; quick-release pin; actuator power cut by e-stop | guard inspection |
| H4 | Reel / line overload, rod breakage | retrieval winds against a snag | 2 | 3 | drives handle (drag preserved); slip clutch; OVER_TENSION above retrieval_force_limit_n (2 consecutive samples); stall detection; current regulation. (A firmware "pause winding above X N" assist was modelled only in the Python engine and was removed from all engines on 2026-10-18; it is not part of the design until specified and bench-tested.) | rig test: snag at 5/10/20 N |
| H5 | Motor stall overheating / fire | jam, snag | 3 | 2 | MOTOR_STALL (current + no progress); analogue over-current trip; DS18B20 OVER_TEMP; fuse | stall test on bench |
| H6 | Uncommanded motion after reset/reboot | watchdog reboot, brown-out | 4 | 2 | boot into IDLE disarmed; supervisor enable OFF at boot; driver nSLEEP pulled low; WATCHDOG_RESET fault latched | power-cycle test under load |
| H7 | Wrong decision from bad sensor | load cell broken/stuck/saturated, cable damage | 3 | 3 | sensor health (stuck, out-of-range, disconnected) → SENSOR_* fault → SAFE_STOP; pre-session zero/span check | inject faults on bench |
| H8 | Battery thermal event / short | damaged pack, water ingress | 4 | 1 | BMS; fuse at pack; IP67-target sealed box; no field charging; UNDERVOLTAGE stop | inspection; ingress test |
| H9 | Loss of host link hides state | cable/Wi-Fi drop | 2 | 3 | COMM_LOSS → SAFE_STOP during actuation; host is outside the safety loop; local logging continues | pull cable during retrieval |
| H10 | Water ingress → shorts, erratic outputs | immersion, rain | 3 | 3 | IP67 target enclosures, glands, conformal coat; actuators default off | immersion test (roadmap) |
| H11 | Animal welfare: prolonged fight / deep hooking | automation fights fish without a person | 3 | 2 | no unattended operation; observer present; retrieval assist only; local regulations; barbless hooks in trials | ethics/regulation check before pond phase |
| H12 | Misleading evidence (claims a catch or a safe run that did not happen) | inferred outcomes; tampered logs | 2 | 2 | fish_landed never inferred; hash chain; replay; verifier criteria; SIMULATION labels | verifier tests (tests/unit/test_konnex_adapter.py) |

## 4. Fault table

Names match the schema / firmware fault strings. Values are provisional (firmware `config.h`).

| Fault | Detection | Threshold (provisional) | Action | Exit |
|---|---|---|---|---|
| E_STOP | e-stop NC chain open (hardware) + sense input | contact open | actuator power cut in hardware; firmware SAFE_STOP | release e-stop + explicit reset → IDLE disarmed |
| OVER_TENSION | load cell | > retrieval_force_limit_n 8 N while retrieving (2 samples); > hard_max_tension_n 20 N any state | SAFE_STOP | reset with tension below limits |
| SENSOR_DISCONNECTED | HX711 timeout / NaN | 3 consecutive invalid samples | SAFE_STOP; classifier rejects (`rejected_sensor_fault`) | sensor valid + reset |
| SENSOR_OUT_OF_RANGE | reading < −5 N or ≥ 49.9 N | 3 samples | same | same |
| SENSOR_STUCK | bit-identical readings | 15 consecutive | same | same |
| MOTOR_STALL | current ≥ 1.8 A and < 5 mm line progress in 200 ms (after 200 ms grace); or < 5 mm in 600 ms at any current (encoder loss) | — | SAFE_STOP | reset after jam cleared |
| ACTUATOR_TIMEOUT | hook-set not at target in 500 ms; retrieval not done in 4000 ms | — | SAFE_STOP | reset |
| TRAVEL_LIMIT | pivot angle > 15 mm equivalent (firmware); NC over-travel switch at 15.5 mm (hardware) | — | SAFE_STOP / power cut | reset after inspection |
| COMM_LOSS | link reports down, or no host heartbeat for > 300 ms (firmware); no ESP32 heartbeat (supervisor) | 300 ms; latched only while armed (arming is refused while present) | SAFE_STOP / power cut | link restored + reset |
| UNDERVOLTAGE | bus voltage | < 10.5 V for 200 ms (re-arm ≥ 10.8 V) | SAFE_STOP, arming refused | voltage restored + reset |
| WATCHDOG_RESET | reset reason at boot | — | boot into IDLE disarmed with fault latched | explicit clear |
| OVER_TEMP | DS18B20 | > 70 °C | SAFE_STOP | cooled + reset |
| INTERLOCK | hook-set and retrieval observed active together (must never happen) | any | SAFE_STOP | investigation + reset |

Interlocks that are not faults (provisional): hook-set and retrieval are never commanded together (safety
controller + gate re-check); a retrieval request is denied until `hook_to_retrieval_gap_ms` (100 ms) after
the hook-set stroke ended; while disarmed (IDLE) the classifier still observes and logs, but nothing is ever
requested; a completed cycle ends disarmed. Debounce: over-tension 2 consecutive valid samples, sensor faults
3 consecutive invalid samples, undervoltage 200 ms. Fault latch ticks are recorded in
`safety.fault_latched_t_ms`.

Simulation evidence (tests/reports/simulation_report.md, Python engine, synthetic): the expected fault
latched and the run ended in SAFE_STOP in 300/300 runs each for sensor_failure, excessive_tension,
motor_stall and comm_loss; no record showed simultaneous actuation or actuation while disarmed
(2700 records). Simulated detection latency after the condition starts: over-tension 20 ms, motor stall
180 ms, comm loss 300 ms (all set by the debounce/timeout values). The fault-injection cases in
`tools/xcompare.py` (sensor disconnect / NaN / out-of-range / stuck, over-tension, stall, encoder loss, hook
jam, comm loss, link down, undervoltage, watchdog reset, e-stop, over-temperature) produce identical faults
and latch ticks in firmware, Python and the web demo. These show the *logic* behaves as specified on
synthetic inputs; they are not evidence about hardware response times.

## 5. Things that must be measured on hardware before trusting any of this
- E-stop response: time from button press to actuator rails at 0 V and motion stopped (TARGET < 100 ms).
- Over-travel switch and hard stop under full servo torque.
- Supervisor heartbeat timeout and reset behaviour.
- Fault injection for every row above, logged as event records.
