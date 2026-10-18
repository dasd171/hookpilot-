/** JSON / CSV export of event records (pure functions). */
import type { HookPilotEvent } from '../../../../packages/shared-types/src/index';

export function toJson(records: HookPilotEvent[]): string {
  return JSON.stringify(records, null, 2);
}

export const CSV_COLUMNS = [
  'event_id',
  'start_utc',
  'end_utc',
  'mode',
  'scenario',
  'seed',
  'classification_result',
  'classification_reason',
  'safety_decision',
  'safety_faults',
  'armed',
  'peak_delta_n',
  'max_rate_n_per_s',
  'duration_above_ms',
  'oscillation_count',
  'sensor_health',
  'actuations_requested',
  'final_state',
  'stop_reason',
  'bite_detected',
  'hook_set_completed',
  'retrieval_completed',
  'fish_landed',
  'record_sha256',
  'prev_record_sha256',
] as const;

function cell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(records: HookPilotEvent[]): string {
  const rows = records.map((r) =>
    [
      r.event_id,
      r.timestamp.start_utc,
      r.timestamp.end_utc,
      r.source.mode,
      r.source.scenario,
      r.source.seed,
      r.classification.result,
      r.classification.reason,
      r.safety.decision,
      (r.safety.faults ?? []).join(' '),
      r.safety.armed,
      r.features.peak_delta_n,
      r.features.max_rate_n_per_s,
      r.features.duration_above_ms,
      r.features.oscillation_count,
      r.features.sensor_health,
      (r.actuation.requested ?? []).map((a) => a.actuator).join(' '),
      r.outcome.final_state,
      r.outcome.stop_reason,
      r.outcome.milestones.bite_detected,
      r.outcome.milestones.hook_set_completed,
      r.outcome.milestones.retrieval_completed,
      r.outcome.milestones.fish_landed,
      r.integrity.record_sha256,
      r.integrity.prev_record_sha256,
    ]
      .map(cell)
      .join(','),
  );
  return [CSV_COLUMNS.join(','), ...rows].join('\n') + '\n';
}
