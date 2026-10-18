# Roadmap

Gates are pass/fail on the TARGETS in testing.md; a phase does not start until the previous gate passes.

| Phase | Scope | Exit gate | Konnex milestone |
|---|---|---|---|
| 0 · Software prototype (done, 2026-10) | schema, firmware core + host sim, web demo, evidence service, mock adapter, 125 tests, synthetic report | cross-implementation agreement (32/32 firmware records), evidence completeness 100 % in sim | proposed task schema + local verifier; Builders application |
| 1 · Bench prototype (sensor only) | build sensor guide + ESP32/HX711; calibration sweeps; noise/drift; labeled pull library on a linear stage with reference gauge; records in `bench` mode | tension error ≤ ±0.1 N / ±3 %; recall ≥ 90 % and false trigger ≤ 1 % on labeled bench pulls; thresholds re-derived from data | try SDK (if obtainable): `robot record/prove` with bench bundles in a sandbox; learn official bundle layout |
| 2 · Controlled load rig (actuators, no hook) | rod-holder pivot + servo; retrieval drive with programmable drag/snag; safety supervisor + hard-wired chain; fault injection for every fault-table row; e-stop timing | e-stop < 100 ms; 100 % fault-row coverage; retrieval-force compliance ≥ 99 %; no simultaneous actuation | secure element signing of record hashes (`integrity.signature`); publish device keys |
| 3 · Pond tests with observer | attended sessions, barbless hooks, independent observer labels bites and sets `fish_landed`; video with hashes; local regulations and welfare rules | ≥ N labeled sessions (N set after phase 1 variance); no safety incident; observer-confirmed metrics | independent second-party verification of bundles; propose "Sensor fusion & PoPW validation" workload or own subnet |
| 4 · Field | weatherproofing verified (IP67 immersion tests), longer sessions, multiple anglers/devices | field recall / false-trigger with CIs; IP test report | testnet task submissions with real bundles if a subnet accepts them |

Cross-team items before phase 1: unify the two classifier variants and the two synthetic generators;
make firmware record `continuous_above_ms`; adopt a pin map (hardware/wiring); decide single source of
truth for thresholds.
