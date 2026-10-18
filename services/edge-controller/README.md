# edge-controller (Python)

Host-side bridge: reads firmware JSON lines (e.g. `make sim` / serial) from stdin, validates and
verifies each event record (schema, hash, chain), appends it verbatim to an evidence store, keeps
rejected records in `<store>.rejected.jsonl`, counts telemetry/text lines, and optionally forwards to
the `MockKonnexAdapter` (SIMULATION) and builds + verifies a bundle at EOF.

```bash
./firmware/build/hookpilot_sim --scenario bite --seed 42 --quiet | \
  python -m edge_controller --store ./evidence/events.jsonl \
      --mock-konnex ./.mock-konnex --device-id hookpilot-sim-001 --firmware 0.1.0-dev \
      --scenario bite --bundle ./evidence/powp.zip
```
Options: `--seal` seals unsealed records on the host (marks `integrity.sealed_by`; the host then is the
attesting party), `--raw-dir` resolves `raw_data_ref`, `--echo` prints non-record lines to stderr.
A firmware restart (new chain segment with prev = null) is logged, not rejected.
