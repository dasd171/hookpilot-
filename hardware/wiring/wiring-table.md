# Wiring table — PROVISIONAL

**PROVISIONAL.** Proposed pin map for the bench prototype. Not built, not continuity-tested. The
firmware hardware layer (`firmware/src/hw/`) had no pin assignments when this was written; firmware
should adopt or reconcile this table (single source of truth to be decided). Machine-readable copy:
`wiring.csv`.

Board: ESP32-WROOM-32E DevKitC. Avoided: strapping pins GPIO0/2/12/15 for inputs that could be held
at boot (GPIO2 only drives the LED; GPIO5 has a pull-up via the 1-Wire resistor), flash pins 6–11.
ADC: only ADC1 pins (32–39) are used, because ADC2 is unavailable while Wi-Fi is on.

## ESP32 connections

| Signal | ESP32 pin | Direction | Connects to | Notes |
|---|---|---|---|---|
| HX711 DOUT | GPIO34 | in | HX711 DT | input-only pin, fine for data |
| HX711 SCK | GPIO25 | out | HX711 SCK | holding SCK high > 60 µs powers HX711 down |
| HX711 RATE | — | — | HX711 RATE → VCC | 80 SPS (needed for 50 Hz sampling); board must expose RATE |
| Hook-set servo PWM | GPIO18 | out | servo signal (via 3.3→5 V buffer if servo needs it) | LEDC 50 Hz; servo power from **switched** 7.4 V rail |
| Motor PH (direction) | GPIO26 | out | DRV8874 PH | |
| Motor EN (PWM) | GPIO27 | out | DRV8874 EN | LEDC ~20 kHz |
| Motor nSLEEP | GPIO13 | out | DRV8874 nSLEEP | pulled low by 100 k → driver asleep at boot |
| Motor nFAULT | GPIO35 | in | DRV8874 nFAULT | open-drain: external 10 k pull-up to 3.3 V |
| Motor current (IPROPI) | GPIO36 (SVP, ADC1_CH0) | analog in | DRV8874 IPROPI + R_IPROPI | also feeds the over-current comparator |
| Encoder A | GPIO32 | in | motor encoder A | PCNT unit; encoder powered 3.3 V or level-shifted |
| Encoder B | GPIO33 | in | motor encoder B | |
| I2C SDA | GPIO21 | bi | INA219 (0x40), AS5600 (0x36) | 4.7 k pull-ups to 3.3 V |
| I2C SCL | GPIO22 | out | INA219, AS5600 | 100 kHz |
| Battery voltage | GPIO39 (SVN, ADC1_CH3) | analog in | divider 100 k / 22 k + 100 nF | 12.6 V → 2.27 V; 11 dB attenuation; calibrate with eFuse Vref |
| E-stop sense | GPIO4 | in | auxiliary NO/NC contact of e-stop | internal pull-up; informs firmware (E_STOP fault); **not** the safety path |
| Arm key switch | GPIO14 | in | key switch to GND | internal pull-up; open = disarmed |
| Home limit switch | GPIO16 | in | hook lever home switch | internal pull-up |
| Over-travel switch (sense) | GPIO17 | in | second contact of over-travel switch | the NC contact itself is in the hardware safety chain |
| Heartbeat to supervisor | GPIO23 | out | supervisor GPIO (e.g. D1) | square wave; supervisor drops enable if it stops > 300 ms (`comm_timeout_ms` analogue) |
| Motor temperature | GPIO5 | bi | DS18B20 data | 4.7 k pull-up (keeps strapping pin high at boot) |
| Buzzer | GPIO19 | out | buzzer via NPN | |
| Status LED | GPIO2 | out | on-board LED | |
| UART0 TX/RX | GPIO1/3 | — | USB-serial to edge host | JSON lines to `services/edge-controller` |

## Power and safety chain

| From | To | Wire | Notes |
|---|---|---|---|
| Battery + | 7.5 A fuse → master switch → 12 V bus | 18 AWG | fuse at the pack terminal |
| 12 V bus | Buck 5 V → ESP32 5V pin, supervisor 5V, HX711/sensors (3.3 V from ESP32 LDO) | 22 AWG | logic is **not** switched by the e-stop (keeps logging/telemetry alive) |
| 12 V bus | Buck 7.4 V → enable relay contact A → servo + | 18 AWG | switched servo rail |
| 12 V bus | enable relay contact B / high-side MOSFET → DRV8874 VM | 18 AWG | switched motor rail; bulk cap 100 µF at VM |
| Enable relay coil + | 12 V via: e-stop NC → over-travel switch NC → supervisor-controlled MOSFET | 22 AWG | series chain: any open element de-energises both actuator rails |
| Supervisor ENABLE out | MOSFET gate (coil low side) | 22 AWG | supervisor boots with ENABLE low; needs manual reset button after a trip |
| DRV8874 IPROPI | LM393 comparator (+), threshold divider (−) → supervisor TRIP in | 24 AWG | analogue over-current, independent of ESP32 |
| Grounds | star point at the 12 V bus negative | — | load-cell shield grounded at HX711 end only |

## Sensor cable

| Load cell wire (typical colour) | HX711 | Notes |
|---|---|---|
| Red (E+) | E+ | excitation |
| Black (E−) | E− | |
| White (A−) | A− | colours vary by maker — **verify with the datasheet** |
| Green (A+) | A+ | |
| Shield | GND at HX711 only | |
