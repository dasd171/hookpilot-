/**
 * HookPilotEngine - SIMULATION ONLY. Pure TS (no DOM / React). Advances in fixed 20 ms ticks (50 Hz).
 *
 * Pipeline per tick: world sample (scenario or idle water) -> SignalProcessor -> actuator models
 * -> BiteDetector (only in IDLE/MONITOR/CANDIDATE) -> SafetyController.evaluate (any critical fault
 * -> SAFE_STOP) -> state-machine step -> EventLogger.
 *
 * States: IDLE, MONITOR, CANDIDATE, CONFIRM, HOOK_SET, RETRIEVE, VERIFY, SAFE_STOP.
 * IDLE = disarmed (detector still observes and logs; safety denies every actuation).
 * SAFE_STOP exits only via resetToSafe() when no live fault remains, into IDLE (disarmed).
 *
 * Scenario runs use the shared generator hookpilot-synth-v1 and the firmware classifier rules, so every
 * scenario record is replayable by evidence_service.replay from (scenario, seed): each scenario run
 * re-initialises the signal processor and the detector, and all times inside a record (samples,
 * decision, actuation) are "local" ms since the scenario start (timestamp.monotonic_ms keeps engine time).
 */
import type {
  ActuationFeedback,
  ActuationRequest,
  ClassificationResult,
  FaultCode,
  FinalState,
  HookPilotEvent,
  MachineState,
  SafetyDecision,
  ScenarioName,
} from '../../../../packages/shared-types/src/index';
import { SimActuator, type ActuatorTelemetry } from './actuators';
import { CLASSIFIER_ID, CLASSIFIER_IMPLEMENTATION, DT_MS, SAMPLE_RATE_HZ, cloneConfig, defaultConfig, thresholdsRecord, type EngineConfig } from './config';
import { BiteDetector, emptyFeatures, monitorSensorFaultDecision, noEventDecision, type Decision } from './detector';
import { EventLogger, r3 } from './logger';
import { Mulberry32 } from './prng';
import { SignalProcessor, type SampleFeatures } from './processor';
import { SafetyController, type PermitResult, type SafetyInputs } from './safety';
import { IdleSource, generateScenario, type ScenarioSignal } from './signals';

/** comm_loss scenario: how long the host heartbeat stays lost once injected (ms). Simulation choice. */
const COMM_OUTAGE_MS = 2000;

export const DEVICE_ID = 'hp-sim-0001';
export const FIRMWARE_VERSION = 'web-demo-sim-0.1.0';
export const DEFAULT_EPOCH_MS = Date.UTC(2026, 0, 1, 0, 0, 0);
const PRE_TRIGGER_MS = 500;
const CONFIRM_DWELL_TICKS = 3;
const VERIFY_DWELL_TICKS = 10;
const FEEDBACK_EVERY_TICKS = 5;
const SUPPLY_V = 12.4; // simulated, constant

export interface EngineOptions {
  seed: number;
  /** wall-clock origin for timestamps; fixed default keeps records (and hashes) reproducible */
  epochMs?: number;
  config?: EngineConfig;
}

export interface ChartPoint {
  t: number;
  x: number; // NaN when sensor invalid
  baseline: number;
  state: MachineState;
}

export interface LogLine {
  t_ms: number;
  level: 'info' | 'warn' | 'error';
  text: string;
}

export interface Counters {
  events: number;
  confirmed: number;
  rejected: number;
  no_event: number;
  hook_set_cycles: number;
  retrievals_completed: number;
  safety_stops: number;
  denied: number;
}

export interface EngineSnapshot {
  t_ms: number;
  state: MachineState;
  armed: boolean;
  tension_n: number | null;
  baseline_n: number;
  delta_n: number;
  rate_n_per_s: number;
  sensor_health: SampleFeatures['health'];
  detector_phase: BiteDetector['phase'];
  scenario: { name: ScenarioName; seed: number; progress: number } | null;
  live_faults: FaultCode[];
  latched_faults: FaultCode[];
  interlocks: Record<string, boolean>;
  hook_set: ActuatorTelemetry;
  retrieval: ActuatorTelemetry;
  heartbeat_ok: boolean;
  counters: Counters;
}

interface ScenarioRun {
  signal: ScenarioSignal;
  k: number;
  events: number;
  peakDelta: number;
  playbackDone: boolean;
  /** every valid scenario sample (local time), attached to a no_event record */
  samples: Array<[number, number]>;
}

interface OpenCycle {
  /** local time (ms since the scenario start) */
  tStart: number;
  scenario: ScenarioName | null;
  scenarioSeed: number | null;
  groundTruth: string | null;
  generator: string | null;
  samples: Array<[number, number]>;
  decision: Decision | null;
  requested: ActuationRequest[];
  feedback: ActuationFeedback[];
  hookSetCompleted: boolean;
  retrievalCompleted: boolean;
  permits: PermitResult[];
  armedAtDecision: boolean;
  interlocksAtDecision: Record<string, boolean> | null;
}

export class HookPilotEngine {
  readonly seed: number;
  readonly epochMs: number;
  readonly config: EngineConfig;
  t = 0;
  state: MachineState = 'IDLE'; // boots into IDLE, disarmed
  armed = false;
  readonly logger = new EventLogger();
  readonly log: LogLine[] = [];
  readonly chart: ChartPoint[] = [];
  readonly stateHistory: Array<{ t_ms: number; from: MachineState; to: MachineState }> = [];

  private processor: SignalProcessor;
  private detector: BiteDetector;
  private safety: SafetyController;
  private hookSet = new SimActuator('hook_set');
  private retrieval = new SimActuator('retrieval');
  private idle: IdleSource;
  private idRng: Mulberry32;
  private counter = 0;
  private run: ScenarioRun | null = null;
  private cycle: OpenCycle | null = null;
  private lastHeartbeat = 0;
  private dwell = 0;
  private tick = 0;
  private lastFeatures: SampleFeatures | null = null;
  private lastLive: FaultCode[] = [];
  private lastInterlocks: Record<string, boolean> = {};
  private recent: Array<[number, number]> = []; // pre-trigger buffer (local time)
  /** engine time of the last scenario start; local time = t - timeBase */
  private timeBase = 0;
  /** actuation-model fault of the running scenario, armed when retrieval starts (local time) */
  private pendingFault: { kind: 'motor_stall' | 'comm_loss'; atMs: number } | null = null;
  private commOutage: { from: number; to: number } | null = null;
  private listeners = new Set<(e: HookPilotEvent) => void>();

  constructor(opts: EngineOptions) {
    this.seed = opts.seed | 0;
    this.epochMs = opts.epochMs ?? DEFAULT_EPOCH_MS;
    this.config = cloneConfig(opts.config ?? defaultConfig());
    this.processor = new SignalProcessor(this.config.classifier, this.config.safety);
    this.detector = new BiteDetector(this.config.classifier);
    this.safety = new SafetyController(this.config.safety);
    this.idle = new IdleSource(this.seed);
    this.idRng = new Mulberry32((this.seed ^ 0x51ed27) | 0);
    this.say('info', 'Engine booted (SIMULATION). State IDLE, disarmed. No hardware connected.');
  }

  // ------------------------------------------------------------------ public API
  get events(): HookPilotEvent[] {
    return this.logger.records;
  }
  get busy(): boolean {
    return this.run !== null || this.cycle !== null;
  }
  onEvent(fn: (e: HookPilotEvent) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  setTriggerThreshold(n: number) {
    this.config.classifier.trigger_delta_n = n;
  }
  setForceLimit(n: number) {
    this.config.safety.retrieval_force_limit_n = n;
  }

  arm(): { ok: boolean; reason: string } {
    if (this.state === 'SAFE_STOP') return this.refuse('Arm refused: system is in SAFE_STOP. Reset first.');
    const live = this.safety.liveFaults(this.safetyInputs());
    if (live.length) return this.refuse(`Arm refused: active faults ${live.join(', ')}.`);
    this.armed = true;
    if (this.state === 'IDLE') this.setState('MONITOR');
    this.say('info', 'Automation ARMED (simulated). Safety controller still arbitrates every actuation.');
    return { ok: true, reason: 'armed' };
  }

  disarm(): { ok: boolean; reason: string } {
    this.armed = false;
    if (this.hookSet.active || this.retrieval.active || this.state === 'VERIFY' || this.state === 'HOOK_SET' || this.state === 'RETRIEVE') {
      this.hookSet.stop();
      this.retrieval.stop();
      if (this.cycle) this.closeCycle('deny', 'operator_disarm: actuators stopped', 'IDLE');
      this.hookSet.idle();
      this.retrieval.idle();
    }
    if (this.state === 'MONITOR' || this.state === 'HOOK_SET' || this.state === 'RETRIEVE' || this.state === 'VERIFY') this.setState('IDLE');
    this.say('info', 'Automation DISARMED.');
    return { ok: true, reason: 'disarmed' };
  }

  startScenario(name: ScenarioName, seed: number = this.seed): { ok: boolean; reason: string } {
    if (this.state === 'SAFE_STOP') return this.refuse('Scenario refused: system is in SAFE_STOP. Reset first.');
    if (this.busy) return this.refuse('Scenario refused: another scenario/cycle is still running.');
    const signal = generateScenario(name, seed | 0);
    this.run = { signal, k: 0, events: 0, peakDelta: 0, playbackDone: false, samples: [] };
    // fresh processor + detector: the record depends only on (scenario, seed) and the thresholds
    this.timeBase = this.t;
    this.processor.reset();
    this.detector.reset();
    this.recent = [];
    this.pendingFault = null;
    this.commOutage = null;
    this.say('info', `Scenario "${name}" started (seed ${seed | 0}, ${signal.durationMs} ms ${signal.generator} signal).`);
    return { ok: true, reason: 'started' };
  }

  /** Operator "Reset to safe state". */
  resetToSafe(): { ok: boolean; reason: string } {
    if (this.state === 'SAFE_STOP') {
      const r = this.safety.tryReset(this.safetyInputs());
      if (!r.ok) return this.refuse(`Reset blocked: faults still active (${r.live.join(', ')}). Wait until they clear.`);
      this.hookSet.idle();
      this.retrieval.idle();
      this.armed = false;
      this.pendingFault = null;
      this.processor.reset();
      this.detector.reset();
      this.setState('IDLE');
      this.say('info', 'Reset accepted: faults cleared. State IDLE, disarmed.');
      return { ok: true, reason: 'reset' };
    }
    this.hookSet.stop();
    this.retrieval.stop();
    if (this.cycle) this.closeCycle('deny', 'operator_reset: cycle aborted, actuators stopped', 'IDLE');
    this.hookSet.idle();
    this.retrieval.idle();
    if (this.run) {
      this.say('warn', `Scenario "${this.run.signal.scenario}" aborted by operator reset.`);
      this.run = null;
    }
    this.armed = false;
    this.pendingFault = null;
    this.commOutage = null;
    this.safety.clearAll();
    this.processor.reset();
    this.detector.reset();
    this.setState('IDLE');
    this.say('info', 'Reset to safe state: actuators stopped, disarmed, IDLE.');
    return { ok: true, reason: 'reset' };
  }

  /** Advance one 20 ms tick. */
  step(): void {
    const t = this.t;
    const lt = t - this.timeBase; // local time: ms since the scenario start
    const L = this.config.safety;

    // 1. world
    let env: number;
    let hb = 1;
    let lastScenarioSample = false;
    const run = this.run;
    if (run && !run.playbackDone) {
      const s = run.signal;
      env = s.tension[run.k];
      run.k++;
      if (run.k >= s.tension.length) {
        run.playbackDone = true;
        lastScenarioSample = true;
      }
    } else {
      env = this.idle.sample(t);
    }
    // actuation-model faults (motor_stall / comm_loss), timed from retrieval start as in engine.py
    const pf = this.pendingFault;
    if (pf && lt >= pf.atMs) {
      if (pf.kind === 'motor_stall') this.retrieval.jam();
      else this.commOutage = { from: pf.atMs, to: pf.atMs + COMM_OUTAGE_MS };
      this.pendingFault = null;
    }
    if (this.commOutage) {
      if (lt >= this.commOutage.from && lt < this.commOutage.to) hb = 0;
      else if (lt >= this.commOutage.to) this.commOutage = null;
    }
    if (hb) this.lastHeartbeat = t;
    const load = this.hookSet.lineLoad(this.config.actuators) + this.retrieval.lineLoad(this.config.actuators);
    const raw = Number.isFinite(env) ? env + load : NaN;

    // 2. signal processing (baseline frozen while a candidate/cycle is open)
    const f = this.processor.update(lt, raw, this.cycle !== null);
    this.lastFeatures = f;
    if (run && f.valid) run.peakDelta = Math.max(run.peakDelta, f.delta);

    // 3. actuator models
    const tension = f.valid ? f.x : NaN;
    this.hookSet.step(tension, L.hook_set_travel_limit_mm);
    this.retrieval.step(tension, Number.POSITIVE_INFINITY);

    // 4. sample bookkeeping
    if (f.valid) {
      this.recent.push([lt, f.x]);
      while (this.recent.length && this.recent[0][0] < lt - PRE_TRIGGER_MS) this.recent.shift();
      if (this.cycle) this.cycle.samples.push([lt, f.x]);
      if (run && (!run.playbackDone || lastScenarioSample)) run.samples.push([lt, f.x]);
    }
    this.chart.push({ t, x: f.valid ? f.x : NaN, baseline: f.baseline, state: this.state });
    if (this.chart.length > 500) this.chart.shift();

    // 5. classifier (observes only in IDLE / MONITOR / CANDIDATE)
    if (this.state === 'IDLE' || this.state === 'MONITOR' || this.state === 'CANDIDATE') {
      const out = this.detector.update(f);
      if (out.kind === 'candidate_started') {
        this.openCycle(lt);
        this.setState('CANDIDATE');
      } else if (out.kind === 'decision') {
        this.onDecision(out.decision);
      }
      // end of the scenario window with a candidate still open: decide on what was observed
      // (same as the Python port's finish(t_end))
      if (lastScenarioSample && this.detector.inCandidate) {
        const d = this.detector.forceDecision(lt);
        if (d) this.onDecision(d);
      }
    }

    // 6. safety (independent of the classifier)
    const inputs = this.safetyInputs();
    const ev = this.safety.evaluate(inputs);
    this.lastLive = ev.live;
    this.lastInterlocks = ev.interlocks;
    if (ev.critical && this.state !== 'SAFE_STOP') {
      this.enterSafeStop(ev.live);
    } else {
      this.advance(inputs);
    }

    // 7. actuator telemetry
    if (this.cycle && this.tick % FEEDBACK_EVERY_TICKS === 0) {
      for (const a of [this.hookSet, this.retrieval]) {
        if (a.status !== 'idle') this.cycle.feedback.push(this.feedbackOf(a, lt, tension));
      }
    }

    // 8. scenario completion
    if (this.run && this.run.playbackDone && !this.cycle) this.finishScenario(lt);

    this.tick++;
    this.t += DT_MS;
  }

  /** Run n ticks. */
  stepN(n: number) {
    for (let i = 0; i < n; i++) this.step();
  }

  snapshot(): EngineSnapshot {
    const f = this.lastFeatures;
    const records = this.logger.records;
    const c: Counters = {
      events: records.length,
      confirmed: 0,
      rejected: 0,
      no_event: 0,
      hook_set_cycles: 0,
      retrievals_completed: 0,
      safety_stops: 0,
      denied: 0,
    };
    for (const r of records) {
      const res = r.classification.result;
      if (res === 'bite_candidate_confirmed') c.confirmed++;
      else if (res === 'no_event') c.no_event++;
      else c.rejected++;
      if (r.outcome.milestones.hook_set_completed) c.hook_set_cycles++;
      if (r.outcome.milestones.retrieval_completed) c.retrievals_completed++;
      if (r.safety.decision === 'safe_stop') c.safety_stops++;
      if (r.safety.decision === 'deny' && res === 'bite_candidate_confirmed') c.denied++;
    }
    return {
      t_ms: this.t,
      state: this.state,
      armed: this.armed,
      tension_n: f && f.valid ? f.x : null,
      baseline_n: f ? f.baseline : 0,
      delta_n: f ? f.delta : 0,
      rate_n_per_s: f ? f.rate : 0,
      sensor_health: f ? f.health : 'ok',
      detector_phase: this.detector.phase,
      scenario: this.run
        ? { name: this.run.signal.scenario as ScenarioName, seed: this.run.signal.seed, progress: Math.min(1, this.run.k / this.run.signal.tension.length) }
        : null,
      live_faults: [...this.lastLive],
      latched_faults: this.safety.latched,
      interlocks: { ...this.lastInterlocks },
      hook_set: this.hookSet.telemetry(),
      retrieval: this.retrieval.telemetry(),
      heartbeat_ok: this.t - this.lastHeartbeat <= this.config.safety.comm_timeout_ms + DT_MS,
      counters: c,
    };
  }

  // ------------------------------------------------------------------ internals
  /** local time: ms since the last scenario start (record-internal time base) */
  private get lt(): number {
    return this.t - this.timeBase;
  }

  private refuse(reason: string) {
    this.say('warn', reason);
    return { ok: false, reason };
  }

  private say(level: LogLine['level'], text: string) {
    this.log.push({ t_ms: this.t, level, text });
    if (this.log.length > 300) this.log.shift();
  }

  private setState(to: MachineState) {
    if (to === this.state) return;
    this.stateHistory.push({ t_ms: this.t, from: this.state, to });
    this.state = to;
    this.dwell = 0;
  }

  private restState(): FinalState {
    return this.armed ? 'MONITOR' : 'IDLE';
  }

  private safetyInputs(): SafetyInputs {
    const f = this.lastFeatures;
    return {
      t_ms: this.t,
      state: this.state,
      armed: this.armed,
      health: f ? f.health : 'ok',
      tension_n: f ? f.x : 0,
      sensor_valid: f ? f.valid : true,
      heartbeat_age_ms: this.t - this.lastHeartbeat,
      supply_v: SUPPLY_V,
      e_stop: false,
      hook_set: { ...this.hookSet.telemetry(), history: this.hookSet.history },
      retrieval: { ...this.retrieval.telemetry(), history: this.retrieval.history },
    };
  }

  private feedbackOf(a: SimActuator, t: number, tension: number): ActuationFeedback {
    return {
      actuator: a.name,
      t_ms: t,
      position: r3(a.position),
      current_a: r3(a.current),
      tension_n: Number.isFinite(tension) ? r3(tension) : null,
      status: a.status,
    };
  }

  private openCycle(t: number) {
    const run = this.run;
    this.cycle = {
      tStart: t,
      scenario: run ? (run.signal.scenario as ScenarioName) : null,
      scenarioSeed: run ? run.signal.seed : null,
      groundTruth: run ? run.signal.groundTruth : null,
      generator: run ? run.signal.generator : null,
      samples: this.recent.slice(),
      decision: null,
      requested: [],
      feedback: [],
      hookSetCompleted: false,
      retrievalCompleted: false,
      permits: [],
      armedAtDecision: this.armed,
      interlocksAtDecision: null,
    };
    this.say('info', `CANDIDATE opened at t=${t} ms (delta >= ${this.config.classifier.trigger_delta_n.toFixed(2)} N). Baseline frozen.`);
  }

  private onDecision(d: Decision) {
    if (!this.cycle) this.openCycle(d.t_start_ms);
    const c = this.cycle!;
    c.decision = d;
    c.armedAtDecision = this.armed;
    this.say(d.result === 'bite_candidate_confirmed' ? 'info' : 'warn', `Classifier: ${d.result} - ${d.reason}`);
    this.setState('CONFIRM');
  }

  /** State-machine step after safety found no critical fault. */
  private advance(inputs: SafetyInputs) {
    const c = this.cycle;
    this.dwell++;
    switch (this.state) {
      case 'CONFIRM': {
        if (!c || !c.decision) return;
        if (this.dwell < CONFIRM_DWELL_TICKS) return;
        c.interlocksAtDecision = { ...this.lastInterlocks };
        if (c.decision.result !== 'bite_candidate_confirmed') {
          this.closeCycle('deny', `classifier_rejected: ${c.decision.result} (no actuation requested)`, this.restState());
          return;
        }
        // classifier REQUESTS; safety decides
        const p = this.safety.requestPermit('hook_set', { ...inputs, state: 'CONFIRM' });
        c.permits.push(p);
        if (p.decision === 'deny') {
          this.say('warn', `Safety DENIED hook_set: ${p.reason}`);
          this.closeCycle('deny', `safety_denied: ${p.reason}`, this.restState());
          return;
        }
        const A = this.config.actuators;
        const L = this.config.safety;
        c.requested.push({
          actuator: 'hook_set',
          command: 'set_hook',
          limit: { travel_mm: A.hook_set_target_mm, max_travel_mm: L.hook_set_travel_limit_mm, timeout_ms: L.hook_set_timeout_ms, max_tension_n: L.hard_max_tension_n },
          t_ms: this.lt,
        });
        this.hookSet.start(A.hook_set_target_mm, A.hook_set_speed_mm_per_s);
        this.say('info', 'Safety PERMITTED hook_set (simulated actuator).');
        this.setState('HOOK_SET');
        return;
      }
      case 'HOOK_SET': {
        if (!c) return;
        if (this.hookSet.status !== 'complete') return;
        c.hookSetCompleted = true;
        c.feedback.push(this.feedbackOf(this.hookSet, this.lt, inputs.tension_n));
        const p = this.safety.requestPermit('retrieval', { ...inputs, state: 'HOOK_SET', hook_set: { ...inputs.hook_set, status: 'complete', active: false } });
        c.permits.push(p);
        this.hookSet.idle();
        if (p.decision === 'deny') {
          this.say('warn', `Safety DENIED retrieval: ${p.reason}`);
          this.closeCycle('deny', `safety_denied: ${p.reason}`, this.restState());
          return;
        }
        const A = this.config.actuators;
        const L = this.config.safety;
        c.requested.push({
          actuator: 'retrieval',
          command: 'retrieve',
          limit: { distance_m: A.retrieval_target_m, max_tension_n: L.retrieval_force_limit_n, timeout_ms: L.retrieval_timeout_ms, stall_current_a: L.stall_current_a },
          t_ms: this.lt,
        });
        this.retrieval.start(A.retrieval_target_m, A.retrieval_speed_m_per_s);
        const sig = this.run ? this.run.signal : null;
        if (sig && sig.faultAfterRetrievalStartMs !== null && (sig.scenario === 'motor_stall' || sig.scenario === 'comm_loss')) {
          this.pendingFault = { kind: sig.scenario, atMs: this.lt + sig.faultAfterRetrievalStartMs };
        }
        this.say('info', `Safety PERMITTED retrieval within force limit ${L.retrieval_force_limit_n.toFixed(1)} N.`);
        this.setState('RETRIEVE');
        return;
      }
      case 'RETRIEVE': {
        if (!c) return;
        if (this.retrieval.status !== 'complete') return;
        c.retrievalCompleted = true;
        c.feedback.push(this.feedbackOf(this.retrieval, this.lt, inputs.tension_n));
        this.retrieval.idle();
        this.setState('VERIFY');
        return;
      }
      case 'VERIFY': {
        if (this.dwell < VERIFY_DWELL_TICKS) return;
        this.closeCycle('permit', 'cycle_complete: hook-set and retrieval completed (simulated); fish landing NOT inferred', this.restState());
        return;
      }
      default:
        return;
    }
  }

  private enterSafeStop(faults: FaultCode[]) {
    this.hookSet.stop();
    this.retrieval.stop();
    const wasArmed = this.armed;
    this.armed = false;
    this.say('error', `SAFE_STOP: ${faults.join(', ')}. Actuators stopped. Reset required once faults clear.`);
    const reason = `safe_stop: ${faults.join(', ')}`;
    if (this.cycle) {
      if (!this.cycle.decision) {
        const d = this.detector.forceDecision(this.lt);
        if (d) this.cycle.decision = d;
      }
      this.cycle.armedAtDecision = this.cycle.decision ? this.cycle.armedAtDecision : wasArmed;
      this.setState('SAFE_STOP');
      this.closeCycle('safe_stop', reason, 'SAFE_STOP', faults);
    } else {
      // stand-alone fault record (no candidate open)
      this.openCycle(this.lt);
      const c = this.cycle!;
      c.armedAtDecision = wasArmed;
      const f = this.lastFeatures;
      if (f && !f.valid) {
        // same convention as the Python port: sensor health failure while monitoring
        c.decision = monitorSensorFaultDecision(f);
      } else {
        c.decision = {
          result: 'no_event',
          reason: `No bite candidate open; safety controller raised SAFE_STOP (${faults.join(', ')}).`,
          rule_trace: [`t=${this.lt}ms no candidate open -> safety ${reason}`],
          features: emptyFeatures({ baseline_n: f ? f.baseline : 0, short_term_variance: f ? f.variance : 0, sensor_health: f ? f.health : 'ok' }),
          t_start_ms: this.lt,
          t_decision_ms: this.lt,
        };
      }
      this.detector.reset();
      this.setState('SAFE_STOP');
      this.closeCycle('safe_stop', reason, 'SAFE_STOP', faults);
    }
    this.hookSet.idle();
    this.retrieval.idle();
  }

  private finishScenario(tEnd: number) {
    const run = this.run!;
    if (run.events === 0) {
      // nothing triggered: log a no_event record (with the whole scenario window) so every run is auditable
      this.openCycle(tEnd);
      const c = this.cycle!;
      c.samples = run.samples.slice();
      c.tStart = c.samples.length ? c.samples[0][0] : 0;
      c.decision = noEventDecision(tEnd, this.config.classifier, this.lastFeatures);
      this.say('info', `Scenario "${run.signal.scenario}" ended without a candidate (no_event).`);
      this.closeCycle('deny', 'no_event: nothing to actuate', this.state === 'SAFE_STOP' ? 'SAFE_STOP' : this.restState());
    }
    this.say('info', `Scenario "${run.signal.scenario}" finished.`);
    this.run = null;
  }

  private closeCycle(decision: SafetyDecision, stopReason: string, finalState: FinalState, faults: FaultCode[] = []) {
    const c = this.cycle;
    if (!c) return;
    this.cycle = null;
    const th = this.config.classifier;
    const L = this.config.safety;
    const d: Decision = c.decision ?? {
      result: 'no_event' as ClassificationResult,
      reason: 'Cycle closed before a classification was made.',
      rule_trace: [`t=${this.lt}ms cycle closed before a classification was made`],
      features: emptyFeatures(),
      t_start_ms: c.tStart,
      t_decision_ms: this.lt,
    };
    this.counter++;
    const id = `${DEVICE_ID}-${String(this.counter).padStart(6, '0')}-${this.idRng.nextU32().toString(16).padStart(8, '0')}`;
    const lastPermit = c.permits[c.permits.length - 1];
    const safetyReason =
      decision === 'safe_stop'
        ? `SAFE_STOP raised by safety controller: ${faults.join(', ')}`
        : lastPermit
          ? lastPermit.reason
          : 'no actuation requested by the state machine';
    const allFaults = Array.from(new Set<string>([...faults, ...this.safety.latched]));

    const record: Omit<HookPilotEvent, 'integrity'> = {
      schema_version: '1.0.0',
      event_id: id,
      device: { device_id: DEVICE_ID, firmware_version: FIRMWARE_VERSION, hardware_revision: 'none-simulated' },
      source: {
        mode: 'simulation',
        ...(c.scenario ? { scenario: c.scenario } : {}),
        ...(c.scenarioSeed !== null ? { seed: c.scenarioSeed } : {}),
        ground_truth_label: c.groundTruth,
        ...(c.generator ? { generator: c.generator } : {}),
      },
      timestamp: {
        start_utc: new Date(this.epochMs + this.timeBase + c.tStart).toISOString(),
        end_utc: new Date(this.epochMs + this.t).toISOString(),
        monotonic_ms: this.timeBase + c.tStart,
        clock_quality: 'host_clock_simulated',
      },
      calibration: { calibration_version: 'sim-uncalibrated-0', scale_n_per_count: 1, offset_counts: 0, calibrated_utc: null },
      sensor_data: {
        sample_rate_hz: SAMPLE_RATE_HZ,
        units: 'N',
        // full precision: replay regenerates (scenario, seed) and compares within 1e-6 N
        samples: c.samples.map(([t, x]) => [t, x] as [number, number]),
        raw_data_ref: null,
        raw_data_sha256: null,
      },
      // same feature keys as firmware and the Python port (unrounded, so recorded rules re-evaluate exactly)
      features: {
        baseline_n: d.features.baseline_n,
        peak_delta_n: d.features.peak_delta_n,
        max_rate_n_per_s: d.features.max_rate_n_per_s,
        duration_above_ms: d.features.duration_above_ms,
        continuous_above_ms: d.features.continuous_above_ms,
        short_term_variance: d.features.short_term_variance,
        oscillation_count: d.features.oscillation_count,
        sensor_health: d.features.sensor_health,
      },
      classification: {
        result: d.result,
        reason: d.reason,
        rule_trace: d.rule_trace,
        classifier: CLASSIFIER_ID,
        implementation: CLASSIFIER_IMPLEMENTATION,
        thresholds: thresholdsRecord(th, L),
        ...(c.decision && d.result !== 'no_event' ? { decision_t_ms: d.t_decision_ms } : {}),
        ...(c.decision && c.decision.rule_trace[0]?.includes('TRIGGER') ? { trigger_t_ms: d.t_start_ms } : {}),
      },
      safety: {
        decision,
        armed: c.armedAtDecision,
        interlocks: c.interlocksAtDecision ?? { ...this.lastInterlocks },
        faults: allFaults,
        // extra (schema allows additional properties): human-readable safety reasoning
        reason: safetyReason,
        checks: c.permits.flatMap((p) => p.checks),
      } as HookPilotEvent['safety'],
      actuation: { requested: c.requested, feedback: c.feedback, simulated: true },
      outcome: {
        final_state: finalState,
        stop_reason: stopReason,
        milestones: {
          bite_detected: d.result === 'bite_candidate_confirmed',
          hook_set_completed: c.hookSetCompleted,
          retrieval_completed: c.retrievalCompleted,
          fish_landed: 'unknown', // never inferred
        },
        test_outcome: 'not_evaluated',
      },
    };
    const sealed = this.logger.append(record);
    if (this.run) this.run.events++;
    if (finalState !== 'SAFE_STOP' && this.state !== 'SAFE_STOP') this.setState(finalState);
    this.say('info', `Event ${sealed.event_id} logged: ${d.result}, safety ${decision}, ${stopReason}.`);
    for (const fn of this.listeners) fn(sealed);
  }
}

/** Headless helper: run one scenario to completion. */
export function runScenario(
  scenario: ScenarioName,
  seed: number,
  opts: { armed?: boolean; epochMs?: number; config?: EngineConfig; maxTicks?: number } = {},
): HookPilotEngine {
  const e = new HookPilotEngine({ seed, epochMs: opts.epochMs, config: opts.config });
  if (opts.armed ?? true) e.arm();
  const r = e.startScenario(scenario, seed);
  if (!r.ok) throw new Error(r.reason);
  const max = opts.maxTicks ?? 2000;
  let n = 0;
  while (e.busy && n < max) {
    e.step();
    n++;
  }
  if (e.busy) throw new Error(`scenario ${scenario} did not finish within ${max} ticks`);
  return e;
}
