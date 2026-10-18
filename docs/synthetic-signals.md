# Synthetic signals — `hookpilot-synth-v1` (SIMULATION)

Normative description of the generator in `services/evidence-service/evidence_service/synth.py`,
ported bit-for-bit (to 4 decimals as recorded) by `firmware/src/core/scenarios.cpp` and, since
2026-10-18, by the web demo (`apps/web-demo/src/engine/signals.ts`, replacing its former
`hookpilot-web-synth-v1`). `apps/web-demo/tests/cross_impl.test.ts` checks the TS port against Python
fixtures (`tools/gen_web_fixtures.py`): |diff| ≤ 1e-9 required, 5.6e-17 observed.

Every simulation record names its generator in `source.generator` (firmware, Python and web demo).
Replay uses it; records without the field are assumed to be `hookpilot-synth-v1`.

## PRNG
mulberry32 (contract), seeded with the integer `seed` (masked to 32 bits). Helpers:
- `uniform(lo, hi) = lo + (hi - lo) * next()` — one draw
- `gauss()` = Box–Muller cosine branch, no caching: `u1 = next(); u2 = next(); u1 = max(u1, 1e-12);
  sqrt(-2 ln u1) * cos(2π u2)` — two draws

## Grid
50 Hz, dt = 20 ms, default window 8000 ms → n = 400, t_i = 20·i. A longer window only appends samples.

## Draw order (normative)
1. `base = uniform(0.35, 0.50)` N; `onset = 20·floor(uniform(1500, 3000)/20)` ms
2. scenario parameters, in this order:

| scenario | draws |
|---|---|
| idle | — |
| bite | amp 0.7–2.6 N, rise 60–200 ms, hold 150–1500 ms, shake_hz 1.5–3, shake_amp 0.03–0.10 N, shake_phase 0–2π, fall 150–400 ms |
| noise_current | amp 0.9–1.7 N, ramp 2500–4000 ms |
| rod_vibration | freq 6–12 Hz, amp 0.9–1.5 N, dur 1000–2000 ms |
| manual_handling | amp 6.5–12 N, rise 300–800 ms, hold 1000–3000 ms (fall fixed 300 ms) |
| sensor_failure | mode = `stuck` if next() < 0.5 else `out_of_range`; fault_dur 200–1000 ms |
| excessive_tension | amp 1.2–2.2 N, rise 60–200 ms, pre 600–1000 ms, over_rate 8–16 N/s (cap 15 N) |
| motor_stall, comm_loss | amp 1.2–2.2, rise 60–200, hold 2000–3000, shake_hz, shake_amp, shake_phase (as bite), fall 300 fixed, fault_after_retrieval_start 200–800 ms |

3. noise: `n_i = 0.02 · gauss()` for **every** sample i (always drawn, 2·n draws)
4. compose `x_i = base + shape(t_i − onset) + n_i` (shapes: linear rise/hold/linear fall; bite hold adds
   `shake_amp·sin(2π·shake_hz·τ/1000 + phase)`; vibration = `amp·env·sin(2π f τ/1000)` with 100 ms
   linear fade in/out; drift = linear ramp then hold; excessive = bite then linear rise to the cap)
5. sensor_failure: `stuck` freezes x at the onset sample to the end; `out_of_range` sets x = 49.99 N for
   `fault_dur` from onset.

`motor_stall` / `comm_loss` faults are injected by the actuation model `fault_after_retrieval_start_ms`
after retrieval begins (they are not in the tension signal).

## Ground truth
`source.ground_truth_label` per scenario (bite, no_bite, no_bite_current_drift, …). Positives: bite,
excessive_tension, motor_stall, comm_loss. Negatives: idle, noise_current, rod_vibration,
manual_handling, sensor_failure.

## Honest limits
The distributions are invented to exercise every rule; they are not fitted to real bites. Synthetic
negatives are much more separable than real water (no waves, no wind gusts, no weed, no small fish
nibbling), so the 0 % false-trigger result in tests/reports is a property of this set, not of the
classifier in the field.
