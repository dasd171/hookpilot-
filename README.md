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
| `firmware/` | C++17 firmware core, mocks, ESP32 driver stubs (provisional), 46 host tests |
| `services/evidence-service/` | Schema/hash-chain verification, deterministic replay, CSV export |
| `services/konnex-adapter/` | Mock Konnex adapter, evidence bundle builder, 11-criteria verifier, disabled CLI skeleton |
| `services/edge-controller/` | Bridge: firmware JSON lines -> evidence store -> mock adapter |
| `packages/event-schema/` | Event JSON Schema 1.0.0, examples, validator |
| `packages/shared-types/` | TS types |
| `hardware/` | Block diagram, mechanical spec, BOM (estimates), provisional wiring |
| `tests/` | Python unit / integration / simulation tests + generated reports |
| `docs/` | architecture, hardware-design, calibration, safety, testing, konnex-integration, builders-application, demo-script, roadmap |

## Quick start
```bash
# Web demo
cd apps/web-demo && npm install && npm test && npm run dev        # or: npm run build && npm run preview
npm run -s sim -- --scenario bite --seed 42 --summary              # headless, reproducible

# Firmware (host simulation). Needs make + a C++17 compiler (pip install ziglang works: CXX="python3 -m ziglang c++")
cd firmware && make test && make sim SCENARIO=bite SEED=42

# Python services and tests
pip install jsonschema pytest
cd tests && python -m pytest -q
python tools/gen_report.py --seeds 300 --sweep-seeds 150          # regenerates tests/reports/
```
A single-file build of the demo is at `apps/web-demo/hookpilot-demo-standalone.html` (open directly in a browser).

## Results actually produced (synthetic data only, 2026-10-18)
- Tests: web 68/68, firmware 46/46 (413 checks), Python 150 passed / 1 slow test skipped.
- Cross-implementation: firmware C++, Python and the web demo's TypeScript share one generator
  (`hookpilot-synth-v1`) and one classifier rule set with the firmware defaults. The TS generator matches Python
  within 5.6e-17 N (10,779/10,800 fixture samples bit-identical). TS and Python agree on result, reason and
  rule-trace text for every committed fixture (360 classifier windows, 320 engine runs). Web-demo records
  replay from (scenario, seed) in Python (`signal_match`), and so do the firmware's.
- Simulation report (`tests/reports/simulation_report.md`): bite recall 85.7% (257/300) at the provisional
  1.0 N trigger; 0/1500 false triggers on synthetic non-bite scenarios; all safety scenarios ended in SAFE_STOP.
  These numbers only show the rules behave as designed on synthetic signals — they say nothing about real water.

## Known gaps (honest)
- Rule traces are not byte-identical in all three yet. Python and the web demo start a candidate's trace with a
  `t=… TRIGGER …` line. Firmware writes no such line but appends `interrupted by safety: …` / `state=… interrupted`
  lines when SAFE_STOP cuts in. For a sensor fault while monitoring, firmware logs its own wording.
  The rule lines (window, R0–R5) are identical. Result, reason and features match.
- The actuation and safety models still differ between implementations, and so do record layouts outside
  classification. Examples: the web demo adds simulated line load during actuation, firmware rounds samples to 4
  decimals, and dwell times differ. Only signal + classification are cross-checked.
- The web demo's first record per scenario is compared. Later candidates in the same run (after re-arm) are
  firmware-style behaviour that the Python reference engine, which makes one decision per window, does not model.
- ESP32 drivers never compiled with a real toolchain; pinout provisional; no secure-element signing.
- Konnex SDK repo (github.com/konnex-world/konnex) unreachable on 2026-10-18 — adapter is a labeled mock. See
  `docs/konnex-docs-findings.md` and `docs/konnex-integration.md`.

See `docs/roadmap.md` for the path from bench prototype to field tests.
