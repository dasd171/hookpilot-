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
  bite: { result: 'bite_candidate_confirmed', decision: 'permit', actuates: true, final: 'MONITOR' },
  noise_current: { result: 'rejected_noise', decision: 'deny', actuates: false, final: 'MONITOR' },
  rod_vibration: { result: 'rejected_vibration', decision: 'deny', actuates: false, final: 'MONITOR' },
  manual_handling: { result: 'rejected_manual_handling', decision: 'deny', actuates: false, final: 'MONITOR' },
  excessive_tension: { result: 'bite_candidate_confirmed', decision: 'safe_stop', fault: 'OVER_TENSION', actuates: true, final: 'SAFE_STOP' },
  sensor_failure: { result: 'rejected_sensor_fault', decision: 'safe_stop', fault: 'SENSOR_STUCK', actuates: false, final: 'SAFE_STOP' },
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
  it('armed bite walks the full cycle and returns to MONITOR', () => {
    const e = runScenario('bite', 42, { armed: true });
    expect(e.stateHistory.map((h) => h.to)).toEqual(['MONITOR', 'CANDIDATE', 'CONFIRM', 'HOOK_SET', 'RETRIEVE', 'VERIFY', 'MONITOR']);
    const r = e.events[0];
    expect(r.outcome.milestones).toEqual({ bite_detected: true, hook_set_completed: true, retrieval_completed: true, fish_landed: 'unknown' });
    expect(r.actuation.requested!.map((a) => a.actuator)).toEqual(['hook_set', 'retrieval']);
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
    expect(e.state).toBe('IDLE');
  });

  it('SAFE_STOP: arm and scenarios refused; reset blocked while fault live, then into IDLE disarmed', () => {
    const e = new HookPilotEngine({ seed: 42 });
    e.arm();
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
  const base = { baseline_n: 0.6, peak_delta_n: 2.5, max_rate_n_per_s: 20, duration_above_ms: 300, short_term_variance: 0.1, oscillation_count: 1, sensor_health: 'ok' as const };
  it('each rule maps to its rejection', () => {
    expect(classify(base, th).result).toBe('bite_candidate_confirmed');
    expect(classify({ ...base, sensor_health: 'disconnected' }, th).result).toBe('rejected_sensor_fault');
    expect(classify({ ...base, peak_delta_n: 15 }, th).result).toBe('rejected_manual_handling');
    expect(classify({ ...base, duration_above_ms: 9000 }, th).result).toBe('rejected_manual_handling');
    expect(classify({ ...base, oscillation_count: 9 }, th).result).toBe('rejected_vibration');
    expect(classify({ ...base, max_rate_n_per_s: 1.5 }, th).result).toBe('rejected_noise');
    expect(classify({ ...base, duration_above_ms: 80 }, th).result).toBe('rejected_transient');
    const c = classify(base, th);
    expect(c.rule_trace[c.rule_trace.length - 1]).toBe('=> bite_candidate_confirmed');
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
  it('processor health: NaN -> disconnected, >max -> out_of_range, repeats -> stuck', () => {
    const cfg = defaultConfig();
    const p = new SignalProcessor(cfg.classifier, cfg.safety);
    expect(p.update(0, 0.5, false).health).toBe('ok');
    expect(p.update(20, NaN, false).health).toBe('disconnected');
    expect(p.update(40, 60, false).health).toBe('out_of_range');
    let h = 'ok';
    for (let k = 0; k < 20; k++) h = p.update(60 + k * 20, 0.7, false).health;
    expect(h).toBe('stuck');
  });
});

// ---------------------------------------------------------------- safety unit
describe('SafetyController', () => {
  const lim = defaultConfig().safety;
  const idleAct = (name: 'hook_set' | 'retrieval') => ({ actuator: name, position: 0, current_a: 0, status: 'idle' as const, elapsed_ms: 0, active: false, history: [] as Array<[number, number]> });
  const inputs = (over: Partial<SafetyInputs> = {}): SafetyInputs => ({
    t_ms: 0,
    state: 'CONFIRM',
    armed: true,
    health: 'ok',
    tension_n: 2,
    sensor_valid: true,
    heartbeat_age_ms: 0,
    supply_v: 12.4,
    e_stop: false,
    hook_set: idleAct('hook_set'),
    retrieval: idleAct('retrieval'),
    ...over,
  });
  it('permits hook_set only when armed, healthy and from CONFIRM', () => {
    const s = new SafetyController(lim);
    expect(s.requestPermit('hook_set', inputs()).decision).toBe('permit');
    expect(s.requestPermit('hook_set', inputs({ armed: false })).decision).toBe('deny');
    expect(s.requestPermit('hook_set', inputs({ state: 'MONITOR' })).decision).toBe('deny');
    expect(s.requestPermit('hook_set', inputs({ health: 'stuck', sensor_valid: true })).decision).toBe('deny');
    expect(s.requestPermit('hook_set', inputs({ heartbeat_age_ms: 1000 })).decision).toBe('deny');
    expect(s.requestPermit('hook_set', inputs({ supply_v: 9 })).decision).toBe('deny');
    expect(s.requestPermit('hook_set', inputs({ state: 'SAFE_STOP' })).decision).toBe('deny');
  });
  it('never permits hook-set and retrieval simultaneously', () => {
    const s = new SafetyController(lim);
    const moving = (n: 'hook_set' | 'retrieval') => ({ ...idleAct(n), status: 'moving' as const, active: true });
    const r1 = s.requestPermit('retrieval', inputs({ state: 'HOOK_SET', hook_set: moving('hook_set') }));
    expect(r1.decision).toBe('deny');
    expect(r1.reason).toMatch(/simultaneous/);
    const r2 = s.requestPermit('hook_set', inputs({ retrieval: moving('retrieval') }));
    expect(r2.decision).toBe('deny');
    expect(r2.reason).toMatch(/simultaneous/);
  });
  it('retrieval denied above force limit; faults latch until reset', () => {
    const s = new SafetyController(lim);
    const done = { ...idleAct('hook_set'), status: 'complete' as const };
    expect(s.requestPermit('retrieval', inputs({ state: 'HOOK_SET', hook_set: done, tension_n: lim.retrieval_force_limit_n + 1 })).decision).toBe('deny');
    expect(s.requestPermit('retrieval', inputs({ state: 'HOOK_SET', hook_set: done })).decision).toBe('permit');
    const ev = s.evaluate(inputs({ tension_n: lim.hard_max_tension_n + 1 }));
    expect(ev.critical).toBe(true);
    expect(ev.live).toContain('OVER_TENSION');
    expect(s.requestPermit('hook_set', inputs()).decision).toBe('deny'); // latched
    expect(s.tryReset(inputs({ tension_n: lim.hard_max_tension_n + 1 })).ok).toBe(false);
    expect(s.tryReset(inputs()).ok).toBe(true);
    expect(s.requestPermit('hook_set', inputs()).decision).toBe('permit');
  });
  it('detects actuator timeout and stall', () => {
    const s = new SafetyController(lim);
    const slow = { ...idleAct('hook_set'), status: 'moving' as const, active: true, elapsed_ms: lim.hook_set_timeout_ms + 20, position: 5 };
    expect(s.liveFaults(inputs({ hook_set: slow }))).toContain('ACTUATOR_TIMEOUT');
    const hist: Array<[number, number]> = [];
    for (let t = 20; t <= 400; t += 20) hist.push([t, 0.3]);
    const jam = { ...idleAct('retrieval'), status: 'jammed' as const, active: true, elapsed_ms: 400, current_a: 2.4, position: 0.3, history: hist };
    expect(s.liveFaults(inputs({ retrieval: jam }))).toContain('MOTOR_STALL');
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
