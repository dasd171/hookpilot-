/**
 * Cross-implementation check (SIMULATION): the TS generator, the TS classifier and the TS engine against the
 * Python port (evidence_service), which is itself a line-by-line port of the firmware C++.
 * Fixtures: tests/fixtures/python_reference.json, produced by `python tools/gen_web_fixtures.py`
 * (tests/integration/test_web_fixtures.py fails if they are stale).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  CLASSIFIER_ID,
  GENERATOR_ID,
  classifyWindow,
  defaultConfig,
  generateScenario,
  runScenario,
  type CandidateFeatures,
  type HookPilotEvent,
  type SynthScenario,
} from '../src/engine/index';

interface SignalFx {
  scenario: SynthScenario;
  seed: number;
  params: Record<string, number | string>;
  tension_n: number[];
}
interface ClassFx {
  scenario: SynthScenario;
  seed: number;
  result: string;
  reason: string;
  rule_trace: string[];
  decision_t_ms: number | null;
  trigger_t_ms: number | null;
  features: CandidateFeatures;
}
const fx = JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/python_reference.json', import.meta.url)), 'utf8')) as {
  generator: string;
  classifier: string;
  thresholds: Record<string, number | null>;
  signals: SignalFx[];
  classifications: ClassFx[];
};

const TOL = 1e-9;
const NUM_FEATURES: Array<keyof CandidateFeatures> = [
  'baseline_n',
  'peak_delta_n',
  'max_rate_n_per_s',
  'duration_above_ms',
  'continuous_above_ms',
  'short_term_variance',
  'oscillation_count',
];

function expectFeatures(ts: Partial<CandidateFeatures>, py: CandidateFeatures, ctx: string) {
  for (const k of NUM_FEATURES) {
    const a = ts[k] as number;
    const b = py[k] as number;
    if (!(Math.abs(a - b) <= TOL)) throw new Error(`${ctx}: feature ${k} ts=${a} py=${b}`);
  }
  expect(ts.sensor_health, ctx).toBe(py.sensor_health);
}

describe('fixtures describe the same generator / classifier / defaults', () => {
  it('ids and default thresholds match', () => {
    expect(fx.generator).toBe(GENERATOR_ID);
    expect(fx.classifier).toBe(CLASSIFIER_ID);
    const c = defaultConfig();
    const merged: Record<string, number> = { ...c.classifier, sensor_min_n: c.safety.sensor_min_n, sensor_max_n: c.safety.sensor_max_n, stuck_samples: c.safety.stuck_samples };
    for (const [k, v] of Object.entries(fx.thresholds)) expect(merged[k], k).toBe(v);
    expect(Object.keys(merged).sort()).toEqual(Object.keys(fx.thresholds).sort());
  });
});

describe('hookpilot-synth-v1: TS port == Python (and C++) within 1e-9', () => {
  it(`${fx.signals.length} (scenario, seed) waveforms and drawn parameters`, () => {
    let maxDiff = 0;
    let identical = 0;
    let total = 0;
    for (const s of fx.signals) {
      const g = generateScenario(s.scenario, s.seed);
      expect(g.tension.length, `${s.scenario}/${s.seed}`).toBe(s.tension_n.length);
      for (let i = 0; i < g.tension.length; i++) {
        const d = Math.abs(g.tension[i] - s.tension_n[i]);
        maxDiff = Math.max(maxDiff, d);
        if (d === 0) identical++;
        total++;
      }
      for (const [k, v] of Object.entries(s.params)) {
        const tv = (g.params as unknown as Record<string, number | string>)[k];
        if (typeof v === 'string') expect(tv, `${s.scenario}/${s.seed} ${k}`).toBe(v);
        else expect(Math.abs((tv as number) - v), `${s.scenario}/${s.seed} ${k}`).toBeLessThanOrEqual(TOL);
      }
    }
    expect(maxDiff).toBeLessThanOrEqual(TOL);
    expect(total).toBe(fx.signals.length * 400);
    // informational: how many samples are bit-identical (libm last-ulp differences are allowed)
    console.info(`synth cross-check: ${identical}/${total} samples bit-identical, max |diff| = ${maxDiff}`);
  });
});

describe('deterministic-baseline-v1: TS classifyWindow == Python classify (result, reason, rule trace, features)', () => {
  it(`${fx.classifications.length} (scenario, seed) windows`, () => {
    const c = defaultConfig();
    for (const p of fx.classifications) {
      const ctx = `${p.scenario}/${p.seed}`;
      const d = classifyWindow(generateScenario(p.scenario, p.seed).tension, c.classifier, c.safety);
      expect(d.result, ctx).toBe(p.result);
      expect(d.reason, ctx).toBe(p.reason);
      expect(d.rule_trace, ctx).toEqual(p.rule_trace);
      if (p.decision_t_ms !== null) expect(d.t_decision_ms, ctx).toBe(p.decision_t_ms);
      if (p.trigger_t_ms !== null) expect(d.t_start_ms, ctx).toBe(p.trigger_t_ms);
      expectFeatures(d.features, p.features, ctx);
    }
  });
});

// ---------------------------------------------------------------- tick engine (state machine + safety + actuators)
interface EngineRunFx {
  id: string;
  group: 'scenario' | 'injection' | 'override';
  scenario: SynthScenario;
  seed: number;
  armed: boolean;
  injections: string[];
  overrides: Record<string, number>;
  trace: Array<Record<string, unknown>>;
  records: Array<Record<string, unknown>>;
}
const engineFx = JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/engine_reference.json', import.meta.url)), 'utf8')) as {
  tolerance: number;
  trace_keys: string[];
  runs: EngineRunFx[];
};

const isNum = (v: unknown): v is number => typeof v === 'number';
/** First difference (numbers within TOL), mirrors tools/xcompare.py value_diff. */
function valueDiff(a: unknown, b: unknown, path: string): string | null {
  if (isNum(a) && isNum(b)) return Math.abs(a - b) <= TOL ? null : `${path}: ${a} != ${b}`;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return `${path}: length ${a.length} != ${b.length}`;
    for (let i = 0; i < a.length; i++) {
      const d = valueDiff(a[i], b[i], `${path}[${i}]`);
      if (d) return d;
    }
    return null;
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a as object).sort();
    const kb = Object.keys(b as object).sort();
    if (ka.join() !== kb.join()) return `${path}: keys ${ka.join(',')} != ${kb.join(',')}`;
    for (const k of ka) {
      const d = valueDiff((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], `${path}.${k}`);
      if (d) return d;
    }
    return null;
  }
  return a === b ? null : `${path}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`;
}
function maxAbs(a: unknown, b: unknown): number {
  if (isNum(a) && isNum(b)) return Math.abs(a - b);
  if (Array.isArray(a) && Array.isArray(b)) return a.reduce((m: number, x, i) => Math.max(m, maxAbs(x, b[i])), 0);
  if (a && b && typeof a === 'object' && typeof b === 'object')
    return Object.keys(a as object).reduce((m, k) => Math.max(m, maxAbs((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])), 0);
  return 0;
}

/** tools/xcompare.py record_core */
function recordCore(r: HookPilotEvent): Record<string, unknown> {
  const f = r.features as Record<string, unknown>;
  return {
    scenario: r.source.scenario,
    seed: r.source.seed,
    start_ms: r.timestamp.monotonic_ms,
    start_utc: r.timestamp.start_utc,
    end_utc: r.timestamp.end_utc,
    samples: r.sensor_data.samples,
    invalid_samples_omitted: r.sensor_data.invalid_samples_omitted ?? 0,
    features: Object.fromEntries(['baseline_n', 'peak_delta_n', 'max_rate_n_per_s', 'duration_above_ms', 'continuous_above_ms', 'short_term_variance', 'oscillation_count', 'sensor_health'].map((k) => [k, f[k]])),
    result: r.classification.result,
    reason: r.classification.reason,
    rule_trace: r.classification.rule_trace,
    trigger_t_ms: r.classification.trigger_t_ms ?? null,
    decision_t_ms: r.classification.decision_t_ms ?? null,
    decision: r.safety.decision,
    armed: r.safety.armed,
    faults: r.safety.faults,
    fault_latched_t_ms: r.safety.fault_latched_t_ms,
    interlocks: r.safety.interlocks,
    requested: (r.actuation.requested ?? []).map((q) => ({ actuator: q.actuator, command: q.command, limit: q.limit, t_ms: q.t_ms, decision: q.decision, decision_reason: q.decision_reason })),
    feedback: (r.actuation.feedback ?? []).map((q) => ({ actuator: q.actuator, t_ms: q.t_ms, position: q.position, current_a: q.current_a, tension_n: q.tension_n, status: q.status })),
    final_state: r.outcome.final_state,
    stop_reason: r.outcome.stop_reason,
    milestones: r.outcome.milestones,
  };
}

describe('tick engine: TS == Python (== firmware) for every shared case', () => {
  it(`${engineFx.runs.length} runs: per-tick state/safety/actuator trace and records within ${engineFx.tolerance}`, () => {
    const mismatches: string[] = [];
    let ticks = 0;
    let records = 0;
    let maxDiff = 0;
    const groups: Record<string, number> = {};
    for (const run of engineFx.runs) {
      groups[run.group] = (groups[run.group] ?? 0) + 1;
      const e = runScenario(run.scenario, run.seed, { armed: run.armed, injections: run.injections, overrides: run.overrides, trace: true });
      // decode the delta-encoded Python trace
      let prev: Record<string, unknown> = {};
      const py = run.trace.map((d) => (prev = { ...prev, ...d }));
      const ts = e.trace as unknown as Array<Record<string, unknown>>;
      let d: string | null = py.length === ts.length ? null : `trace length ${ts.length} != ${py.length}`;
      for (let i = 0; !d && i < py.length; i++) {
        const a = Object.fromEntries(engineFx.trace_keys.map((k) => [k, ts[i][k]]));
        const b = Object.fromEntries(engineFx.trace_keys.map((k) => [k, py[i][k]]));
        d = valueDiff(a, b, `tick[${i}]`);
        maxDiff = Math.max(maxDiff, maxAbs(a, b));
      }
      ticks += ts.length;
      const cores = e.events.map(recordCore);
      if (!d) d = cores.length === run.records.length ? null : `record count ${cores.length} != ${run.records.length}`;
      for (let i = 0; !d && i < cores.length; i++) {
        d = valueDiff(JSON.parse(JSON.stringify(cores[i])), run.records[i], `record[${i}]`);
        maxDiff = Math.max(maxDiff, maxAbs(cores[i], run.records[i]));
      }
      records += cores.length;
      if (d) mismatches.push(`${run.id}: ${d}`);
    }
    console.info(
      `engine cross-check: ${engineFx.runs.length} runs (${JSON.stringify(groups)}), ${ticks} trace lines, ${records} records, ` +
        `${mismatches.length} mismatching runs, max |diff| = ${maxDiff}`,
    );
    expect(mismatches.slice(0, 5)).toEqual([]);
  }, 180_000);
});
