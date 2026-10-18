# HookPilot engine specification (state machine, safety controller, actuator models)

> **SIMULATION model, shared by all three implementations.** Every numeric value below is a configurable
> **provisional placeholder** ("provisional - derive from calibration + labeled data"). None was validated on
> hardware, against a real reel, or on labeled bite data. The actuator models are kinematic placeholders, not
> physics.

Status: unified on 2026-10-18. Ground truth is the firmware C++:

| Part | Firmware (ground truth) | Python port | TypeScript port (web demo) |
|---|---|---|---|
| Defaults | `firmware/include/hookpilot/config.h` | `evidence_service/safety.py`, `classifier.py` | `apps/web-demo/src/engine/config.ts` |
| Safety controller + gate | `firmware/src/core/safety_controller.cpp` | `evidence_service/safety.py` | `src/engine/safety.ts` |
| State machine + records | `firmware/src/core/controller.cpp` | `evidence_service/engine.py` (`Controller`) | `src/engine/engine.ts` (`Controller`) |
| Actuator / sensor / comm mocks | `firmware/host/mocks.{h,cpp}` | `engine.py` (`MockHook`, `MockRetrieval`, …) | `src/engine/actuators.ts`, `engine.ts` |
| Rig + run procedure | `firmware/host/sim_harness.cpp` | `engine.py` (`Rig`, `run_scenario`) | `engine.ts` (`Rig`, `HookPilotEngine`, `runScenario`) |
| Classifier | `bite_detector.cpp`, `signal_processor.cpp` | `classifier.py` (`Detector`, `FwProcessor`) | `detector.ts` (`FwDetector`), `processor.ts` |
| Signal generator | `scenarios.cpp` | `synth.py` | `signals.ts` (see `docs/synthetic-signals.md`) |

For any (scenario, seed, armed, injections, parameter overrides) the three engines produce the same tick
trace and the same records (numbers within 1e-9; see "Cross-implementation comparison").

## 1. Time base

- One tick = 20 ms (50 Hz). The clock starts at 0 at boot; tick *i* runs at `t = 20·i` ms. All times are
  integer milliseconds since boot ("local" time of a run). Records use `start_utc = epoch + t` with the
  simulated epoch 2026-01-01T00:00:00Z (the web demo's interactive UI uses its own wall-clock epoch).
- Sample *i* of the synthetic signal is read on tick *i*. A run lasts exactly as many ticks as the signal
  has samples (default 8000 ms → 400 ticks).

## 2. Run procedure (one scenario run)

1. Build a fresh rig and `begin()` (boot): all actuators stopped, state `IDLE`, **disarmed**, signal
   processor reset. If the boot cause was a watchdog reset, latch `WATCHDOG_RESET` and log one boot record
   (`no_event`, final state `IDLE`, stop reason `watchdog_reset_boot: disarmed, awaiting operator reset`).
2. For each sample *i* (one **harness step**):
   1. `rig.step()`: advance the clock by 20 ms (not before the first tick), set the line load (§5), set the
      retrieval motor load to the previous tick's processed `x`, then `controller.tick()` (§3).
   2. If *i* = 0 and the run is armed: `requestArm()`. Arming can be refused (latched fault, active
      condition, invalid sample, bus voltage < `undervoltage_rearm_v`, e-stop, an actuator active); a refused
      arm leaves the run disarmed (observation only).
   3. If the state is `RETRIEVE` and retrieval was not seen yet: remember `retrieval_start = t`.
   4. Apply due fault injections (§6).
   5. Emit the tick-trace line (§8).
3. End of window: if an event is still open, `requestDisarm()` (it decides an undecided candidate and closes
   the event as `IDLE`, stop reason `operator_disarm`). If the run logged no record at all, log one
   **no_event summary** record (pre-roll samples, reason `No candidate in the observation window: delta
   never reached trigger_delta_n %.3f N.`, stop reason `no_event: nothing to actuate`, final state = current
   state). Emit the `end` trace line.

A run can log several records (multi-event runs): e.g. a disarmed observation followed by later candidates,
a rejected candidate followed by a re-armed one, or a watchdog boot record followed by observation records.
All implementations emit all of them, in order (Python: `run_scenario(...).records` / `build_events`;
`build_event` returns the first one).

## 3. Controller tick (state machine)

Per tick, in this order:

1. `comm.poll(t)`: the heartbeat timestamp becomes `t` when the link is up and heartbeats are not lost.
2. `gate.update(t)`: both actuator models advance (§5).
3. Read the sensor and process the sample (classifier processor: EMA baseline, frozen while a candidate or a
   cycle is open; validity: non-finite → `disconnected`, `< sensor_min_n` or `>= sensor_max_n` →
   `out_of_range`, `stuck_samples` bit-identical readings → `stuck`). Push `(t, raw, valid)` into a 64-entry
   pre-roll ring. If an event is open and has < 400 samples: append `(t, raw)` when finite (valid or not),
   otherwise count it in `invalid_samples_omitted`.
4. Gather safety inputs (sample, both actuator feedbacks after step 2, bus voltage, temperature, e-stop,
   link flag, last heartbeat) and run `safety.update()` (§4).
5. If any latched fault other than `WATCHDOG_RESET` is set and the state is not `SAFE_STOP`: enter
   `SAFE_STOP` (§3.1) and end the tick.
6. State step:

| State | Behaviour this tick |
|---|---|
| `IDLE` (disarmed) | stop all actuators; **observation**: the same monitoring step as `MONITOR` (never requests actuation). Configurable (`observe_when_disarmed`, default on). |
| `MONITOR` | if awaiting re-arm: done when `delta < trigger·release_ratio` for `rearm_quiet_ms`; else if the sample is valid and `delta >= trigger_delta_n`: freeze the baseline, open an event (pre-roll: ring samples with `t - 500 <= t' <= t`), record `trigger_t_ms`, begin the candidate → `CANDIDATE` |
| `CANDIDATE` | feed the detector; when the window is complete (600 ms incl. the trigger sample), on an invalid sample, or on `delta > manual_handling_delta_n` → `CONFIRM` **and decide on the same tick** |
| `CONFIRM` | decide (only reached transiently; dwell 0 ticks) |
| `HOOK_SET` | every 5th tick since boot record hook feedback; when the hook first reports `at_target && !active` record feedback, mark `hook_set_completed`, remember `t_done`; once `t - t_done >= hook_to_retrieval_gap_ms` request `retrieve` (permit → `RETRIEVE`; deny → `SAFE_STOP` "retrieval denied after hook set (…)") |
| `RETRIEVE` | every 5th tick record retrieval feedback; when the retrieval reports `at_target && !active`: stop it, record feedback, mark `retrieval_completed` → `VERIFY`, request hook `return_to_rest` (target `hook_set_travel_min_mm`) |
| `VERIFY` | when `t - t_enter >= verify_ms` and the hook is not active: record hook feedback, close the event (`IDLE`, `cycle_complete: …; fish_landed not inferred`), **disarm**, unfreeze the baseline, await re-arm → `IDLE` |
| `SAFE_STOP` | stop all actuators; leaves only via an explicit reset with faults cleared → `IDLE` (disarmed) |

**Decision** (`CONFIRM`): `result, reason, rule_trace, features = detector.decide()`; `decision_t_ms = t`.

- `bite_candidate_confirmed`, **disarmed**: close the event (`IDLE`, `disarmed_observation_only: no actuation
  requested`), unfreeze, await re-arm → `IDLE`. Nothing is requested.
- `bite_candidate_confirmed`, armed: request hook `stroke_to` (`hook_set_target_mm` at `hook_set_speed_mm_s`);
  permit → `HOOK_SET`; deny → close (`MONITOR`, `hook_set_denied: <reason>`) → `MONITOR`.
- `rejected_sensor_fault` → `SAFE_STOP` "safe_stop: sensor fault during candidate (<health>)". This can
  happen before the safety controller's 3-sample sensor debounce latches a fault (the record then has
  `decision: safe_stop` and no fault code yet).
- any other rejection: unfreeze, await re-arm, close (`MONITOR` if armed else `IDLE`,
  `classifier_rejected: <result>`) → same state.

Feedback entries are recorded only for **permitted** requests (and at stops), never for denied ones.
Record `safety.decision`: `safe_stop` if the event closed in `SAFE_STOP`, else `permit` if any request was
permitted, else `deny`. `safety.faults` = latched faults (∪ active conditions when closing in `SAFE_STOP`);
`safety.fault_latched_t_ms` = the tick each latched fault latched. `armed` = armed when the event opened.

### 3.1 Entering SAFE_STOP

Stop both actuators, disarm. Then:
- no open event: open one at `t` with features from the current sample; result `rejected_sensor_fault`
  ("Sensor health '<h>' while <STATE>; safety controller entered SAFE_STOP.") if the sample is invalid, else
  `no_event` ("No bite candidate; safety fault while <STATE>."); rule trace `state=<STATE> -> <why>`;
- open, undecided candidate: decide now and append `interrupted by safety: <why>`;
- open, decided: append `state=<STATE> interrupted: <why>`.

Record hook and retrieval feedback, close the event (`SAFE_STOP`, stop reason `<why>` =
`safe_stop: <FAULT,FAULT,…>`), unfreeze the baseline → `SAFE_STOP`.

### 3.2 Operator requests

- `requestArm` — only from `IDLE`; safety `arm()` checks (above); clears await-re-arm, unfreezes → `MONITOR`.
- `requestDisarm` — stop all, disarm; in `SAFE_STOP` nothing else. Otherwise an open event is decided if
  needed (`interrupted by operator_disarm`, else `state=<S> interrupted: operator_disarm`), closed as
  `IDLE`/`operator_disarm` (feedback recorded only if something was permitted), await re-arm; → `IDLE`.
- `requestReset` — only from `SAFE_STOP` or `IDLE`; `clearFaults()` keeps a latched fault only while its
  condition is still active (`WATCHDOG_RESET` is acknowledgement-only and always clears); on success stop
  all, zero the retrieval position, reset the processor → `IDLE` (disarmed).

### 3.3 Rule trace text (identical in all three)

Candidate records: `t=<t>ms TRIGGER delta=<%.3f>N >= trigger_delta_n=<%.3f>N (baseline frozen at <%.3f>N)`,
then `window: <ms> ms observed after trigger (decision_window_ms=<ms>)`, then the R0–R5 lines and the final
line (`docs/architecture.md`), then any interruption line (§3.1, §3.2). Numbers use C `printf` rounding
(`%.3f`, `%.0f`) in all three ports.

## 4. Safety controller (independent of the classifier)

`update(inputs)` evaluates the conditions below every tick (all of them, armed or not), then latches:
`latched |= active` except `COMM_LOSS`, which latches **only while armed**. Any latched fault disarms.

| Condition | Rule (per tick) | Default (provisional) |
|---|---|---|
| `E_STOP` | e-stop input active | — |
| `SENSOR_*` | `sensor_fault_samples` consecutive invalid samples; code from the last invalid health (`out_of_range` → `SENSOR_OUT_OF_RANGE`, `stuck` → `SENSOR_STUCK`, else `SENSOR_DISCONNECTED`) | 3 samples |
| `OVER_TENSION` | `over_tension_samples` consecutive **valid** samples with `raw > hard_max_tension_n`, or with retrieval active and `raw > retrieval_force_limit_n` | **2 samples** (1-sample check retired), 20 N, 8 N |
| `ACTUATOR_TIMEOUT` | hook active for `> hook_set_timeout_ms` since the first update that saw it active; same for retrieval with `retrieval_timeout_ms` | 500 ms, 4000 ms |
| `TRAVEL_LIMIT` | hook position (if valid) `> hook_set_travel_limit_mm` or `< travel_min - 1 mm`; retrieval position (if valid) `> retrieval_travel_limit_m` | 15 mm, 1.5 m |
| `MOTOR_STALL` | while retrieval is active and `t - start >= stall_grace_ms`: with the newest history sample at least `stall_window_ms` old, progress `< stall_min_progress_m` **and** (current `>= stall_current_a` or current invalid); or progress over `3·stall_window_ms` `< stall_min_progress_m` regardless of current (encoder loss). History: 64 (t, position) samples, cleared when retrieval starts | 200 ms grace, 200 ms window, 5 mm, 1.8 A |
| `INTERLOCK` | hook and retrieval both active | — |
| `COMM_LOSS` | link reports down, or `t - last_heartbeat > comm_timeout_ms` | 300 ms |
| `UNDERVOLTAGE` | bus `< undervoltage_v` continuously for `>= undervoltage_debounce_ms` | 10.5 V, 200 ms |
| `OVER_TEMP` | temperature `> over_temp_c` | 70 °C |

`WATCHDOG_RESET` is latched at boot only (never evaluated); it blocks arming until an explicit reset but is
not critical (does not cause `SAFE_STOP`).

`permit(request)` — first failing check wins (reason text identical everywhere):
`deny: not armed` · `deny: latched faults <list>` · `deny: active conditions <list>` · `deny: sensor not valid
(<health>)` · `deny: tension above hard_max_tension_n` · hook-set: `deny: mutual exclusion (retrieval
active)`, target within `[travel_min, travel_limit]`, `0 < speed <= hook_set_max_speed_mm_s` (80 mm/s) ·
retrieval: `deny: mutual exclusion (hook_set active)`, **`deny: hook_set stopped <n> ms ago (<
hook_to_retrieval_gap_ms)`**, `0 < target <= retrieval_travel_limit_m`, `0 < speed <=
retrieval_max_speed_m_s` (0.6 m/s), `deny: tension <%.3f> N above retrieval_force_limit_n` · else `permit:
all interlocks clear`. The gate re-checks mutual exclusion from live feedback before issuing a command;
`stop()` is never gated.

**Hook-to-retrieval gap** (new 2026-10-18, provisional): `hook_to_retrieval_gap_ms = 100` ms minimum dead
time between the hook-set stroke ending (first `update()` that sees the hook inactive) and the retrieval
start. The controller waits for it; the safety controller also denies a retrieval request inside the gap.
Set to 0 to restore same-tick hand-over.

**"Pause winding above X N"** (the former Python-only `assist_pause_n = 6 N`) was **removed from all engines**:
no implementation pauses winding; above `retrieval_force_limit_n` the cycle stops with `OVER_TENSION`
(the reel drag and slip clutch are mechanical, docs/hardware-design.md). The Konnex task verifier keeps an
*opt-in* `assist_pause_n` evidence check for tasks that require such a mechanism; it is off by default.

## 5. Actuator, sensor and line-load models (SIMULATION)

All feedback values below are what the controller sees after `gate.update(t)`; `dt = (t - t_prev)/1000` s
(0 on the first update).

**Hook-set servo** (position mm): `moveTo(target, speed)` → active, `moving`. Each update while active:
jammed → speed 0, current 1.6 A, `jammed`. Else `step = speed·dt`; if `step >= |target - pos|` then
`pos = target` else `pos += sign·step`; current 0.8 A; when `|pos - target| <= hook_set_position_tol_mm`
(0.5 mm): inactive, `at_target`, current 0.1 A. Inactive: current 0.05 A. `stop()`: inactive, current 0,
status `stopped` (unless `idle`). Defaults: target 12 mm, 60 mm/s → the stroke completes on the 10th update
(≈ 200 ms) at 11.999… mm.

**Retrieval motor** (position m of line): `start(target, speed)` → active, `retrieving`. Each update while
active: stalled → speed 0, current 2.5 A, `stalled`. Else `step = speed·dt`; if `pos + step >= target`:
`pos = target`, inactive, `at_target`, current 0. Else `pos += step` (unless encoder lost), current
`= retrieval_current_base_a + retrieval_current_per_n_a · max(0, x_prev)` (0.3 A + 0.1 A/N × the previous
tick's processed tension). Inactive: current 0. Position persists across cycles until a reset. Defaults:
1.0 m at 0.5 m/s → 2.0 s.

**Line load** (sensor coupling): on each step, *before* the tick, the sensor reading gets an additive load
of `hook_set_line_load_n` (1.5 N) if the hook was active at the end of the previous tick plus
`retrieval_line_load_n` (0.6 N) if the retrieval was active. Raw samples in records include this load;
replay therefore compares only samples before the first actuation request.

**Sensor faults**: disconnect / NaN return a non-finite reading; out-of-range replaces the value (default
60 N); stuck repeats the first value read after the injection (load included); `over_tension` overrides the
value (default 25 N). **Comm**: heartbeat = poll time while the link is up and heartbeats are not lost.
**Health**: 11.4 V, 25 °C, e-stop released unless injected.

## 6. Fault injection

Spec `<kind>@<when>[:<value>]`, `when` = `<t_ms>` (absolute) or `retrieval+<ms>` (relative to the tick that
first observed `RETRIEVE`). An injection is applied at the **end of the first harness step** whose condition
holds (`t >= when`, resp. `t - retrieval_start >= ms`) and therefore acts **from the next tick**. Injections
persist until the end of the run.

| Kind | Effect | Typical result |
|---|---|---|
| `sensor_disconnect`, `sensor_nan` | non-finite readings | `SENSOR_DISCONNECTED` after 3 samples (or classifier `rejected_sensor_fault` inside a candidate) |
| `sensor_out_of_range[:N]` / `sensor_stuck` | value 60 N / frozen value | `SENSOR_OUT_OF_RANGE` / `SENSOR_STUCK` |
| `over_tension[:N]` | sensor reads N (default 25) | `OVER_TENSION` after 2 samples |
| `motor_stall` / `encoder_loss` | retrieval stalls (2.5 A, no motion) / position frozen at normal current | `MOTOR_STALL` |
| `hook_jam` | hook stops moving, 1.6 A | `ACTUATOR_TIMEOUT` |
| `comm_loss` | heartbeats stop, link still reports up | `COMM_LOSS` when heartbeat age > 300 ms (armed only) |
| `link_down` | link reports down | `COMM_LOSS` on the next tick (armed only) |
| `undervoltage[:V]` | bus voltage V (default 10.2) | `UNDERVOLTAGE` after 200 ms |
| `estop`, `over_temp[:C]` | e-stop / temperature C (default 75) | `E_STOP` / `OVER_TEMP` |
| `watchdog_reset` | boot cause (`when` ignored) | boot record, `IDLE` disarmed, arming refused until reset |

The scenarios `motor_stall` and `comm_loss` are bites whose drawn `fault_after_retrieval_start_ms`
(uniform 200–800 ms, **not** snapped) becomes the injection `<scenario>@retrieval+<value>`; i.e. the fault
acts from the tick after the first tick with `t - retrieval_start >= fault_after_retrieval_start_ms`. The
comm outage lasts until the end of the scenario signal (the web demo then restores the heartbeat so the
operator can reset).

## 7. Defaults (all provisional)

Classifier thresholds: `docs/architecture.md` / `config.h` `ClassifierThresholds`. Safety limits (§4) and:

| Parameter | Default | Note |
|---|---|---|
| `hook_set_target_mm`, `hook_set_speed_mm_s`, `hook_set_position_tol_mm` | 12 mm, 60 mm/s, 0.5 mm | stroke |
| `retrieval_target_m`, `retrieval_speed_m_s` | 1.0 m, 0.5 m/s | |
| `verify_ms` | 500 ms | VERIFY dwell (was 200 ms in the web demo) |
| CONFIRM dwell | 0 ticks | (was 3 ticks in the web demo) |
| `hook_to_retrieval_gap_ms` | 100 ms | new; was 0 (firmware/web) and 40 ms (Python) |
| `over_tension_samples` | 2 | was 1 in the web demo |
| `sensor_fault_samples` | 3 | was 1 in the web demo |
| `hook_set_line_load_n`, `retrieval_line_load_n` | 1.5 N, 0.6 N | SIMULATION coupling (was absent in Python) |
| `retrieval_current_base_a`, `retrieval_current_per_n_a` | 0.3 A, 0.1 A/N | (was 0.12 A/N in Python, 0.15 A/N in the web demo) |
| pre-roll / record caps | 500 ms, 400 samples, 60 feedback entries | |
| `observe_when_disarmed` | true | |

Parameter overrides use the same names everywhere: firmware `hookpilot_sim --set name=value`, Python
`run_scenario(..., overrides={...})` / `python -m evidence_service generate --set`, TS
`runScenario(..., { overrides })` / `npm run sim -- --set`.

## 8. Tick trace

One JSON object per harness step (after injections) plus one `end` object after the end-of-window handling.
Firmware: `hookpilot_sim --trace <file>` (or `make sim TRACE=<file>`); Python: `run_scenario(trace=True)` /
`generate --trace`; TS: `runScenario(..., { trace: true }).trace` / `npm run sim -- --trace <file>`.

| Key | Meaning |
|---|---|
| `k`, `phase`, `t` | step index, `tick` or `end`, time (ms) |
| `state`, `armed` | after the step |
| `raw` (null if non-finite), `valid`, `health`, `x`, `baseline`, `delta` | the processed sample of this tick |
| `load_n` | line load added to this tick's reading |
| `hook`, `retr` | `{pos, cur, active, at_target, pos_valid, status}` at the end of the step |
| `hb_ms` | last heartbeat time |
| `latched`, `active` | latched faults / conditions present now (fault-bit order) |
| `cmds` | requests made during the step: `{actuator, command, target, speed, t_ms, permitted, reason}` |
| `trans` | transitions during the step: `{t, from, to, reason}` |
| `injected` | injections applied at the end of the step |
| `records` | records logged during the step (firmware lists their hashes; compared by count) |

## 9. Cross-implementation comparison

`tools/xcompare.py` defines the comparison and the shared case list: 9 scenarios × armed/disarmed × seeds
1–20 (360 runs), 21 injection specs × {bite, idle} × armed/disarmed × seeds 1–2 (168 runs), 6 override sets
× 9 scenarios × seeds 1–2 (108 runs). Two runs match when every trace line is equal (numbers within 1e-9)
and every record's **core** is equal: scenario, seed, start/end times, samples, omitted-sample count,
features, result, reason, rule trace, trigger/decision times, safety decision, armed, faults, fault latch
times, interlocks, requested commands (with decision and reason), feedback, final state, stop reason,
milestones. Record fields outside the core differ by design (device id and firmware version, event id,
calibration label, `classification.implementation`, the thresholds block layout, Python's
`test_outcome`/`class_match`, hashes).

- Firmware vs Python: `tests/integration/test_cross_impl.py` runs `hookpilot_sim --trace` for every case.
- TS vs Python: `tools/gen_web_fixtures.py` writes `apps/web-demo/tests/fixtures/engine_reference.json`
  (gitignored, generated by `npm test`'s `pretest`); `apps/web-demo/tests/cross_impl.test.ts` replays every
  case in the TS engine.

Known numeric differences: none observed beyond floating-point summation order in the variance feature and
libm last-ulp differences in the generator (max |diff| observed 4.4e-16 TS vs Python).
