# HookPilot hardware block diagram (PROVISIONAL – nothing below has been built)

Rendered version: [`block-diagram.svg`](block-diagram.svg) (hand-drawn SVG, same content).

```mermaid
flowchart LR
  subgraph PWR["Power (IP67 battery box)"]
    BAT["3S Li-ion pack 11.1 V nom.<br/>with BMS"] --> FUSE["Main fuse 7.5 A<br/>+ master switch"]
    FUSE --> BUS["12 V bus"]
    BUS --> BUCK5["Buck 5 V / 3 A"]
    BUS --> BUCK8["Buck 7.4 V / 5 A<br/>(servo rail)"]
    BUCK5 --> LDO["3.3 V (ESP32 module LDO)"]
  end

  subgraph SAFE["Independent safety chain (hard-wired + supervisor MCU)"]
    ESTOP["E-stop mushroom<br/>(NC, latching)"] --> CHAIN
    OT["Hook lever over-travel<br/>limit switch (NC)"] --> CHAIN
    SUP["Safety supervisor MCU<br/>(XIAO RP2040 class)"] -->|"enable"| CHAIN["Actuator power<br/>enable relay / MOSFET<br/>(AND of all)"]
    CMP["Motor over-current<br/>comparator"] --> SUP
  end

  subgraph CTRL["Main controller (IP67 electronics box)"]
    ESP["ESP32-WROOM-32E<br/>firmware: classifier,<br/>state machine, logger"]
    HX["HX711 24-bit ADC"] --> ESP
    INA["INA219 bus V/I (I2C)"] --> ESP
    ENC["AS5600 pivot angle (I2C)"] --> ESP
    DIV["Battery divider 100k/22k"] --> ESP
    TMP["DS18B20 motor temp"] --> ESP
    ARM["Arm key switch"] --> ESP
  end

  subgraph SENS["Line-tension sensor (guide-mounted)"]
    LINE(("fishing line")) --> ROLL["3-roller deflection guide<br/>centre roller on lever"]
    ROLL --> LC["1 kg bar load cell<br/>+ overload stop"]
  end
  LC -->|"4-wire, shielded"| HX

  subgraph ACT["Actuators"]
    SERVO["Hook-set servo (waterproof,<br/>60 kg·cm class) -> pushrod -><br/>rod-holder pivot lever"]
    DRV["DRV8874 motor driver<br/>(IPROPI current sense, nFAULT)"] --> MOT["12 V gearmotor + encoder<br/>-> slip clutch -> reel handle<br/>(reel drag preserved)"]
  end

  ESP -->|"PWM"| SERVO
  ESP -->|"PH / EN / nSLEEP"| DRV
  DRV -->|"IPROPI, nFAULT"| ESP
  MOT -->|"encoder A/B"| ESP
  ESP -->|"heartbeat"| SUP
  ESTOP -->|"sense"| ESP
  CHAIN -->|"switched 7.4 V"| SERVO
  CHAIN -->|"switched 12 V (VM)"| DRV
  DRV -->|"current mirror"| CMP
  BUCK8 --> CHAIN
  BUS --> CHAIN
  BUCK5 --> SUP
  LDO --> ESP

  ESP <-->|"USB-serial / Wi-Fi<br/>JSON lines"| HOST["Edge host (laptop / SBC)<br/>edge-controller -> evidence-service<br/>-> MockKonnexAdapter (SIMULATION)"]
```

## Design rules expressed by the diagram

1. **Actuator power is gated outside the ESP32.** The servo rail and motor-driver VM pass through an
   enable element that is the logical AND of: e-stop (NC, latching), hook-lever over-travel switch (NC)
   and the safety supervisor's enable output. Firmware can *request* actuation; it cannot energise an
   actuator whose power rail the safety chain has cut.
2. **Safety supervisor is a separate MCU** with its own clock and power branch. It drops the enable
   when the ESP32 heartbeat stops (comm-loss / watchdog), when the analogue over-current comparator
   trips, or when its own e-stop sense input opens. After any trip it requires a manual reset
   (button on the box) and boots disarmed — mirroring the contract (`SAFE_STOP -> explicit reset -> IDLE disarmed`).
3. **Sensing path is passive and independent of actuation**: load cell → HX711 → ESP32. The
   classifier never has a direct path to actuators; all requests pass the firmware safety controller
   and then the hardware chain.
4. **Retrieval drives the reel handle, never the spool**, so the reel's drag remains the force limiter
   for fish-side loads; a slip clutch and the driver's current regulation are additional limits.
5. **The host is outside the safety loop.** Loss of the host link is a fault (COMM_LOSS) but nothing
   safety-relevant depends on the host being present.

All part numbers are candidates; see `../bom/bom.csv` (prices are ESTIMATES) and
`../wiring/wiring-table.md` (PROVISIONAL pin map).
