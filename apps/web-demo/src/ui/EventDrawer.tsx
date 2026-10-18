import { useEffect, useMemo, useRef } from 'react';
import { recordHash, type HookPilotEvent } from '../engine/index';

interface Props {
  record: HookPilotEvent;
  prev: HookPilotEvent | null;
  onClose: () => void;
}

/** display only: the record keeps full precision */
const n3 = (v: number | undefined) => (v === undefined ? '-' : v.toFixed(3));

function Sparkline({ samples }: { samples: Array<[number, number]> }) {
  if (samples.length < 2) return <p className="muted small">No valid samples recorded for this event.</p>;
  const w = 560, h = 90;
  const t0 = samples[0][0], t1 = samples[samples.length - 1][0];
  const max = Math.max(1, ...samples.map((s) => s[1])) * 1.1;
  const d = samples
    .map(([t, v], i) => `${i ? 'L' : 'M'}${(((t - t0) / Math.max(1, t1 - t0)) * w).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`)
    .join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label={`Recorded tension samples, ${samples.length} points over ${t1 - t0} ms, peak ${Math.max(...samples.map((s) => s[1])).toFixed(2)} N`} style={{ background: '#0b1525', borderRadius: 10 }}>
      <path d={d} fill="none" stroke="#4ee0a0" strokeWidth="1.6" />
    </svg>
  );
}

export function EventDrawer({ record: r, prev, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      prevFocus?.focus?.();
    };
  }, [onClose]);

  const recomputed = useMemo(() => recordHash(r), [r]);
  const hashOk = recomputed === r.integrity.record_sha256;
  const linkOk = (r.integrity.prev_record_sha256 ?? null) === (prev ? prev.integrity.record_sha256 : null);
  const safety = r.safety as HookPilotEvent['safety'] & { reason?: string; checks?: string[] };
  const f = r.features;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div className="row">
          <div>
            <div className="eyebrow">Event detail · SIMULATION</div>
            <h2 id="drawer-title" className="mono">{r.event_id}</h2>
          </div>
          <button ref={closeRef} className="btn sm" onClick={onClose}>
            Close <span className="sr-only">event detail</span>
          </button>
        </div>

        <h3>Decision</h3>
        <dl className="kv">
          <dt>Classification</dt>
          <dd><strong>{r.classification.result}</strong></dd>
          <dt>Reason</dt>
          <dd>{r.classification.reason}</dd>
          <dt>Classifier</dt>
          <dd>{r.classification.classifier}</dd>
          <dt>Scenario / seed</dt>
          <dd>{r.source.scenario ?? '-'} / {r.source.seed ?? '-'}{r.source.generator ? ` · ${r.source.generator}` : ''}</dd>
          <dt>Start / end (simulated clock)</dt>
          <dd className="mono">{r.timestamp.start_utc} → {r.timestamp.end_utc}</dd>
          <dt>Final state / stop reason</dt>
          <dd>{r.outcome.final_state} · {r.outcome.stop_reason}</dd>
          <dt>Milestones</dt>
          <dd>
            bite_detected={String(r.outcome.milestones.bite_detected)}, hook_set_completed={String(r.outcome.milestones.hook_set_completed)},
            retrieval_completed={String(r.outcome.milestones.retrieval_completed)}, fish_landed=<strong>{r.outcome.milestones.fish_landed}</strong>
          </dd>
        </dl>
        <p className="notice" style={{ marginTop: 10 }}>fish_landed is never inferred from actuator completion. Only an independent observer could set it.</p>

        <h3>Features</h3>
        <dl className="kv">
          <dt>baseline_n</dt><dd>{n3(f.baseline_n)} N</dd>
          <dt>peak_delta_n</dt><dd>{n3(f.peak_delta_n)} N</dd>
          <dt>max_rate_n_per_s</dt><dd>{n3(f.max_rate_n_per_s)} N/s</dd>
          <dt>duration_above_ms</dt><dd>{f.duration_above_ms} ms</dd>
          <dt>continuous_above_ms</dt><dd>{f.continuous_above_ms ?? '-'} ms</dd>
          <dt>short_term_variance</dt><dd>{n3(f.short_term_variance)} N²</dd>
          <dt>oscillation_count</dt><dd>{f.oscillation_count}</dd>
          <dt>sensor_health</dt><dd>{f.sensor_health}</dd>
        </dl>
        <div style={{ marginTop: 10 }}>
          <Sparkline samples={r.sensor_data.samples ?? []} />
        </div>

        <h3>Rule trace</h3>
        <ol className="trace">
          {(r.classification.rule_trace ?? []).map((line, i) => (
            <li key={i} className={line.startsWith('=>') ? 'final' : line.includes('FAIL') ? 'fail' : line.includes('pass') ? 'pass' : ''}>
              {line}
            </li>
          ))}
        </ol>
        <details style={{ marginTop: 8 }}>
          <summary className="small muted">Thresholds in force (provisional, not validated)</summary>
          <pre className="json">{JSON.stringify(r.classification.thresholds, null, 2)}</pre>
        </details>

        <h3>Safety decision</h3>
        <dl className="kv">
          <dt>Decision</dt>
          <dd><strong className={r.safety.decision === 'permit' ? 'ok' : r.safety.decision === 'deny' ? 'warn' : 'bad'}>{r.safety.decision.toUpperCase()}</strong></dd>
          <dt>Reason</dt><dd>{safety.reason ?? '-'}</dd>
          <dt>Armed at decision</dt><dd>{String(r.safety.armed)}</dd>
          <dt>Faults</dt><dd>{(r.safety.faults ?? []).length ? r.safety.faults!.join(', ') : 'none'}</dd>
        </dl>
        {safety.checks && safety.checks.length > 0 && (
          <ol className="trace" style={{ marginTop: 8 }}>
            {safety.checks.map((c, i) => (
              <li key={i} className={c.includes('DENY') ? 'fail' : c.startsWith('=>') ? 'final' : 'pass'}>{c}</li>
            ))}
          </ol>
        )}
        <p className="muted small" style={{ margin: '10px 0 6px' }}>Interlocks as recorded at the CONFIRM arbitration (or at the stop when no arbitration happened):</p>
        <ul className="interlocks">
          {Object.entries(r.safety.interlocks ?? {}).map(([k, v]) => (
            <li key={k}>
              <span className={v ? 'ok' : 'bad'} aria-hidden="true">{v ? '●' : '○'}</span>
              {k}: {v ? 'ok' : 'NOT OK'}
            </li>
          ))}
        </ul>

        <h3>Actuator telemetry (simulated)</h3>
        {(r.actuation.requested ?? []).length === 0 ? (
          <p className="muted small">No actuator command was requested for this event.</p>
        ) : (
          <>
            <div className="table-wrap" style={{ maxHeight: 160 }}>
              <table>
                <caption className="sr-only">Actuation requests</caption>
                <thead><tr><th>t_ms</th><th>actuator</th><th>command</th><th>limits</th></tr></thead>
                <tbody>
                  {r.actuation.requested!.map((a, i) => (
                    <tr key={i}><td>{a.t_ms}</td><td>{a.actuator}</td><td>{a.command}</td><td className="mono">{JSON.stringify(a.limit)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-wrap" style={{ maxHeight: 220, marginTop: 8 }}>
              <table>
                <caption className="sr-only">Actuator feedback</caption>
                <thead><tr><th>t_ms</th><th>actuator</th><th>position</th><th>current A</th><th>tension N</th><th>status</th></tr></thead>
                <tbody>
                  {(r.actuation.feedback ?? []).map((a, i) => (
                    <tr key={i}><td>{a.t_ms}</td><td>{a.actuator}</td><td>{a.position}{a.actuator === 'hook_set' ? ' mm' : ' m'}</td><td>{a.current_a}</td><td>{a.tension_n ?? 'n/a'}</td><td>{a.status}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <h3>Integrity</h3>
        <dl className="kv">
          <dt>record_sha256</dt><dd className="mono">{r.integrity.record_sha256}</dd>
          <dt>prev_record_sha256</dt><dd className="mono">{r.integrity.prev_record_sha256 ?? 'null (first record)'}</dd>
          <dt>Recomputed hash</dt><dd className={hashOk ? 'ok' : 'bad'}>{hashOk ? 'matches' : 'MISMATCH'}</dd>
          <dt>Chain link</dt><dd className={linkOk ? 'ok' : 'bad'}>{linkOk ? 'links to previous record' : 'BROKEN'}</dd>
          <dt>Signature</dt><dd>{r.integrity.signature ?? 'null (no secure element in prototype)'}</dd>
        </dl>

        <details style={{ marginTop: 16 }}>
          <summary className="small muted">Raw JSON record</summary>
          <pre className="json">{JSON.stringify(r, null, 2)}</pre>
        </details>
      </aside>
    </>
  );
}
