# evidence-service (Python)

Canonical hashing, schema validation, hash-chain verification, raw-data sha256, deterministic replay,
CSV export, plus the Python reference engine (SIMULATION) and metric computation used by
`tools/gen_report.py`. Stdlib + `jsonschema` only.

```bash
python -m evidence_service verify events.jsonl [--replay] [--raw-dir DIR] [--json]
python -m evidence_service replay event.json
python -m evidence_service export events.jsonl --out events.csv
python -m evidence_service generate --scenario bite,motor_stall --seed 1 --count 5 --raw --out out/events.jsonl   # SIMULATION
python -m evidence_service hash event.json
```

| Module | Purpose |
|---|---|
| `prng.py` | mulberry32 (bit-exact vs JS reference), uniform, Box–Muller gauss |
| `synth.py` | `hookpilot-synth-v1` scenarios (docs/synthetic-signals.md) |
| `classifier.py` | deterministic-baseline-v1, port of the firmware C++ (the web demo's TS is a port of the same code) |
| `safety.py` | reference safety-controller model + limits (mirrors firmware config.h) |
| `engine.py` | (scenario, seed) → full event record; `EventLog` hash chain; raw CSV I/O |
| `canonical.py` | canonical JSON (JS number formatting), record hash, seal, file sha256 |
| `schema.py` | schema location + validation |
| `chain.py` | per-record schema/hash/chain/raw verification |
| `replay.py` | signal regeneration + classifier replay + rule consistency |
| `export.py` | CSV export |
| `simmetrics.py` | labeled scenario set, recall/false-trigger/latency/safety metrics, sweeps |

See `REVIEWER_GUIDE.md` for how a third party should use it.
