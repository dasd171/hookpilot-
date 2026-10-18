# HookPilot — smart fishing bite detection, safety-gated hook-set, assisted retrieval

> **Status: software prototype + simulation.** No hardware has been built. All measurements in this repo are
> **synthetic** and labeled SIMULATION. Thresholds are provisional placeholders. "Fish landed" is never inferred.

HookPilot monitors fishing-line tension, classifies candidate bites with an explainable deterministic
classifier (`deterministic-baseline-v1`), and — only if an **independent safety controller** permits —
performs a travel-limited hook-set followed by force-limited assisted retrieval. Every event becomes a
schema-validated, sha256 hash-chained evidence record that reviewers can replay. A Konnex adapter maps
these records onto Proof-of-Physical-Work concepts (local mock; official SDK not yet reachable).

**Live demo (simulation, synthetic data):** https://raw.githack.com/dasd171/hookpilot-/main/apps/web-demo/hosted/index.html

## Repository map
| Path | What |
|---|---|
| `apps/web-demo/` | React + TS + Vite interactive simulation demo (68 vitest tests) |
| `firmware/` | C++17 firmware core, mocks, ESP32 driver stubs (provisional), 53 host tests |
| `services/evidence-service/` | Schema/hash-chain verification, deterministic replay, CSV export |
| `services/konnex-adapter/` | Mock Konnex adapter, evidence bundle builder, 11-criteria verifier, disabled CLI skeleton |
| `services/edge-controller/` | Bridge: firmware JSON lines -> evidence store -> mock adapter |
| `packages/event-schema/` | Event JSON Schema 1.0.0, examples, validator |
| `packages/shared-types/` | TS types |
| `hardware/` | Block diagram, mechanical spec, BOM (estimates), provisional wiring |
| `tests/` | Python unit / integration / simulation tests + generated reports |
| `docs/` | architecture, engine-spec, hardware-design, calibration, safety, testing, konnex-integration, builders-application, demo-script, roadmap |

## Quick start
```bash
# Web demo
cd apps/web-demo && npm install && npm test && npm run dev        # or: npm run build && npm run preview
npm run -s sim -- --scenario bite --seed 42 --summary              # headless, reproducible

# Firmware (host simulation). Needs make + a C++17 compiler (pip install ziglang works: CXX="python3 -m ziglang c++")
cd firmware && make test && make sim SCENARIO=bite SEED=42
make sim SCENARIO=motor_stall SEED=3 TRACE=build/trace.jsonl   # + one JSON line per 20 ms tick (docs/engine-spec.md)

# Python services and tests
pip install jsonschema pytest
cd tests && python -m pytest -q
python tools/gen_report.py --seeds 300 --sweep-seeds 150          # regenerates tests/reports/
```
A single-file build of the demo is at `apps/web-demo/hookpilot-demo-standalone.html` (open directly in a browser).

## Results actually produced (synthetic data only, 2026-10-18)
- Tests: web 68/68 (vitest), firmware 53/53 (574 checks), Python 187 passed / 1 slow test skipped.
- Cross-implementation: firmware C++, Python and the web demo's TypeScript share one generator
  (`hookpilot-synth-v1`), one classifier rule set **and one engine**: state machine, safety controller,
  actuator / line-load models, fault injection and record contents (`docs/engine-spec.md`). On the 636 shared
  cases (9 scenarios × armed/disarmed × seeds 1–20, 21 fault injections × 8, 6 parameter-override sets × 18)
  the firmware `hookpilot_sim --trace` output and the Python engine agree tick for tick (255,036 trace lines,
  641 records; max |numeric diff| 1.4e-14), and so do the TS engine and Python (max |diff| 4.4e-16): same
  states, faults and the tick they latch, commands with permit/deny reasons, actuator position/current, line
  tension, outcome and stop reason. The TS generator matches Python within 5.6e-17 N.
- Simulation report (`tests/reports/simulation_report.md`): bite recall 85.7% (257/300) at the provisional
  1.0 N trigger; 0/1500 false triggers on synthetic non-bite scenarios; all safety scenarios ended in SAFE_STOP.
  These numbers only show the rules behave as designed on synthetic signals — they say nothing about real water.

## Known gaps (honest)
- Engine agreement is shown on the shared case list only (636 runs plus the unit tests). Record fields outside
  the compared core still differ by design: device id / firmware version, event id, calibration label, the
  thresholds block layout, Python's `test_outcome` / `class_match`, and therefore the record hashes.
- New engine defaults are placeholders: the 100 ms hook-to-retrieval gap, 500 ms VERIFY dwell, 2-sample
  over-tension debounce, 3-sample sensor-fault debounce, 200 ms stall window and the simulated line load /
  motor current model are all provisional, not derived from a mechanism.
- A NaN or disconnect *inside* a candidate window stops the cycle through the classifier's sensor rule before
  the 3-sample safety debounce latches a fault, so that record shows `safe_stop` with no fault code (the code
  latches 2 ticks later). Firmware behaviour, kept in all three.
- ESP32 drivers never compiled with a real toolchain; pinout provisional; no secure-element signing.
- Konnex SDK repo (github.com/konnex-world/konnex) unreachable on 2026-10-18 — adapter is a labeled mock. See
  `docs/konnex-docs-findings.md` and `docs/konnex-integration.md`.

See `docs/roadmap.md` for the path from bench prototype to field tests.
