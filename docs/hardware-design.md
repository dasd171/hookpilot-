# Hardware design (proposal, rev 0 — not built)

Detailed spec: `hardware/mechanical/prototype-spec.md`. Diagram: `hardware/architecture/`.
BOM: `hardware/bom/bom.csv` (ESTIMATES). Wiring: `hardware/wiring/wiring-table.md` (PROVISIONAL).

## Goals and non-goals
- **Goal:** a bench-testable prototype that measures line tension with a defined force path, sets the
  hook with a short, travel-limited motion, and assists retrieval without bypassing the reel drag —
  with safety independent of the classifier and of the firmware.
- **Non-goals (for now):** autonomous unattended fishing, live-fish use, salt water, multiple rods,
  hardware attestation (secure element) — all later phases.

## Key decisions and why

| Decision | Reason | Alternative considered |
|---|---|---|
| Hook-set on a **separate rod-holder pivot** | actuator loads never pass through the reel; rod stays a normal rod; mechanical stops are on the frame | reel-mounted actuator (rejected: loads the reel, hard to stop mechanically) |
| **Servo + pushrod + butt lever (r = 60 mm)** with hard stop at 16 mm stroke | commanded 12 mm ≈ 11.5° → ≈ 0.36 m tip sweep at ≈ 1.8 m/s; firmware units (mm) match the pushrod | spring-loaded latch release (simpler force, but uncontrolled speed and needs re-cocking) |
| **Guide-mounted 3-roller load-cell lever** | fixed wrap angle → linear F = 2T·sin(θ/2)·(a/b); electronics stay near the reel; overload stop protects cell | tip-mounted cell (closer to bite, but mass + cable on blank); reel-seat strain gauges (indirect) |
| **Retrieval drives the handle, not the spool** | the reel drag remains the fish-side force limiter; a slip clutch and current regulation add margins | spool-direct drive (rejected: bypasses drag) |
| **ESP32 + HX711 at 80 SPS** | cheap, well-documented, ADC1 for analog, PCNT for encoder, Wi-Fi for host link | STM32 + ADS1232 (better analog, more effort) |
| **Independent safety supervisor MCU + hard-wired chain** | firmware bugs must not be able to energise actuators; e-stop must work with the ESP32 hung | firmware-only interlocks (insufficient) |
| **3S Li-ion 11.1 V** | 12 V motor, 7.4 V servo rail via buck; matches firmware `undervoltage_v` 10.5 V | 2S (too low for 12 V motor) |

## Electrical summary
- Logic rail (5 V → 3.3 V) is never cut by the e-stop, so logging continues during a stop.
- Actuator rails (7.4 V servo, 12 V motor VM) pass through an enable relay/MOSFET in series with the
  e-stop NC contact, the over-travel NC switch and the supervisor's enable output.
- Measurements for interlocks: line tension (HX711), motor current (DRV8874 IPROPI → ADC and analog
  comparator), bus voltage/current (INA219, divider), pivot angle (AS5600), encoder progress, motor
  temperature (DS18B20), e-stop and arm-key sense.

## Sensor design numbers (provisional)
- θ = 20°, a/b = 1 → F_cell = 0.347·T; T_max (hard max 20 N) → 6.9 N on a 1 kg (9.81 N) cell → 70 % FS.
- HX711 at 80 SPS gives headroom for 50 Hz processing; expect 16–18 noise-free bits on the bench
  (to be measured); the simulator assumes 0.02 N RMS noise, which must be checked against reality.

## Mechanical risks to retire first (bench)
Sensor friction/hysteresis and temperature drift; hook-set effectiveness of an 11.5° sweep; slip
clutch repeatability; motor housing ingress; line abrasion on rollers; entanglement around rollers.

## Waterproofing
IP67 is a **target** for both boxes and connectors (gaskets, IP68 glands, M8/M12 connectors, ePTFE
vent, conformal coat, potted load cell, sealed motor housing). Verification by immersion test is a
roadmap item; no IP claim is made today.
