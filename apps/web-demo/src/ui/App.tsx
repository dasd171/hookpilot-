import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DT_MS,
  HookPilotEngine,
  STATES,
  toCsv,
  toJson,
  verifyChain,
  type EngineSnapshot,
  type HookPilotEvent,
  type MachineState,
  type ScenarioName,
} from '../engine/index';
import { EventDrawer } from './EventDrawer';
import { TensionChart } from './TensionChart';

const STATE_HELP: Record<MachineState, string> = {
  IDLE: 'disarmed',
  MONITOR: 'armed, sampling',
  CANDIDATE: 'observing',
  CONFIRM: 'classify + arbitrate',
  HOOK_SET: 'limited sweep',
  RETRIEVE: 'force-limited',
  VERIFY: 'check + log',
  SAFE_STOP: 'faulted',
};

const SCENARIO_BUTTONS: Array<{ id: ScenarioName; label: string; kind: string }> = [
  { id: 'bite', label: 'Simulate bite', kind: 'primary' },
  { id: 'noise_current', label: 'Simulate water / line noise', kind: '' },
  { id: 'rod_vibration', label: 'Simulate rod vibration', kind: '' },
  { id: 'manual_handling', label: 'Simulate manual handling', kind: '' },
  { id: 'excessive_tension', label: 'Simulate excessive tension', kind: 'warnb' },
  { id: 'sensor_failure', label: 'Simulate sensor failure', kind: 'warnb' },
  { id: 'comm_loss', label: 'Simulate comm loss', kind: 'warnb' },
  { id: 'motor_stall', label: 'Simulate motor stall', kind: 'warnb' },
];

const INTERLOCK_LABEL: Record<string, string> = {
  armed: 'Armed',
  e_stop_clear: 'E-stop clear',
  sensor_valid: 'Sensor valid',
  comm_ok: 'Host link (heartbeat)',
  tension_below_hard_max: 'Below hard tension max',
  tension_below_force_limit: 'Below retrieval force limit',
  supply_ok: 'Supply voltage',
  actuators_idle: 'Actuators idle',
  no_latched_faults: 'No latched faults',
};

function stateKind(s: MachineState): string {
  if (s === 'SAFE_STOP') return 'bad';
  if (s === 'CANDIDATE' || s === 'CONFIRM') return 'warn';
  if (s === 'HOOK_SET' || s === 'RETRIEVE' || s === 'VERIFY') return 'good';
  if (s === 'MONITOR') return 'info';
  return '';
}

function download(name: string, text: string, type: string) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function resultKind(r: HookPilotEvent): string {
  if (r.safety.decision === 'safe_stop') return 'bad';
  if (r.classification.result === 'bite_candidate_confirmed') return r.safety.decision === 'permit' ? 'good' : 'warn';
  return '';
}

export function App() {
  const engineRef = useRef<HookPilotEngine | null>(null);
  if (!engineRef.current) engineRef.current = new HookPilotEngine({ seed: 42, epochMs: Date.now() });
  const engine = engineRef.current;

  const [snap, setSnap] = useState<EngineSnapshot>(() => engine.snapshot());
  const [records, setRecords] = useState<HookPilotEvent[]>([]);
  const [frame, setFrame] = useState(0);
  const [threshold, setThreshold] = useState(engine.config.classifier.trigger_delta_n);
  const [forceLimit, setForceLimit] = useState(engine.config.safety.retrieval_force_limit_n);
  const [seed, setSeed] = useState(42);
  const [speed, setSpeed] = useState(1);
  const [paused, setPaused] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  const [toast, setToast] = useState<string | null>(null);
  const visited = useRef<Set<MachineState>>(new Set());
  const lastState = useRef<MachineState>('IDLE');

  // engine clock: fixed 20 ms ticks driven by requestAnimationFrame
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    let lastUi = 0;
    const loop = (now: number) => {
      const dt = Math.min(250, now - last);
      last = now;
      if (!pausedRef.current) {
        acc += dt * speedRef.current;
        let n = 0;
        while (acc >= DT_MS && n < 50) {
          engine.step();
          acc -= DT_MS;
          n++;
        }
        if (n === 50) acc = 0;
      }
      if (now - lastUi > 66) {
        lastUi = now;
        setSnap(engine.snapshot());
        setFrame((f) => f + 1);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    const off = engine.onEvent((e) => {
      setRecords([...engine.events]);
      setAnnounce(`New event: ${e.classification.result}, safety ${e.safety.decision}.`);
    });
    return () => {
      cancelAnimationFrame(raf);
      off();
    };
  }, [engine]);

  // stepper bookkeeping + state announcements
  useEffect(() => {
    if (snap.state !== lastState.current) {
      if (snap.state === 'MONITOR' || snap.state === 'IDLE') visited.current = new Set();
      lastState.current = snap.state;
      setAnnounce(`State ${snap.state}.`);
    }
    visited.current.add(snap.state);
  }, [snap.state]);

  const act = useCallback((r: { ok: boolean; reason: string }) => {
    if (!r.ok) setToast(r.reason);
    else setToast(null);
    setSnap(engine.snapshot());
  }, [engine]);

  const chain = useMemo(() => verifyChain(records), [records]);
  const selectedIdx = records.findIndex((r) => r.event_id === selected);
  const busy = snap.scenario !== null || (snap.state !== 'IDLE' && snap.state !== 'MONITOR' && snap.state !== 'SAFE_STOP');
  const inStop = snap.state === 'SAFE_STOP';
  const logLines = engine.log.slice(-80).reverse();
  const c = snap.counters;

  return (
    <>
      <div className="sim-banner" role="note">
        SIMULATION — synthetic data, no hardware connected
      </div>
      <div className="wrap">
        <header className="top">
          <div className="brand">
            <div className="logo" aria-hidden="true">H</div>
            <div>
              <div className="eyebrow">Physical AI · Prototype · Simulation</div>
              <h1 className="title">HookPilot</h1>
              <p className="subtitle">Bite detection (deterministic-baseline-v1), independent safety controller, simulated hook-set and retrieval actuators, hash-chained audit log.</p>
            </div>
          </div>
          <div className="pills">
            <span className="pill">50 Hz synthetic signal</span>
            <span className="pill">Thresholds: provisional</span>
            <span className="pill">No hardware · No Konnex integration</span>
          </div>
        </header>

        <div className="sr-only" aria-live="polite" role="status">{announce}</div>
        {toast && (
          <div className="notice" role="alert" style={{ marginBottom: 12 }}>
            {toast}{' '}
            <button className="btn sm" onClick={() => setToast(null)}>Dismiss</button>
          </div>
        )}

        <div className="grid">
          {/* ---------------- live tension ---------------- */}
          <section className="card" aria-labelledby="h-tension">
            <div className="card-head">
              <h2 id="h-tension">Live line tension</h2>
              <span className={`status ${stateKind(snap.state)}`}>
                <span className="dot" aria-hidden="true" />State: {snap.state}
              </span>
            </div>
            <div className="row" style={{ alignItems: 'end', marginBottom: 12, flexWrap: 'wrap' }}>
              <div>
                <div className="metric" aria-label="Current tension">
                  {snap.tension_n === null ? '— ' : snap.tension_n.toFixed(2)} <span className="unit">N</span>
                </div>
                <div className="muted small">
                  Simulated sensor · health <strong className={snap.sensor_health === 'ok' ? 'ok' : 'bad'}>{snap.sensor_health}</strong>
                </div>
              </div>
              <div className="small" style={{ textAlign: 'right' }}>
                <div>baseline <strong>{snap.baseline_n.toFixed(2)} N</strong></div>
                <div>delta <strong>{snap.delta_n.toFixed(2)} N</strong> · rate <strong>{snap.rate_n_per_s.toFixed(1)} N/s</strong></div>
                <div className="muted">detector: {snap.detector_phase}</div>
              </div>
            </div>
            <TensionChart points={engine.chart} threshold={threshold} forceLimit={forceLimit} frame={frame} />
            <div className="legend" aria-hidden="true">
              <span><i style={{ background: '#4ee0a0' }} />tension</span>
              <span><i style={{ background: '#7bb8ff' }} />baseline (EMA, frozen in candidate)</span>
              <span><i style={{ background: '#ffcc66' }} />trigger = baseline + threshold</span>
              <span><i style={{ background: '#ff7777' }} />force limit</span>
              <span>shading = state</span>
            </div>
            <div className="two">
              <div className="field">
                <div className="row">
                  <label htmlFor="threshold">Bite threshold (trigger delta)</label>
                  <strong>{threshold.toFixed(1)} N</strong>
                </div>
                <input
                  id="threshold"
                  type="range"
                  min={0.4}
                  max={4}
                  step={0.1}
                  value={threshold}
                  aria-valuetext={`${threshold.toFixed(1)} newtons`}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    setThreshold(v);
                    engine.setTriggerThreshold(v);
                  }}
                />
                <div className="prov">provisional – derive from calibration + labeled data</div>
              </div>
              <div className="field">
                <div className="row">
                  <label htmlFor="force">Retrieval force limit</label>
                  <strong>{forceLimit.toFixed(1)} N</strong>
                </div>
                <input
                  id="force"
                  type="range"
                  min={2}
                  max={15}
                  step={0.5}
                  value={forceLimit}
                  aria-valuetext={`${forceLimit.toFixed(1)} newtons`}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    setForceLimit(v);
                    engine.setForceLimit(v);
                  }}
                />
                <div className="prov">provisional – hard max {engine.config.safety.hard_max_tension_n} N in any state</div>
              </div>
            </div>
          </section>

          {/* ---------------- control + safety ---------------- */}
          <section className="card" aria-labelledby="h-control">
            <div className="card-head">
              <h2 id="h-control">Control &amp; safety</h2>
              <span className={`status ${snap.armed ? 'good' : 'warn'}`}>
                <span className="dot" aria-hidden="true" />
                {snap.armed ? 'Armed' : 'Disarmed'}
              </span>
            </div>
            <div className="two">
              <button className="btn primary" onClick={() => act(engine.arm())} disabled={snap.armed || inStop}>Arm</button>
              <button className="btn" onClick={() => act(engine.disarm())} disabled={!snap.armed}>Disarm</button>
            </div>
            <div className="card-head" style={{ marginTop: 14, marginBottom: 8 }}>
              <strong className="small">Safety interlocks</strong>
              <span className={`status ${inStop ? 'bad' : snap.live_faults.length ? 'bad' : 'good'}`}>
                <span className="dot" aria-hidden="true" />
                {inStop ? 'SAFE_STOP' : snap.live_faults.length ? 'Fault' : 'Interlocks OK'}
              </span>
            </div>
            <ul className="interlocks">
              {Object.entries(snap.interlocks).map(([k, v]) => (
                <li key={k}>
                  <span className={v ? 'ok' : k === 'armed' ? 'warn' : 'bad'} aria-hidden="true">{v ? '●' : '○'}</span>
                  <span>
                    {INTERLOCK_LABEL[k] ?? k}: <strong>{v ? 'ok' : k === 'armed' ? 'no' : 'NOT OK'}</strong>
                  </span>
                </li>
              ))}
            </ul>
            <p className="small" style={{ margin: '10px 0' }}>
              Live faults: <strong className={snap.live_faults.length ? 'bad' : 'ok'}>{snap.live_faults.join(', ') || 'none'}</strong>
              {' · '}Latched: <strong className={snap.latched_faults.length ? 'bad' : 'ok'}>{snap.latched_faults.join(', ') || 'none'}</strong>
            </p>
            <button className="btn danger" style={{ width: '100%' }} onClick={() => act(engine.resetToSafe())}>
              Reset to safe state
            </button>
            <p className="muted small" style={{ marginBottom: 0 }}>
              The classifier can only request actuation; the safety controller permits or denies. SAFE_STOP exits only via reset once faults clear, into IDLE (disarmed).
            </p>
          </section>

          {/* ---------------- state machine ---------------- */}
          <section className="card full" aria-labelledby="h-sm">
            <div className="card-head">
              <h2 id="h-sm">State machine</h2>
              <span className="muted small">t = {(snap.t_ms / 1000).toFixed(2)} s (simulated clock)</span>
            </div>
            <ol className="stepper" aria-label="State machine">
              {STATES.map((s) => (
                <li
                  key={s}
                  className={`${snap.state === s ? 'active' : ''} ${s === 'SAFE_STOP' ? 'stop' : ''} ${visited.current.has(s) && snap.state !== s ? 'visited' : ''}`}
                  aria-current={snap.state === s ? 'step' : undefined}
                >
                  {s}
                  <small>{STATE_HELP[s]}</small>
                </li>
              ))}
            </ol>
            <div className="tele">
              {[snap.hook_set, snap.retrieval].map((a) => {
                const target = a.actuator === 'hook_set' ? engine.config.actuators.hook_set_target_mm : engine.config.actuators.retrieval_target_m;
                return (
                  <div className="box" key={a.actuator}>
                    <div className="row">
                      <strong>{a.actuator === 'hook_set' ? 'Hook-set actuator (sim)' : 'Retrieval actuator (sim)'}</strong>
                      <span className={a.status === 'jammed' || a.status === 'stopped' ? 'bad' : a.active ? 'ok' : 'muted'}>{a.status}</span>
                    </div>
                    <div className="muted">
                      position {a.position.toFixed(a.actuator === 'hook_set' ? 1 : 2)} {a.actuator === 'hook_set' ? 'mm' : 'm'} · current {a.current_a.toFixed(2)} A
                    </div>
                    <div className="bar" aria-hidden="true">
                      <span style={{ width: `${Math.min(100, (a.position / target) * 100)}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* ---------------- scenarios ---------------- */}
          <section className="card full" aria-labelledby="h-scen">
            <div className="card-head">
              <h2 id="h-scen">Scenarios (synthetic)</h2>
              <span className="muted small">
                {snap.scenario ? `Running "${snap.scenario.name}" seed ${snap.scenario.seed} · ${Math.round(snap.scenario.progress * 100)}%` : 'Idle water (synthetic noise)'}
              </span>
            </div>
            <div className="seedrow">
              <label>
                Seed (reproducible)
                <input type="number" value={seed} step={1} onChange={(e) => setSeed(Number.parseInt(e.target.value || '0', 10) || 0)} />
              </label>
              <button className="btn sm" onClick={() => setSeed(Math.floor(Math.random() * 1e6))}>Random seed</button>
              <label>
                Playback speed
                <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                  <option value={0.25}>0.25×</option>
                  <option value={0.5}>0.5×</option>
                  <option value={1}>1× (real time)</option>
                  <option value={2}>2×</option>
                </select>
              </label>
              <button className="btn sm" onClick={() => setPaused((p) => !p)} aria-pressed={paused}>{paused ? 'Resume' : 'Pause'}</button>
              <span className="muted small">Same scenario + seed → same signal, same decision, same record hash (for a fixed clock origin, see CLI).</span>
            </div>
            <div className="controls">
              {SCENARIO_BUTTONS.map((b) => (
                <button key={b.id} className={`btn ${b.kind}`} disabled={busy || inStop} onClick={() => act(engine.startScenario(b.id, seed))}>
                  {b.label}
                </button>
              ))}
            </div>
            <p className="muted small" style={{ marginBottom: 0 }}>
              Noise, vibration and handling scenarios must never command an actuator. Arm first to see the full hook-set / retrieval cycle (a completed cycle ends disarmed, like the firmware: re-arm for the next one); while disarmed, detections are logged as observation only and nothing is requested.
            </p>
          </section>

          {/* ---------------- counters ---------------- */}
          <section className="card full" aria-labelledby="h-count">
            <div className="card-head">
              <h2 id="h-count">Event counters</h2>
              <span className={`status ${chain.ok ? 'good' : 'bad'}`}>
                <span className="dot" aria-hidden="true" />
                Hash chain {chain.ok ? `verified (${records.length})` : `BROKEN at #${chain.index}`}
              </span>
            </div>
            <div className="kpis">
              {[
                ['Events logged', c.events],
                ['Bites confirmed', c.confirmed],
                ['Rejected', c.rejected],
                ['No event', c.no_event],
                ['Hook-set cycles', c.hook_set_cycles],
                ['Retrievals done', c.retrievals_completed],
                ['Denied (confirmed)', c.denied],
                ['Safety stops', c.safety_stops],
              ].map(([l, v]) => (
                <div className="kpi" key={l as string}>
                  <div className="lbl">{l}</div>
                  <div className="metric">{v}</div>
                </div>
              ))}
            </div>
          </section>

          {/* ---------------- history ---------------- */}
          <section className="card full" aria-labelledby="h-hist">
            <div className="card-head">
              <h2 id="h-hist">Execution history</h2>
              <div className="pills">
                <button className="btn sm" disabled={!records.length} onClick={() => download(`hookpilot-sim-events-${Date.now()}.json`, toJson(records), 'application/json')}>
                  Export JSON
                </button>
                <button className="btn sm" disabled={!records.length} onClick={() => download(`hookpilot-sim-events-${Date.now()}.csv`, toCsv(records), 'text/csv')}>
                  Export CSV
                </button>
              </div>
            </div>
            {records.length === 0 ? (
              <p className="muted">No events yet. Run a scenario above.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <caption className="sr-only">Execution history; select a row to open the event detail</caption>
                  <thead>
                    <tr>
                      <th scope="col">#</th>
                      <th scope="col">Start (sim clock)</th>
                      <th scope="col">Scenario / seed</th>
                      <th scope="col">Classification</th>
                      <th scope="col">Decision reason</th>
                      <th scope="col">Safety</th>
                      <th scope="col">Actuation</th>
                      <th scope="col">Final state</th>
                      <th scope="col">Hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...records].reverse().map((r) => {
                      const idx = records.indexOf(r) + 1;
                      return (
                        <tr
                          key={r.event_id}
                          tabIndex={0}
                          onClick={() => setSelected(r.event_id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setSelected(r.event_id);
                            }
                          }}
                          aria-label={`Event ${idx}: ${r.classification.result}, safety ${r.safety.decision}. Open details.`}
                        >
                          <td>{idx}</td>
                          <td className="mono">{r.timestamp.start_utc.slice(11, 23)}</td>
                          <td>{r.source.scenario ?? '-'} / {r.source.seed ?? '-'}</td>
                          <td><span className={`status ${resultKind(r)}`}>{r.classification.result}</span></td>
                          <td className="reason">{r.classification.reason}</td>
                          <td className={r.safety.decision === 'permit' ? 'ok' : r.safety.decision === 'deny' ? 'warn' : 'bad'}>
                            {r.safety.decision}
                            {(r.safety.faults ?? []).length ? ` (${r.safety.faults!.join(', ')})` : ''}
                          </td>
                          <td>{(r.actuation.requested ?? []).map((a) => a.actuator).join(' → ') || 'none'}</td>
                          <td>{r.outcome.final_state}</td>
                          <td className="mono">{r.integrity.record_sha256.slice(0, 10)}…</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ---------------- engine log ---------------- */}
          <section className="card full" aria-labelledby="h-log">
            <div className="card-head">
              <h2 id="h-log">Engine log</h2>
              <span className="muted small">newest first · local, in-memory</span>
            </div>
            <pre className="log" tabIndex={0} aria-label="Engine log">
              {logLines.map((l, i) => (
                <div key={i} className={l.level}>
                  [{(l.t_ms / 1000).toFixed(2)}s] {l.text}
                </div>
              ))}
            </pre>
          </section>
        </div>

        <p className="foot">
          SIMULATION ONLY. All tension values, actuator positions and currents are synthetic and generated in your browser from (scenario, seed) with the
          shared hookpilot-synth-v1 generator (mulberry32 PRNG) and classified with the same deterministic-baseline-v1 rules and default thresholds as the
          firmware and the Python evidence service. Thresholds and limits are provisional placeholders, not validated values. Nothing here controls a real rod, reel, motor or sensor.
          Konnex integration is not implemented in this demo.
        </p>
      </div>

      {selected && selectedIdx >= 0 && (
        <EventDrawer record={records[selectedIdx]} prev={selectedIdx > 0 ? records[selectedIdx - 1] : null} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
