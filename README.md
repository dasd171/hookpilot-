# HookPilot — smart fishing bite detection, safety-gated hook-set, assisted retrieval

> **Status: software prototype + simulation.** No hardware has been built. All measurements in this repo are
> **synthetic** and labeled SIMULATION. Thresholds are provisional placeholders. "Fish landed" is never inferred.

HookPilot monitors fishing-line tension, classifies candidate bites with an explainable deterministic
classifier (`deterministic-baseline-v1`), and — only if an **independent safety controller** permits —
performs a travel-limited hook-set followed by force-limited assisted retrieval. Every event becomes a
schema-validated, sha256 hash-chained evidence record that reviewers can replay. A Konnex adapter maps
these records onto Proof-of-Physical-Work concepts (local mock; official SDK not yet reachable).

**Live demo (simulation, synthetic data):** https://raw.githack.com/dasd171/hookpilot-/main/apps/web-demo/hookpilot-demo-standalone.html

## Repository map
| Path | What |
|---|---|
| `apps/web-demo/` | React + TS + Vite interactive simulation demo (63 vitest tests) |
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
- Tests: web 63/63, firmware 46/46 (413 checks), Python 125 passed / 1 slow test skipped.
- Simulation report (`tests/reports/simulation_report.md`): bite recall 85.7% (257/300) at the provisional
  1.0 N trigger; 0/1500 false triggers on synthetic non-bite scenarios; all safety scenarios ended in SAFE_STOP.
  These numbers only show the rules behave as designed on synthetic signals — they say nothing about real water.

## Known gaps (honest)
- Web demo uses its own synthetic generator (`hookpilot-web-synth-v1`) and thresholds tuned to it; firmware and
  Python share another generator and firmware thresholds. Aligning them is the next task.
- ESP32 drivers never compiled with a real toolchain; pinout provisional; no secure-element signing.
- Konnex SDK repo (github.com/konnex-world/konnex) unreachable on 2026-10-18 — adapter is a labeled mock. See
  `docs/konnex-docs-findings.md` and `docs/konnex-integration.md`.

See `docs/roadmap.md` for the path from bench prototype to field tests.
