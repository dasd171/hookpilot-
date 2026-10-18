# Konnex Builders application — draft answers (English)

Form: https://subnets.testnet.konnex.world/builders · Draft prepared 2026-10-18 · Replace `https://raw.githack.com/dasd171/hookpilot-/main/apps/web-demo/hosted/index.html`
before submitting. All claims below are limited to what exists in this repository.

---

**Project / team name:** HookPilot

**Your name:** takeshi Daddy

**Role:** Founder / embedded & full-stack developer

**Email:** chengw372@gmail.com

**X (Twitter):** @daddyosek

**GitHub:** https://github.com/dasd171

**One-line pitch (≤ 140 chars):**
> Smart fishing rod controller: explainable bite detection, safety-gated hook-set, and replayable, hash-chained evidence for PoPW.

(128 characters)

**Category:** Sensor fusion & PoPW validation
*(Alternative: propose our own subnet for low-cost physical-actuation tasks with deterministic
controllers.)*

**Stage:** Prototype — software prototype and simulation. Hardware is designed (block diagram,
mechanical spec, BOM, provisional wiring) but **not yet built**; no field or live-fish tests.

**Demo link:** https://raw.githack.com/dasd171/hookpilot-/main/apps/web-demo/hosted/index.html

**Team:** Solo founder.

**Project description (short):**
HookPilot detects fish bites from line tension, sets the hook with a travel-limited rod-holder pivot,
and assists retrieval through the reel handle so the reel's drag stays in control. A rule-based
classifier (deterministic-baseline-v1) explains every decision with a rule trace; it can only request
actuation, and an independent safety controller permits or denies. Every candidate event becomes a
JSON record (shared schema across firmware C++, web TypeScript and Python services) with calibration
version, samples or raw-file hash, features, decision, safety interlocks, actuator telemetry and a
sha256 hash chain. "Fish landed" is never inferred — only an independent observer can set it.

**What is built today (verifiable in the repo):**
- C++ firmware core with host simulation, a React/TS simulation demo, and Python services for
  evidence verification, deterministic replay, an edge bridge and a mock Konnex adapter.
- Reproducible synthetic scenarios (mulberry32, identical in Python, C++ and TS): bites, current drift,
  rod vibration, manual handling, sensor failure, over-tension, motor stall, comm loss.
- Cross-language agreement: one engine specification (state machine, safety controller, actuator models,
  fault injection) implemented in C++, Python and TS. On 636 shared simulation cases (scenarios, fault
  injections, parameter overrides) the firmware sim, the Python engine and the web demo produce the same
  tick-by-tick states, faults, actuator commands and telemetry (636/636 runs each pair, within 1e-9).
  Record hashes from the TS demo and the C++ firmware sim verify in Python, and replay reproduces them.
- Automated tests passing on 2026-10-18: 187 Python (1 opt-in slow test skipped), 68 web, 53 firmware
  host tests. Simulation metrics are generated from actual runs: on our synthetic set
  the provisional thresholds give 85.7 % bite recall (257/300) and 0/1500 false triggers. These are
  synthetic numbers that only show the rules behave as designed, not field performance.

**How it relates to Konnex / PoPW:**
We propose a task type `detect_bite_and_execute_hook_set` (inputs: device identity, firmware version,
scenario, allowed trigger parameters, safety constraints, required evidence fields; outputs: detection
result, sensor-data reference, rationale, actuator telemetry, safety status, integrity metadata, final
status). Our evidence bundle maps onto PoPW's task instruction / execution trace / sensor bundle /
validator scoring, and a local verifier scores 11 explicit criteria (schema, hash chain, raw-data
hashes, required fields, safety constraints, deterministic replay, no inferred outcomes, parameter
ranges, device identity, labelling). We have **not** integrated with the Konnex SDK yet — the SDK
repository was not reachable from our environment — so the adapter is a clearly labelled local mock,
and the real CLI adapter is a disabled skeleton mirroring only documented commands.

**Honest limits:** no hardware built; no secure element (records are hash-chained, not hardware-signed);
thresholds are provisional; a hash chain does not prove that a physical event happened — sensor trust,
calibration, device identity and independent observation are required, and are on our roadmap.

**Next 3 months (milestones):**
1. Bench sensor rig: calibrated tension sensing vs a reference gauge; labeled pull library.
2. Controlled load rig with actuators: hook-set travel/timing, retrieval with drag/snags, full
   fault-injection and e-stop timing.
3. Konnex: run record/prove against a testnet subnet once the SDK is available; adapt our bundle to the
   official layout; prototype on-device signing with a secure element.
Pond tests with an independent observer only after the bench and rig targets are met.

**Optional grant ask:** Spark tier — to fund the bench prototype parts and test equipment
(bill of materials estimated at roughly USD 665 per prototype unit plus about USD 240 of test
equipment; estimates, not quotes) and the secure-element signing work. We are early; we'd use the
grant to turn the simulation into measured bench data and an end-to-end testnet submission.

**Anything else:**
Everything simulated is labelled SIMULATION, and reports are regenerated from runs
(`python tools/gen_report.py`). We are happy to share the event schema and the local verifier as a
starting point for a sensor-fusion PoPW validation workload.
