# HookPilot mechanical prototype specification (rev 0 — PROPOSAL, not built)

Status: design intent only. No part has been made or tested. All dimensions, forces and
speeds are **provisional starting points** that need verification on a bench rig before any use with
a live line, and long before any live fish (see `docs/safety.md`, `docs/roadmap.md`).

Values that also live in firmware (`firmware/include/hookpilot/config.h`) are quoted with their
config names so the mechanism and the code use the same numbers.

---

## 1. Architecture: three separate subassemblies

| Subassembly | Purpose | Interfaces with the rod/reel |
|---|---|---|
| A. Rod-holder pivot with hook-set mechanism | sets the hook with a short, fast, travel-limited rod sweep | rod sits in an open cradle; **no clamp on the reel** |
| B. Line-tension sensor guide | measures line tension through a defined force path | clamps to the blank above the reel seat; line passes over 3 rollers |
| C. Retrieval-assist drive | turns the reel handle within a torque limit | couples to the handle only; reel drag is untouched |

They are separate on purpose: the hook-set never acts through the reel, the retrieval never acts on
the rod-holder pivot, and the sensor carries no actuator load. Any one can be removed and the rod
still works as a normal rod.

---

## 2. Subassembly A — rod-holder pivot hook-set

### 2.1 Geometry (provisional)
- Base: aluminium frame (6061, 6 mm plate) on a standard bank spike or a clamp to a rail/bench.
- Pivot shaft: Ø8 mm stainless 316, two sealed deep-groove bearings (608-2RS stainless), ~120 mm apart.
- Cradle: U-shaped, rubber-lined, rod held by gravity and line load only, plus a light spring clip
  that releases at < 20 N upward pull. **The rod can always be lifted out by hand.**
- Butt lever: rigidly attached to the cradle, extends **r = 60 mm** behind the pivot axis.
- Pushrod: from the servo horn (r_h ≈ 25 mm) to the butt lever, ball-joint ends; connected to the
  lever by a **quick-release pin** (manual release decouples the actuator from the rod holder).

### 2.2 Travel and stops (mapped to firmware units: mm of pushrod stroke)
| Item | Value | Angle at r = 60 mm | Config name |
|---|---|---|---|
| Commanded hook-set stroke | 12 mm | ≈ 11.5° | `hook_set_target_mm` |
| Firmware travel limit | 15 mm | ≈ 14.3° | `hook_set_travel_limit_mm` |
| Over-travel limit switch (NC, in hardware safety chain) | 15.5 mm | ≈ 14.8° | — |
| Mechanical hard stop (elastomer-faced, on frame) | 16 mm | ≈ 15.3° | — |
| Commanded speed | 60 mm/s (≈ 200 ms per stroke) | ≈ 1 rad/s | `hook_set_speed_mm_s` |

With a 2.1 m rod pivoting about 0.3 m above the butt, the tip arm is ≈ 1.8 m, so 11.5° gives a tip
sweep of ≈ 0.36 m at ≈ 1.8 m/s peak. Whether that sets a hook reliably is **unknown** and is one of
the bench questions (docs/testing.md). The mechanical stop, not software, defines the absolute
maximum angle; the over-travel switch opens the actuator power chain before the hard stop is reached.

### 2.3 Force path (hook-set)
servo output → horn → pushrod (axial load) → butt lever → cradle → pivot shaft → bearings → frame →
spike/clamp. Over-travel: lever → elastomer hard stop → frame. Fish load during hook-set:
line → rod blank → cradle → pivot → frame. The servo only has to overcome the moment of line tension
about the pivot plus rod inertia:

- line tension T at the tip, tip arm L ≈ 1.8 m, lever r = 60 mm → pushrod force F ≈ T·L/r = 30·T
- at a bite-level T = 2 N: F ≈ 60 N; at T = 3 N: ≈ 90 N → servo torque ≈ 90 N × 25 mm ≈ 2.3 N·m (23 kg·cm)
- a 60 kg·cm class servo gives ≈ 2.6× margin at T = 3 N. Above that the firmware's over-tension
  checks (and the servo's own torque ceiling) limit the event; the hook-set is not designed to win a
  tug-of-war, and `hard_max_tension_n` (20 N) aborts any state.

### 2.4 Manual release / handling
- Quick-release pin on the pushrod: pull to decouple; the cradle then pivots freely between the stops.
- Disarm (key switch) before any manual handling of rod or line. The classifier has a
  manual-handling rejection rule, but the procedure must not depend on it (simulation shows it is a
  heuristic, see tests/reports).

---

## 3. Subassembly B — line-tension sensor guide (load-cell lever)

### 3.1 Principle and force path
Three rollers clamped to the blank ~30–40 cm above the reel seat (above the reel, below the first
stripper guide). The two outer rollers are fixed; the centre roller sits on a lever that bears on a
bar load cell. The line is deflected by a **fixed wrap angle θ ≈ 20°** set by the roller geometry, so

    F_cell = 2 · T · sin(θ/2) · (a / b)          (a: roller-to-pivot, b: cell-to-pivot)

With θ = 20° and a/b = 1: F_cell ≈ 0.347 · T. At T = 20 N (hard max): F_cell ≈ 6.9 N (0.71 kgf).

Force path: line → centre roller (bearing) → lever → load cell (single defined contact point, ball
nose on a hardened pad) → sensor body → blank clamp → blank. The outer rollers carry the reaction
into the same body, so **no actuator load passes through the sensor**.

Why not tip-mounted: a tip-top load cell measures closest to the bite but adds mass at the tip and
needs a cable along the blank; the guide position keeps electronics near the reel. The trade-off is
guide friction above the sensor (capstan effect when the rod bends), which reduces the measured
tension during strong bends. Both positions are candidates; the guide position is the first build.

### 3.2 Parts (candidates)
- Load cell: 1 kg straight-bar (TAL221-class or equivalent), IP-rated or potted after assembly.
- Overload stop: a set-screw 0.1–0.2 mm under the lever limits cell deflection to ≈ 150 % of
  full scale (protects the cell during snags / hand pulls).
- Rollers: Ø10 mm stainless or hard-anodised aluminium with a polished U-groove (radius ≥ 1 mm),
  on stainless miniature bearings (MR63ZZ / 683ZZ class), side flanges taller than the groove depth.
- Line retainer: a snap-closing gate over each roller so the line cannot jump out under slack;
  gate clearance smaller than the line diameter for the line used (braid needs the tightest gate).

### 3.3 Friction, wear and entanglement
- **Friction:** bearing friction adds a small, mostly constant offset plus hysteresis; quantify with the
  calibration up/down sweep (docs/calibration.md). Wet operation increases it; recalibrate wet.
- **Wear:** braid abrades soft aluminium grooves; use hard-anodised or stainless rollers and inspect
  grooves for burrs every session. Mono is gentler but stretches, which slows rise times.
- **Line damage:** groove radius and polished finish keep bend radius above the line maker's limit.
- **Entanglement:** wind knots and slack loops can wrap a roller axle — use shrouded axles, flanged
  rollers, retainer gates, and keep the sensor body free of hooks/edges. Snag test is part of the
  bench plan.
- **Casting:** the line runs through the rollers during casting; added friction reduces cast
  distance. Measure; if unacceptable, add an openable gate (sensor reads invalid while open → firmware
  must treat it as a sensor fault).
- **Temperature / creep:** load-cell zero drifts with temperature; baseline EMA absorbs slow drift, but
  calibration must record temperature and a zero check is required before arming.

---

## 4. Subassembly C — retrieval-assist drive (drag preserved)

### 4.1 Principle
A 12 V gearmotor turns the **reel handle** through a coupler. In spinning and most baitcasting reels the
drag washers sit between the spool and the gear train, so torque applied at the handle reaches the
spool only up to the drag setting: if the fish pulls harder, the drag slips exactly as when a person
cranks. The assist therefore never bypasses the drag.

### 4.2 Drive chain and limits
motor (12 V, ~50:1, quadrature encoder) → **adjustable slip clutch** (friction disc, set below the reel
gear-train rating and above normal winding torque) → **dog clutch / handle coupler** (engage lever;
disengaged = handle free for the angler) → reel handle → reel gears → drag → spool.

| Limit | How | Config name |
|---|---|---|
| Pause winding when line tension is high (let the drag work) | firmware, from load cell | `assist_pause_n` (6 N, Python engine) |
| Abort retrieval on sustained over-tension | firmware safety controller | `retrieval_force_limit_n` (8 N), `over_tension_samples` (2) |
| Absolute tension limit, any state | firmware safety controller | `hard_max_tension_n` (20 N) |
| Motor current regulation | DRV8874 current chopping (set by IPROPI resistor) | hardware |
| Stall detection | current ≥ 1.8 A with < 5 mm line progress in 200 ms | `stall_current_a`, `stall_window_ms`, `stall_min_progress_m` |
| Over-current trip independent of firmware | analogue comparator → safety supervisor | hardware |
| Mechanical torque ceiling | slip clutch | hardware |

### 4.3 Mounting
Motor bracket clamps to the rod-holder frame (not the rod), aligned with the handle axis; the coupler
tolerates ±2 mm misalignment (Oldham-type or flexible jaw coupling). The rod can still be lifted out
after the dog clutch is disengaged.

---

## 5. Waterproofing — IP67 TARGET (not verified)

IP67 = dust-tight and 30 min at 1 m immersion (IEC 60529). It is a target for the enclosures and
connectors, to be **verified by test**, not a claim.

- Electronics box and battery box: IP67-rated polycarbonate/ABS enclosures with gasketed lids,
  stainless screws, a **pressure-equalisation vent membrane** (ePTFE) to stop pumping of humid air.
- Cable entries: IP68 cable glands sized to each cable; drip loops; no cable enters from the top.
- Connectors: IP67/IP68 circular connectors (M8/M12 class) for load cell, servo, motor and e-stop,
  so modules can be replaced without opening boxes.
- PCBs: conformal coating (acrylic or silicone), excluding connectors and the HX711 input pins
  until the bench validation is done (coating can shift high-impedance inputs).
- Load cell: potted strain-gauge area + sealed cable exit; the roller bearings are stainless and
  rinse-able (not sealed — they will see water).
- Servo: use a model rated IP67 by its maker; still shield it from direct spray.
- Gearmotor: typically **not** waterproof — enclose in a sealed housing with an output-shaft lip seal
  or O-ring; this is the weakest point for IP67.
- Battery: Li-ion pack with BMS inside its own sealed box, main fuse at the pack, no charging in the field.
- Verification (roadmap): 30 min @ 1 m immersion of each box (unpowered first), then powered spray
  test, then inspection for ingress; salt-water exposure only after fresh-water results.

---

## 6. Open mechanical questions (to resolve on the bench)
1. Does an 11.5° / ~0.36 m tip sweep at ~1.8 m/s set a hook through line stretch and rod flex?
2. Sensor guide friction vs rod bend: how much tension is lost above the sensor at 30°/60°/90° bend?
3. Load-cell temperature drift over 5–35 °C and the zero-check procedure needed.
4. Slip-clutch setting repeatability, and whether the reel handle coupler survives back-driving.
5. Casting-distance penalty of the roller guide.
6. Real IP rating of the motor housing.
