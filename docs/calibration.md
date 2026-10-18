# Calibration procedure (draft — not yet performed)

Nothing has been calibrated. Records produced today carry `calibration_version: "sim-identity-v0"`
(Python engine) or similar SIMULATION identifiers. This document defines what a real calibration must
produce and record before any bench result is meaningful.

## 1. What is calibrated

| Quantity | Model | Recorded in event | Source |
|---|---|---|---|
| Line tension | `T = scale_n_per_count · (counts − offset_counts)` through the actual roller geometry | `calibration.scale_n_per_count`, `offset_counts`, `calibration_version`, `calibrated_utc` | weights over the real line path |
| Wrap-angle / lever ratio | absorbed into `scale_n_per_count` (calibrate in place, not from formula) | — | same |
| Motor current | IPROPI volts → amps (`A_IPROPI` gain × R_IPROPI) | (planned: interlock limits note) | bench supply + meter |
| Bus voltage | divider ratio + ADC eFuse Vref | interlocks.battery_v | bench supply + meter |
| Pivot angle → pushrod mm | AS5600 counts at home, 12 mm, 15 mm, hard stop | feedback.position | gauge blocks / calipers |
| Line progress | encoder counts → metres of line (reel ratio × spool diameter, changes with line on spool) | feedback.position (m) | measured line length |

## 2. Tension calibration steps (bench)
1. Mount the sensor guide on a rod section fixed to the bench; route the line exactly as in use
   (same wrap angle, same line type). Record line type/diameter, temperature, humidity.
2. Warm-up 15 min powered. Zero: no load, average 10 s → `offset_counts`.
3. **Up/down sweep** with certified weights hanging from the line over a low-friction pulley:
   0, 0.5, 1, 2, 3, 5, 8, 10, 15, 20 N and back down. Hold 10 s each; average the last 5 s.
4. Fit a least-squares line `counts → N`; report slope, residuals, max nonlinearity (% FS), hysteresis
   (max up/down difference) and repeatability (3 sweeps).
5. Repeat at least at 10 °C and 30 °C (or in a cooler/warm box) to estimate zero and span drift.
6. Wet test: repeat steps 3–4 with wet line and rollers.
7. Write a calibration record (JSON, versioned) and bump `calibration_version`; the firmware embeds the
   version in every event. Store raw sweep data with sha256 so the calibration itself is auditable.

Acceptance TARGETS (provisional, to be revisited after first data): nonlinearity ≤ 1 % FS,
hysteresis ≤ 2 % FS, zero drift ≤ 0.05 N over 10 °C, error vs reference ≤ ±0.1 N or ±3 % (whichever
larger) in 0.5–20 N.

## 3. Reference measurement (for the tension-error metric)
A digital force gauge in series with the line (or a reference load cell on the pulley rig) logged in
parallel. The metric is `tension error = HookPilot − reference` over static steps and dynamic pulls
(see testing.md).

## 4. Pre-session check (every session, before arming)
- Zero check: unloaded reading within ±0.05 N of the stored zero, else re-zero and log it.
- Span check: one known weight (e.g. 2 N) within ±3 %.
- Sensor health: no `stuck` (bit-identical readings) and no out-of-range.
- Both results are written as a "calibration check" note in the session log and referenced by
  `calibration.calibration_version`.

## 5. From calibration to classifier thresholds
Thresholds (`trigger_delta_n`, `min_rate_n_per_s`, `manual_handling_delta_n`, …) must be derived from
**calibrated** bench data and **labeled** recordings (bites confirmed by an observer, plus labeled
negatives: wind, current, vibration, handling). Until then every threshold stays marked
"provisional – derive from calibration + labeled data". The threshold sweep in
`tests/reports/simulation_report.md` only shows sensitivity on synthetic data.
