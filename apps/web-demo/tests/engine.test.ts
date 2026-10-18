import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import {
  BiteDetector,
  HookPilotEngine,
  Mulberry32,
  SCENARIOS,
  SafetyController,
  SignalProcessor,
  faultList,
  newFeedback,
  canonicalJson,
  classify,
  defaultConfig,
  generateScenario,
  recordHash,
  runScenario,
  sha256Hex,
  toCsv,
  verifyChain,
  type SafetyInputs,
  type ScenarioName,
} from '../src/engine/index';

const SEEDS = [1, 7, 42, 1234, 99999];

// ---------------------------------------------------------------- primitives
describe('mulberry32', () => {
  // reference implementation copied verbatim from SHARED_CONTRACT / evidence-service docstring
  function ref(a: number) {
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  it('matches the reference algorithm and fixed vectors', () => {
    const g = new Mulberry32(42);
    expect([g.next(), g.next(), g.next()]).toEqual([0.6011037519201636, 0.44829055899754167, 0.8524657934904099]);
    for (const seed of [0, 1, -5, 2 ** 31 - 1, 123456789]) {
      const a = new Mulberry32(seed);
      const b = ref(seed);
      for (let i = 0; i < 1000; i++) expect(a.next()).toBe(b());
    }
  });
  it('gauss consumes exactly two draws', () => {
    const a = new Mulberry32(9);
    const b = new Mulberry32(9);
    a.gauss();
    b.next();
    b.next();
    expect(a.next()).toBe(b.next());
  });
});

describe('sha256 + canonical JSON', () => {
  it('pure-TS sha256 equals node:crypto', () => {
    const inputs = ['', 'abc', 'a'.repeat(55), 'a'.repeat(56), 'a'.repeat(64), 'a'.repeat(1000), 'HookPilot – ünïcode ✓ 🎣'];
    for (const s of inputs) expect(sha256Hex(s)).toBe(createHash('sha256').update(s, 'utf8').digest('hex'));
    expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });
  it('sorts keys recursively, no whitespace, JS number formatting', () => {
    expect(canonicalJson({ b: 1, a: { d: [1, 2.5, { z: null, y: true }], c: 'x' } })).toBe('{"a":{"c":"x","d":[1,2.5,{"y":true,"z":null}]},"b":1}');
    expect(canonicalJson({ v: 1.0, w: -0, x: 1e21 })).toBe('{"v":1,"w":0,"x":1e+21}');
    expect(() => canonicalJson({ v: NaN })).toThrow();
  });
});

// ---------------------------------------------------------------- signals
describe('synthetic signals', () => {
  it('are reproducible from (scenario, seed) and differ across seeds', () => {
    for (const s of SCENARIOS) {
      const a = generateScenario(s, 42);
      const b = generateScenario(s, 42);
      const c = generateScenario(s, 43);
      expect(Array.from(a.tension)).toEqual(Array.from(b.tension));
      expect(Array.from(a.tension)).not.toEqual(Array.from(c.tension));
      expect(a.tension.length).toBe(a.durationMs / 20); // 50 Hz
    }
  });
});

// ---------------------------------------------------------------- classification per scenario
const EXPECT: Record<ScenarioName, { result: string; decision: string; fault?: string; actuates: boolean; final: string }> = {
  // a completed cycle ends in IDLE, disarmed (firmware semantics)
  bite: { result: 'bite_candidate_confirmed', decision: 'permit', actuates: true, final: 'IDLE' },
  noise_current: { result: 'rejected_noise', decision: 'deny', actuates: false, final: 'MONITOR' },
  rod_vibration: { result: 'rejected_vibration', decision: 'deny', actuates: false, final: 'MONITOR' },
  manual_handling: { result: 'rejected_manual_handling', decision: 'deny', actuates: false, final: 'MONITOR' },
  excessive_tension: { result: 'bite_candidate_confirmed', decision: 'safe_stop', fault: 'OVER_TENSION', actuates: true, final: 'SAFE_STOP' },
  // fault depends on the drawn mode (stuck | out_of_range), checked below
  sensor_failure: { result: 'rejected_sensor_fault', decision: 'safe_stop', actuates: false, final: 'SAFE_STOP' },
  comm_loss: { result: 'bite_candidate_confirmed', decision: 'safe_stop', fault: 'COMM_LOSS', actuates: true, final: 'SAFE_STOP' },
  motor_stall: { result: 'bite_candidate_confirmed', decision: 'safe_stop', fault: 'MOTOR_STALL', actuates: true, final: 'SAFE_STOP' },
};

describe('scenario classification (armed, fixed seeds)', () => {
  for (const s of SCENARIOS) {
    for (const seed of SEEDS) {
      it(`${s} seed=${seed}`, () => {
        const e = runScenario(s, seed, { armed: true });
        const exp = EXPECT[s];
        expect(e.events.length).toBe(1);
        const r = e.events[0];
        expect(r.classification.result).toBe(exp.result);
        expect(r.classification.classifier).toBe('deterministic-baseline-v1');
        expect(r.classification.reason.length).toBeGreaterThan(10);
        expect(r.classification.rule_trace!.length).toBeGreaterThan(0);
        expect(r.safety.decision).toBe(exp.decision);
        if (exp.fault) expect(r.safety.faults).toContain(exp.fault);
        if (s === 'sensor_failure') {
          const mode = generateScenario(s, seed).params.mode;
          expect(r.safety.faults).toContain(mode === 'stuck' ? 'SENSOR_STUCK' : 'SENSOR_OUT_OF_RANGE');
        }
        expect(r.source.generator).toBe('hookpilot-synth-v1');
        expect((r.actuation.requested ?? []).length > 0).toBe(exp.actuates);
        expect(r.outcome.final_state).toBe(exp.final);
        expect(r.outcome.milestones.fish_landed).toBe('unknown');
        expect(r.source.mode).toBe('simulation');
        expect(r.source.seed).toBe(seed);
        expect(r.actuation.simulated).toBe(true);
      });
    }
  }

  it('noise scenarios never command an actuator (armed and disarmed, 50 seeds)', () => {
    for (const s of ['noise_current', 'rod_vibration', 'manual_handling'] as ScenarioName[]) {
      for (let seed = 0; seed < 50; seed++) {
        for (const armed of [true, false]) {
          const e = runScenario(s, seed, { armed });
          for (const r of e.events) {
            expect(r.actuation.requested).toEqual([]);
            expect(r.outcome.milestones.hook_set_completed).toBe(false);
            expect(r.outcome.milestones.retrieval_completed).toBe(false);
          }
          expect(e.stateHistory.some((h) => h.to === 'HOOK_SET' || h.to === 'RETRIEVE')).toBe(false);
        }
      }
    }
  });

  it('a threshold above the bite amplitude yields no_event', () => {
    const cfg = defaultConfig();
    cfg.classifier.trigger_delta_n = 4.0;
    const e = runScenario('bite', 42, { armed: true, config: cfg });
    expect(e.events.map((r) => r.classification.result)).toEqual(['no_event']);
    expect(e.events[0].actuation.requested).toEqual([]);
  });
});

// ---------------------------------------------------------------- state machine
describe('state machine', () => {
  it('armed bite walks the full cycle and ends in IDLE, disarmed', () => {
    const e = runScenario('bite', 42, { armed: true });
    expect(e.stateHistory.map((h) => h.to)).toEqual(['MONITOR', 'CANDIDATE', 'CONFIRM', 'HOOK_SET', 'RETRIEVE', 'VERIFY', 'IDLE']);
    expect(e.armed).toBe(false); // disarmed after a completed cycle
    const r = e.events[0];
    expect(r.outcome.milestones).toEqual({ bite_detected: true, hook_set_completed: true, retrieval_completed: true, fish_landed: 'unknown' });
    // stroke, retrieval (after the hook_to_retrieval_gap_ms dead time), return to rest
    expect(r.actuation.requested!.map((a) => a.actuator)).toEqual(['hook_set', 'retrieval', 'hook_set']);
    const strokeDone = r.actuation.feedback!.find((f) => f.actuator === 'hook_set' && f.status === 'at_target')!;
    expect(r.actuation.requested![1].t_ms! - strokeDone.t_ms!).toBe(100);
    expect(r.actuation.feedback!.length).toBeGreaterThan(5);
  });

  it('boots into IDLE disarmed; disarmed bite is confirmed but denied, no actuation', () => {
    const e0 = new HookPilotEngine({ seed: 1 });
    expect(e0.state).toBe('IDLE');
    expect(e0.armed).toBe(false);
    const e = runScenario('bite', 42, { armed: false });
    const r = e.events[0];
    expect(r.classification.result).toBe('bite_candidate_confirmed');
    expect(r.safety.decision).toBe('deny');
    expect(r.safety.armed).toBe(false);
    expect(r.actuation.requested).toEqual([]);
    expect(r.outcome.stop_reason).toMatch(/^disarmed_observation_only/);
    expect(e.state).toBe('IDLE');
  });

  it('SAFE_STOP: arm and scenarios refused; reset blocked while fault live, then into IDLE disarmed', () => {
    const e = new HookPilotEngine({ seed: 42 });
    e.stepN(2); // a valid sample is needed before arming
    expect(e.arm().ok).toBe(true);
    e.startScenario('sensor_failure', 42);
    let guard = 0;
    while (e.state !== 'SAFE_STOP' && guard++ < 500) e.step();
    expect(e.state).toBe('SAFE_STOP');
    expect(e.armed).toBe(false);
    // the sensor is still stuck/disconnected for a while
    e.stepN(5);
    expect(e.resetToSafe().ok).toBe(false);
    expect(e.state).toBe('SAFE_STOP');
    expect(e.arm().ok).toBe(false);
    expect(e.startScenario('bite', 1).ok).toBe(false);
    while (e.busy && guard++ < 2000) e.step();
    e.stepN(5);
    const r = e.resetToSafe();
    expect(r.ok).toBe(true);
    expect(e.state).toBe('IDLE');
    expect(e.armed).toBe(false);
    expect(e.arm().ok).toBe(true);
    expect(e.state).toBe('MONITOR');
  });

  it('operator disarm during retrieval stops actuators and logs operator_disarm', () => {
    const e = new HookPilotEngine({ seed: 42 });
    e.stepN(2);
    e.arm();
    e.startScenario('bite', 42);
    let guard = 0;
    while (e.state !== 'RETRIEVE' && guard++ < 500) e.step();
    expect(e.state).toBe('RETRIEVE');
    e.disarm();
    expect(e.state).toBe('IDLE');
    expect(e.snapshot().retrieval.active).toBe(false);
    const r = e.events[e.events.length - 1];
    expect(r.outcome.stop_reason).toMatch(/operator_disarm/);
    expect(r.outcome.milestones.retrieval_completed).toBe(false);
  });
});

// ---------------------------------------------------------------- detector unit
describe('BiteDetector rules', () => {
  const th = defaultConfig().classifier;
  const base = { baseline_n: 0.6, peak_delta_n: 2.5, max_rate_n_per_s: 20, duration_above_ms: 300, continuous_above_ms: 300, short_term_variance: 0.1, oscillation_count: 1, sensor_health: 'ok' as const };
  it('each rule maps to its rejection', () => {
    expect(classify(base, th).result).toBe('bite_candidate_confirmed');
    expect(classify({ ...base, sensor_health: 'disconnected' }, th).result).toBe('rejected_sensor_fault');
    expect(classify({ ...base, peak_delta_n: th.manual_handling_delta_n + 0.5 }, th).result).toBe('rejected_manual_handling');
    expect(classify({ ...base, duration_above_ms: 9000 }, th).result).toBe('rejected_manual_handling');
    expect(classify({ ...base, oscillation_count: 9 }, th).result).toBe('rejected_vibration');
    expect(classify({ ...base, max_rate_n_per_s: 1.5 }, th).result).toBe('rejected_noise');
    expect(classify({ ...base, continuous_above_ms: 80 }, th).result).toBe('rejected_transient');
    // firmware rule order: rate (R4) is checked before the transient rule (R5)
    expect(classify({ ...base, continuous_above_ms: 80, max_rate_n_per_s: 1.5 }, th).result).toBe('rejected_noise');
    const c = classify(base, th, 600);
    expect(c.rule_trace[0]).toBe('window: 600 ms observed after trigger (decision_window_ms=600)');
    expect(c.rule_trace[c.rule_trace.length - 1]).toBe('ALL rules pass -> bite_candidate_confirmed (thresholds provisional)');
  });
  it('defaults are the firmware defaults and stay labelled provisional', () => {
    const c = defaultConfig();
    expect(c.classifier.decision_window_ms).toBe(600);
    expect(c.classifier.min_rate_n_per_s).toBe(2.0);
    expect(c.classifier.manual_handling_delta_n).toBe(5.0);
    expect(c.classifier.baseline_alpha).toBe(0.002);
    expect(c.safety.sensor_max_n).toBe(49.9);
    const r = runScenario('bite', 42).events[0];
    expect(r.classification.thresholds!.status).toBe('provisional - derive from calibration + labeled data');
    expect(r.classification.thresholds!.decision_window_ms).toBe(600);
  });
  it('short spike through processor + detector is rejected_transient', () => {
    const cfg = defaultConfig();
    const p = new SignalProcessor(cfg.classifier, cfg.safety);
    const d = new BiteDetector(cfg.classifier);
    const rng = new Mulberry32(5);
    let decision = null;
    for (let k = 0; k < 200 && !decision; k++) {
      const t = k * 20;
      const spike = t >= 1000 && t < 1080 ? 3 : 0; // 80 ms spike
      const f = p.update(t, 0.6 + 0.03 * rng.gauss() + spike, d.inCandidate);
      const o = d.update(f);
      if (o.kind === 'decision') decision = o.decision;
    }
    expect(decision?.result).toBe('rejected_transient');
  });
  it('processor health: NaN -> disconnected, >=max -> out_of_range, repeats -> stuck', () => {
    const cfg = defaultConfig();
    const p = new SignalProcessor(cfg.classifier, cfg.safety);
    expect(p.update(0, 0.5, false).health).toBe('ok');
    expect(p.update(20, NaN, false).health).toBe('disconnected');
    expect(p.update(40, 60, false).health).toBe('out_of_range');
    expect(p.update(60, cfg.safety.sensor_max_n, false).health).toBe('out_of_range'); // >= bound (firmware)
    expect(p.update(80, 49.99, false).health).toBe('out_of_range'); // synth saturation value
    let h = 'ok';
    for (let k = 0; k < 20; k++) h = p.update(60 + k * 20, 0.7, false).health;
    expect(h).toBe('stuck');
  });
});

// ---------------------------------------------------------------- safety unit
describe('SafetyController (port of firmware DefaultSafetyController)', () => {
  const lim = defaultConfig().safety;
  const inputs = (over: Partial<SafetyInputs> = {}): SafetyInputs => ({
    now_ms: 0,
    sample: { raw: 1.0, valid: true, health: 'ok' },
    hook: newFeedback(),
    retrieval: newFeedback(),
    bus_voltage_v: 11.4,
    temperature_c: 25,
    estop_active: false,
    comm_link_ok: true,
    last_heartbeat_ms: 0,
    ...over,
  });
  it('denies everything while disarmed; arm needs a clean state', () => {
    const s = new SafetyController(lim);
    expect(s.permit('hook_set', 12, 60, inputs()).permitted).toBe(false);
    expect(s.permit('hook_set', 12, 60, inputs()).reason).toBe('deny: not armed');
    s.update(inputs());
    expect(s.arm(inputs()).ok).toBe(true);
    expect(s.permit('hook_set', 12, 60, inputs()).permitted).toBe(true);
    expect(s.permit('hook_set', 15.5, 60, inputs()).reason).toBe('deny: hook_set target 15.500 mm outside travel limits');
    expect(s.permit('hook_set', 12, 500, inputs()).permitted).toBe(false);
    expect(s.permit('retrieval', 2.0, 0.5, inputs()).permitted).toBe(false);
  });
  it('never permits hook-set and retrieval together; enforces the hook-to-retrieval gap', () => {
    const s = new SafetyController(lim);
    s.update(inputs());
    s.arm(inputs());
    const moving = { ...newFeedback(), active: true, status: 'moving' };
    expect(s.permit('retrieval', 1, 0.5, inputs({ hook: moving })).reason).toBe('deny: mutual exclusion (hook_set active)');
    expect(s.permit('hook_set', 12, 60, inputs({ retrieval: moving })).reason).toBe('deny: mutual exclusion (retrieval active)');
    s.update(inputs({ now_ms: 0, hook: moving }));
    s.update(inputs({ now_ms: 20 })); // hook seen idle at t=20
    expect(s.permit('retrieval', 1, 0.5, inputs({ now_ms: 60 })).reason).toBe('deny: hook_set stopped 40 ms ago (< hook_to_retrieval_gap_ms)');
    expect(s.permit('retrieval', 1, 0.5, inputs({ now_ms: 120 })).permitted).toBe(true);
    expect(s.permit('retrieval', 1, 0.5, inputs({ now_ms: 120, sample: { raw: 8.5, valid: true, health: 'ok' } })).permitted).toBe(false);
  });
  it('over-tension needs 2 consecutive samples; latch tick recorded; reset only when the condition is gone', () => {
    const s = new SafetyController(lim);
    s.update(inputs());
    s.arm(inputs());
    const hi = { raw: lim.hard_max_tension_n + 0.5, valid: true, health: 'ok' as const };
    expect(s.update(inputs({ now_ms: 20, sample: hi }))).toBe(0);
    expect(faultList(s.update(inputs({ now_ms: 40, sample: hi })))).toEqual(['OVER_TENSION']);
    expect(s.latchTimeMs('OVER_TENSION')).toBe(40);
    expect(s.armed).toBe(false);
    s.update(inputs({ now_ms: 60, sample: hi }));
    expect(s.clearFaults()).toBe(false);
    s.update(inputs({ now_ms: 80 }));
    expect(s.clearFaults()).toBe(true);
  });
  it('sensor faults debounce over 3 samples; comm loss latches only while armed', () => {
    const s = new SafetyController(lim);
    const bad = { raw: NaN, valid: false, health: 'disconnected' as const };
    expect(s.update(inputs({ sample: bad }))).toBe(0);
    expect(s.update(inputs({ sample: bad }))).toBe(0);
    expect(faultList(s.update(inputs({ sample: bad })))).toEqual(['SENSOR_DISCONNECTED']);
    const t = new SafetyController(lim);
    expect(t.update(inputs({ now_ms: 1000, last_heartbeat_ms: 600 }))).toBe(0); // disarmed: not latched
    expect(faultList(t.active)).toEqual(['COMM_LOSS']);
    expect(t.arm(inputs({ now_ms: 1000, last_heartbeat_ms: 600 })).ok).toBe(false);
  });
});

// ---------------------------------------------------------------- logging / hashing
describe('event log integrity', () => {
  const schemaPath = fileURLToPath(new URL('../../../packages/event-schema/hookpilot-event.schema.json', import.meta.url));
  const schema = JSON.parse(readFileSync(schemaPath, 'utf8'));
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  const validate = ajv.compile(schema);

  it('every record from every scenario (armed + disarmed) validates against the JSON schema', () => {
    for (const s of SCENARIOS) {
      for (const armed of [true, false]) {
        const e = runScenario(s, 42, { armed });
        for (const r of e.events) {
          const ok = validate(r);
          if (!ok) throw new Error(`${s}: ${JSON.stringify(validate.errors)}`);
        }
      }
    }
  });

  it('the validator really rejects non-compliant records (guards against a vacuous schema test)', () => {
    const r = JSON.parse(JSON.stringify(runScenario('bite', 42).events[0]));
    expect(validate(r)).toBe(true);
    r.outcome.milestones.fish_landed = 'yes';
    expect(validate(r)).toBe(false);
    const r2 = JSON.parse(JSON.stringify(runScenario('bite', 42).events[0]));
    r2.source.mode = 'real';
    expect(validate(r2)).toBe(false);
  });

  it('hashes are deterministic for the same (scenario, seed, epoch) and differ across seeds', () => {
    for (const s of SCENARIOS) {
      const a = runScenario(s, 42).events.map((r) => r.integrity.record_sha256);
      const b = runScenario(s, 42).events.map((r) => r.integrity.record_sha256);
      const c = runScenario(s, 43).events.map((r) => r.integrity.record_sha256);
      expect(a).toEqual(b);
      expect(a).not.toEqual(c);
      expect(a[0]).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it('record_sha256 = sha256(canonical JSON with record_sha256 "") and the chain links + detects tampering', () => {
    const e = new HookPilotEngine({ seed: 42 });
    e.stepN(2);
    e.arm();
    for (const s of ['bite', 'noise_current', 'rod_vibration', 'manual_handling'] as ScenarioName[]) {
      e.startScenario(s, 42);
      let g = 0;
      while (e.busy && g++ < 2000) e.step();
    }
    const recs = e.events;
    expect(recs.length).toBe(4);
    expect(recs[0].integrity.prev_record_sha256).toBeNull();
    for (let i = 1; i < recs.length; i++) expect(recs[i].integrity.prev_record_sha256).toBe(recs[i - 1].integrity.record_sha256);
    const copy = JSON.parse(JSON.stringify(recs[1]));
    copy.integrity.record_sha256 = '';
    expect(createHash('sha256').update(canonicalJson(copy), 'utf8').digest('hex')).toBe(recs[1].integrity.record_sha256);
    expect(recordHash(recs[1])).toBe(recs[1].integrity.record_sha256);
    expect(verifyChain(recs).ok).toBe(true);
    const tampered = JSON.parse(JSON.stringify(recs));
    tampered[2].classification.result = 'bite_candidate_confirmed';
    const v = verifyChain(tampered);
    expect(v.ok).toBe(false);
    expect(v.index).toBe(2);
  });

  it('CSV export has a header plus one row per record', () => {
    const e = runScenario('bite', 42);
    const lines = toCsv(e.events).trim().split('\n');
    expect(lines.length).toBe(2);
    expect(lines[0].startsWith('event_id,')).toBe(true);
    expect(lines[1]).toContain('bite_candidate_confirmed');
  });
});
