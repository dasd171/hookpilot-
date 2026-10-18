/**
 * Headless engine run - SIMULATION ONLY. Same flags as firmware `hookpilot_sim`:
 *   npm run sim -- --scenario bite --seed 42 [--disarmed] [--inject kind@t|retrieval+ms[:value]]...
 *                  [--set name=value]... [--trace <file>] [--epoch 2026-01-01T00:00:00Z] [--summary]
 *   (--threshold N / --force-limit N are shorthands for --set trigger_delta_n=N / retrieval_force_limit_n=N)
 * Prints the event records (JSON array, hash-chained) to stdout; --trace writes one JSON line per tick.
 */
import { writeFileSync } from 'node:fs';
import { defaultConfig, runScenario, verifyChain, type SynthScenario } from '../src/engine/index';

const ALL = ['idle', 'bite', 'noise_current', 'rod_vibration', 'manual_handling', 'sensor_failure', 'excessive_tension', 'motor_stall', 'comm_loss'];
function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
function args(name: string): string[] {
  const out: string[] = [];
  process.argv.forEach((a, i) => {
    if (a === `--${name}` && i + 1 < process.argv.length) out.push(process.argv[i + 1]);
  });
  return out;
}
const flag = (name: string) => process.argv.includes(`--${name}`);

const scenario = (arg('scenario') ?? 'bite') as SynthScenario;
if (!ALL.includes(scenario)) {
  console.error(`unknown scenario "${scenario}". One of: ${ALL.join(', ')}`);
  process.exit(2);
}
const seed = Number.parseInt(arg('seed') ?? '42', 10);
if (!Number.isFinite(seed)) {
  console.error('--seed must be an integer');
  process.exit(2);
}
const epochArg = arg('epoch');
const epochMs = epochArg ? Date.parse(epochArg) : undefined;
const overrides: Record<string, number> = {};
for (const kv of args('set')) {
  const [k, v] = kv.split('=');
  overrides[k] = Number(v);
}
const th = arg('threshold');
if (th !== undefined) overrides.trigger_delta_n = Number(th);
const fl = arg('force-limit');
if (fl !== undefined) overrides.retrieval_force_limit_n = Number(fl);
const tracePath = arg('trace');
const e = runScenario(scenario, seed, {
  armed: !flag('disarmed'),
  epochMs,
  config: defaultConfig(),
  injections: args('inject'),
  overrides,
  trace: tracePath !== undefined,
});
const records = e.events;
if (tracePath) writeFileSync(tracePath, e.trace.map((l) => JSON.stringify(l)).join('\n') + '\n');
if (flag('summary')) {
  for (const r of records) {
    console.log([r.event_id, r.classification.result, r.safety.decision, r.outcome.final_state, r.outcome.stop_reason, r.integrity.record_sha256].join(' | '));
  }
  const chain = verifyChain(records);
  console.log(`chain_ok=${chain.ok} records=${records.length} states=${e.stateHistory.map((s) => s.to).join('>')}`);
} else {
  process.stdout.write(JSON.stringify(records, null, 2) + '\n');
}
