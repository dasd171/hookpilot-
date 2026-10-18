# Live demo script (≈ 4 minutes)

Everything shown is **SIMULATION**; say so at the start and whenever a number appears.
Prep: terminal at repo root, `pip install jsonschema pytest`, web demo running at https://raw.githack.com/dasd171/hookpilot-/main/apps/web-demo/hookpilot-demo-standalone.html (or
`npm run dev` in apps/web-demo), firmware host sim built (`make -C firmware sim` target available).

## 0:00 – 0:30 · What and why
"HookPilot detects a bite from line tension, sets the hook with a travel-limited pivot and assists
retrieval through the reel handle, so the drag stays in control. Every decision is explainable and
leaves replayable, hash-chained evidence. Today this is software and simulation; the hardware is
designed, not built."

## 0:30 – 1:30 · Web demo (SIMULATION banner visible)
1. Show live tension chart, trigger threshold, state machine (IDLE → MONITOR …).
2. Run **bite** → CANDIDATE → CONFIRM → HOOK_SET → RETRIEVE → VERIFY. Open the event record: rule
   trace, thresholds marked provisional, `fish_landed: "unknown"`.
3. Run **rod vibration** → rejected_vibration (oscillation count), no actuation.
4. Run **excessive tension** → SAFE_STOP (OVER_TENSION). Point out: the classifier only requested;
   the safety controller stopped it; reset returns to IDLE disarmed.

## 1:30 – 2:30 · Same logic in firmware, verified by an independent tool
```bash
./firmware/build/hookpilot_sim --scenario motor_stall --seed 42 --quiet > /tmp/fw.jsonl
cd services/evidence-service
python -m evidence_service verify /tmp/fw.jsonl --replay
```
"The C++ firmware produced this record; the Python tool recomputes the hash, regenerates the signal
from scenario + seed, re-runs the classifier and gets the same decision and rule trace."
Then tamper: `sed 's/"MOTOR_STALL"/"NONE"/' /tmp/fw.jsonl > /tmp/bad.jsonl` and verify it → `hash:FAIL`.

## 2:30 – 3:30 · Konnex-style flow (mock, clearly labelled)
```bash
cd ../konnex-adapter
python -m konnex_adapter demo --scenario excessive_tension --seed 4 --runs 3 --store /tmp/mk | head -40
python -m konnex_adapter verify-bundle /tmp/mk/jobs/*/powp.zip
```
"Proposed task `detect_bite_and_execute_hook_set`; bundle with manifest, events, raw data; 11 explicit
criteria — schema, chain, raw hashes, safety limits, replay, no inferred catch. This is a local mock:
the Konnex SDK was not reachable, and the real CLI adapter is disabled until we can test it."
Read the trust note aloud: "a passing score does not prove the physical event happened."

## 3:30 – 4:00 · Numbers and next steps
Open `tests/reports/simulation_report.md`: "On our synthetic set, provisional thresholds: 85.7 %
bite recall, 0 of 1500 false triggers, every safety scenario ended in SAFE_STOP. Synthetic, so it only
shows the logic is consistent. Next: bench sensor rig against a reference gauge, then a controlled
load rig with fault injection, then pond tests with an observer, and on-device signing."

## Fallbacks
- Web demo down → show `python -m evidence_service generate --scenario bite,excessive_tension --count 1 --out /tmp/e.jsonl` + verify.
- Firmware binary missing → use `packages/event-schema/examples/safe_stop_motor_stall.json`.
