/**
 * Headless engine run - SIMULATION ONLY.
 *   npm run sim -- --scenario bite --seed 42 [--disarmed] [--epoch 2026-01-01T00:00:00Z] [--threshold 1.0] [--force-limit 8] [--summary]
 * Prints the event records (JSON array, hash-chained) to stdout.
 */
import { SCENARIOS, defaultConfig, runScenario, verifyChain, type ScenarioName } from '../src/engine/index';

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const flag = (name: string) => process.argv.includes(`--${name}`);

const scenario = (arg('scenario') ?? 'bite') as ScenarioName;
if (!SCENARIOS.includes(scenario)) {
  console.error(`unknown scenario "${scenario}". One of: ${SCENARIOS.join(', ')}`);
  process.exit(2);
}
const seed = Number.parseInt(arg('seed') ?? '42', 10);
if (!Number.isFinite(seed)) {
  console.error('--seed must be an integer');
  process.exit(2);
}
const epochArg = arg('epoch');
const epochMs = epochArg ? Date.parse(epochArg) : undefined;
const config = defaultConfig();
const th = arg('threshold');
if (th !== undefined) config.classifier.trigger_delta_n = Number(th);
const fl = arg('force-limit');
if (fl !== undefined) config.safety.retrieval_force_limit_n = Number(fl);
const e = runScenario(scenario, seed, { armed: !flag('disarmed'), epochMs, config, maxTicks: 5000 });
const records = e.events;
if (flag('summary')) {
  for (const r of records) {
    console.log(
      [r.event_id, r.classification.result, r.safety.decision, r.outcome.final_state, r.outcome.stop_reason, r.integrity.record_sha256].join(' | '),
    );
  }
  const chain = verifyChain(records);
  console.log(`chain_ok=${chain.ok} records=${records.length} states=${e.stateHistory.map((s) => s.to).join('>')}`);
} else {
  process.stdout.write(JSON.stringify(records, null, 2) + '\n');
}
