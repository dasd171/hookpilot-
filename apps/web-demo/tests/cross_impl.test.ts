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
  type ScenarioName,
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

describe('TS engine records == Python classification (first record of each armed scenario run)', () => {
  const cases = fx.classifications.filter((p) => p.scenario !== 'idle');
  it(`${cases.length} engine runs: result, reason, rule_trace, features, generator id`, () => {
    for (const p of cases) {
      const ctx = `${p.scenario}/${p.seed}`;
      const e = runScenario(p.scenario as ScenarioName, p.seed, { armed: true, maxTicks: 5000 });
      expect(e.events.length, ctx).toBeGreaterThan(0);
      const r = e.events[0];
      expect(r.classification.result, ctx).toBe(p.result);
      expect(r.classification.reason, ctx).toBe(p.reason);
      expect(r.classification.rule_trace, ctx).toEqual(p.rule_trace);
      if (p.decision_t_ms !== null && p.result !== 'no_event') expect(r.classification.decision_t_ms, ctx).toBe(p.decision_t_ms);
      expectFeatures(r.features as CandidateFeatures, p.features, ctx);
      expect(r.source.generator, ctx).toBe(GENERATOR_ID);
      expect(r.source.seed, ctx).toBe(p.seed);
    }
  });
});
