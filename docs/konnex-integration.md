# Konnex integration

Source of truth for what Konnex documents: `docs/konnex-docs-findings.md` (fetched 2026-10-18 from
docs.konnex.world). This page maps HookPilot onto those documented concepts and is explicit about what
is verified, what is unverified, and what we propose.

**Bottom line:** HookPilot has **no working connection to Konnex**. The only implemented path is
`MockKonnexAdapter` — a local, file-backed SIMULATION. `KonnexCliAdapter` mirrors the documented CLI
shapes but is disabled by default and was never executed against Konnex (the SDK repository returned
HTTP 404 from our environment on 2026-10-18).

## 1. Verified (as documented, not executed by us)
- CLI shapes: `konnex robot attach --robot-id <id> --subnet <subnet> --keys ./keys.json`,
  `konnex robot record --job-id <JOBID> --sensors camera,imu,torque,thermal --out ./powp.zip`,
  `konnex prove --job-id <JOBID> --bundle ./powp.zip`, `konnex task submit --subnet … --prompt …
  --reward-usd1 … --stake-usd1 … --deadline …`. The validators page calls these commands "illustrative".
- Python client names: `KonnexClient`, `client.tasks.submit(...)`, `client.miners.register(...)`,
  `client.validators.verify(job_id, bundle_path)`.
- HTTP paths: `POST /api/v1/tasks`, `POST /api/v1/miners/models`, `POST /api/v1/validators/verify`;
  base URL and auth "match your deployment" (not published).
- PoPW binds: the signed/hashed task instruction, the policy execution trace, a sensor bundle
  (hardware-signed via TEE/secure element where assurance applies), and independent validator scoring.
- Testnet subnets listed: drone navigation, roboarm VLA, SLAM 3D map. The Builders page offers
  "Propose your own subnet" and lists "Sensor fusion & PoPW validation".

## 2. Unverified / open
- Base URL, auth scheme, `keys.json` format, PoPW bundle (`powp.zip`) internal layout.
- Whether `--sensors` accepts custom channels such as `tension`, `motor_current`.
- Task schema for non-VLA, deterministic-controller workloads (no `policy.wasm`).
- Secure-element signing requirements and supported hardware.
- No subnet accepts fishing / tension-sensor tasks today → HookPilot would be a proposed subnet or fit
  "Sensor fusion & PoPW validation".

## 3. Proposed task: `detect_bite_and_execute_hook_set` (our schema, not Konnex's)
Implemented in `services/konnex-adapter/konnex_adapter/task.py` (`TaskSpec`, version string
"hookpilot-task-0.1 (proposed, not a Konnex schema)").

**Inputs**
| Field | Meaning |
|---|---|
| `task_id` | unique task identifier |
| `device` | device identity: `device_id`, `firmware_version`, `hardware_revision`, `public_key` (null — no secure element) |
| `firmware_version` | duplicated top-level for policy matching |
| `test_scenario` (+ `seed`, `mode`) | scenario to run; seed makes SIMULATION runs reproducible |
| `allowed_trigger_params` | ranges the recorded classifier thresholds must lie in (e.g. `trigger_delta_n` 0.3–3.0 N) |
| `safety_constraints` | `retrieval_force_limit_n`, `hard_max_tension_n`, `over_tension_samples`, `hook_set_travel_limit_mm`, stall limits, `assist_pause_n` (optional; no engine implements an assist pause since 2026-10-18), require-armed, no-simultaneous-actuation, safe-stop-on-fault |
| `required_evidence_fields` | dotted paths that must be present and non-null in every record |

**Outputs** (`task_result_from_record`)
`detection_result`, `sensor_data_ref` (raw ref + sha256), `rationale` (reason + rule trace),
`actuator_telemetry` (requests, peak current/tension, max hook travel, simulated flag),
`safety_status` (decision, armed, faults), `integrity_metadata` (record hash, prev, chain head, bundle
hash, signature = null + note), `final_status` (final state, stop reason, milestones incl.
`fish_landed`, test outcome).

## 4. PoPW mapping (proposal)

| PoPW element (documented) | HookPilot artefact | Gap |
|---|---|---|
| Task instruction, signed and hashed | `task.json` in the bundle; `task_sha256` in the mock job | not signed; not on-chain |
| Policy execution trace | per-event `classification.rule_trace`, `thresholds`, `safety.interlocks.log`, `actuation.requested/feedback`; deterministic replay reproduces the decision | no `policy.wasm`; our "policy" is a deterministic controller identified by `classifier` + `implementation` + firmware version |
| Sensor bundle, hardware-signed | `events/events.jsonl` + `raw/*.csv` with sha256 in records and manifest; hash chain | `integrity.signature = null`: no TEE/secure element yet |
| Independent validator scoring | `verify_evidence` / `verify_bundle`: 11 explicit criteria (C1–C11) | it is *our* local scorer, not a Konnex validator; no independent party |

### Bundle layout (our own)
`hookpilot-evidence-bundle-v0`: `manifest.json` (file list with sha256, chain head, labels,
manifest_sha256), `task.json`, `events/events.jsonl`, `raw/<event_id>.csv`, `LAYOUT.txt`. Named
"powp-style" only because the documented CLI writes `powp.zip`; the official layout is unpublished.
The zip is deterministic (fixed timestamps) so identical content gives an identical bundle hash.

### Local verification criteria
C1 schema valid · C2 hash chain intact · C3 bundle files intact · C4 raw data integrity · C5 required
fields present · C6 safety constraints respected · C7 classification replay matches · C8 fish_landed
not inferred · C9 trigger params within allowed ranges · C10 device identity matches task · C11
simulation labelled. Verdict `accept` only if every applicable criterion passes. Unit tests show each
criterion fails on a targeted tamper even when the attacker re-seals the hash chain.

## 5. Adapter usage
```bash
cd services/konnex-adapter
python -m konnex_adapter demo --scenario bite --seed 1 --runs 3 --store ./.mock-konnex   # SIMULATION
python -m konnex_adapter verify-bundle ./.mock-konnex/jobs/<job>/powp.zip
python -m konnex_adapter cli-argv          # prints documented argv; shows enabled=false
```
Real path guard: `KonnexCliAdapter` refuses unless `HOOKPILOT_ENABLE_REAL_KONNEX=1` **and** a `konnex`
binary is on PATH; default is dry-run (returns argv); `submit_task`, `verify`, and output parsing raise
`NotImplementedError("unverified …")`.

## 6. Trust limits — what a record can and cannot prove
- **A blockchain (or hash-chain) record does not prove the physical event.** It proves that a set of
  bytes existed in a given order and has not changed since sealing. Anyone who controls the device or
  the host before sealing can produce internally consistent fake records — simulation records are
  exactly that, which is why they are labelled SIMULATION.
- **Sensor trust:** a load-cell reading is a voltage interpreted through a calibration. A swapped,
  shunted or replayed sensor signal is indistinguishable in the record without hardware attestation
  (signed ADC samples from a secure element) and physical tamper evidence.
- **Calibration:** a valid record with a wrong calibration is a precise lie. Records carry
  `calibration_version`; the calibration itself (raw sweep data, sha256, date, temperature) must be
  published and checked (docs/calibration.md).
- **Device identity:** today `device_id` is a string; there is no key pair. Konnex's documented
  assurance relies on hardware signing (TEE/secure element) — a planned milestone, not present.
- **Independent observation:** outcomes such as `fish_landed` require an independent observer
  (and ideally video with its own timestamps/hashes). HookPilot never infers them from actuator data.
- **Clock:** simulated records use `host_clock_simulated`; real records must state `ntp_synced`, `gps`
  or `rtc_unsynced`, and the verifier should treat unsynced clocks as weaker evidence.
- **Replay** proves determinism of the *software* decision given the recorded data, not that the data
  reflect reality.

## 7. Konnex milestones (see roadmap.md)
1. Keep the mock and the local verifier as the reference for a proposed task schema; publish it.
2. When the SDK is obtainable: run `konnex robot record/prove` in a sandbox subnet with our bundle,
   learn the real layout, write an exporter from `hookpilot-evidence-bundle-v0` to it.
3. Ask whether custom `--sensors` channels and deterministic-controller policies are supported; if not,
   propose a "Sensor fusion & PoPW validation" workload class or own subnet.
4. Add a secure element (e.g. ATECC608-class) to sign record hashes on device; set
   `integrity.signature`; publish device public keys.
5. Independent validators: second party re-runs replay + criteria; observer attestations for outcomes.
