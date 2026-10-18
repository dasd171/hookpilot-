/**
 * SafetyController - independent of the classifier. The classifier can only REQUEST actuation;
 * this controller PERMITs or DENIEs, and raises SAFE_STOP on critical faults.
 * Limits are configurable placeholders (provisional - derive from calibration + labeled data).
 */
import type { ActuatorName, FaultCode, MachineState, SensorHealth } from '../../../../packages/shared-types/src/index';
import type { SafetyLimits } from './config';
import type { ActuatorTelemetry } from './actuators';

export interface SafetyInputs {
  t_ms: number;
  state: MachineState;
  armed: boolean;
  health: SensorHealth;
  tension_n: number; // last valid tension
  sensor_valid: boolean;
  heartbeat_age_ms: number;
  supply_v: number;
  e_stop: boolean;
  hook_set: ActuatorTelemetry & { history: ReadonlyArray<[number, number]>; target?: number };
  retrieval: ActuatorTelemetry & { history: ReadonlyArray<[number, number]> };
}

export interface SafetyEvaluation {
  live: FaultCode[];
  latched: FaultCode[];
  critical: boolean;
  interlocks: Record<string, boolean>;
}

export interface PermitResult {
  decision: 'permit' | 'deny';
  reason: string;
  checks: string[];
}

function progressOver(history: ReadonlyArray<[number, number]>, windowMs: number): number | null {
  if (history.length < 2) return null;
  const last = history[history.length - 1];
  if (last[0] < windowMs) return null;
  for (let i = history.length - 1; i >= 0; i--) {
    if (last[0] - history[i][0] >= windowMs) return last[1] - history[i][1];
  }
  return null;
}

export class SafetyController {
  private lim: SafetyLimits;
  private latchedSet = new Set<FaultCode>();

  constructor(lim: SafetyLimits) {
    this.lim = lim;
  }
  setLimits(lim: SafetyLimits) {
    this.lim = lim;
  }
  get latched(): FaultCode[] {
    return [...this.latchedSet];
  }

  liveFaults(i: SafetyInputs): FaultCode[] {
    const L = this.lim;
    const f: FaultCode[] = [];
    if (i.e_stop) f.push('E_STOP');
    if (i.health === 'disconnected') f.push('SENSOR_DISCONNECTED');
    if (i.health === 'out_of_range') f.push('SENSOR_OUT_OF_RANGE');
    if (i.health === 'stuck') f.push('SENSOR_STUCK');
    if (i.sensor_valid && i.tension_n > L.hard_max_tension_n) f.push('OVER_TENSION');
    else if (i.sensor_valid && i.retrieval.active && i.tension_n > L.retrieval_force_limit_n) f.push('OVER_TENSION');
    if (i.heartbeat_age_ms > L.comm_timeout_ms) f.push('COMM_LOSS');
    if (i.supply_v < L.undervoltage_v) f.push('UNDERVOLTAGE');
    if (
      (i.hook_set.active && i.hook_set.elapsed_ms > L.hook_set_timeout_ms) ||
      (i.retrieval.active && i.retrieval.elapsed_ms > L.retrieval_timeout_ms)
    )
      f.push('ACTUATOR_TIMEOUT');
    if (i.hook_set.position > L.hook_set_travel_limit_mm) f.push('TRAVEL_LIMIT');
    const stalled = (a: SafetyInputs['retrieval'], minProgress: number) => {
      if (!a.active || a.current_a < L.stall_current_a) return false;
      const p = progressOver(a.history, L.stall_window_ms);
      return p !== null && p < minProgress;
    };
    if (stalled(i.retrieval, L.stall_min_progress_m) || stalled(i.hook_set, L.stall_min_progress_m * 100)) f.push('MOTOR_STALL');
    return f;
  }

  interlocks(i: SafetyInputs): Record<string, boolean> {
    const L = this.lim;
    return {
      armed: i.armed,
      e_stop_clear: !i.e_stop,
      sensor_valid: i.sensor_valid && i.health === 'ok',
      comm_ok: i.heartbeat_age_ms <= L.comm_timeout_ms,
      tension_below_hard_max: !i.sensor_valid || i.tension_n <= L.hard_max_tension_n,
      tension_below_force_limit: !i.sensor_valid || i.tension_n <= L.retrieval_force_limit_n,
      supply_ok: i.supply_v >= L.undervoltage_v,
      actuators_idle: !i.hook_set.active && !i.retrieval.active,
      no_latched_faults: this.latchedSet.size === 0,
    };
  }

  /** Called every tick. All listed faults are critical: any -> SAFE_STOP, and they latch until reset. */
  evaluate(i: SafetyInputs): SafetyEvaluation {
    const live = this.liveFaults(i);
    for (const f of live) this.latchedSet.add(f);
    return { live, latched: this.latched, critical: live.length > 0, interlocks: this.interlocks(i) };
  }

  /** Arbitrate an actuation request coming from the state machine / classifier. */
  requestPermit(actuator: ActuatorName, i: SafetyInputs): PermitResult {
    const L = this.lim;
    const checks: string[] = [];
    const deny = (reason: string): PermitResult => {
      checks.push(`=> DENY: ${reason}`);
      return { decision: 'deny', reason, checks };
    };
    const ok = (c: string) => checks.push(`${c} -> ok`);

    if (i.state === 'SAFE_STOP') return deny('system is in SAFE_STOP');
    if (!i.armed) return deny('automation is disarmed');
    ok('armed');
    const live = this.liveFaults(i);
    if (live.length) return deny(`active faults: ${live.join(', ')}`);
    if (this.latchedSet.size) return deny(`latched faults need reset: ${this.latched.join(', ')}`);
    ok('no active or latched faults');
    if (!(i.sensor_valid && i.health === 'ok')) return deny(`sensor not valid (${i.health})`);
    ok('sensor valid');
    if (i.heartbeat_age_ms > L.comm_timeout_ms) return deny('host link lost');
    ok('comm link');
    const other = actuator === 'hook_set' ? i.retrieval : i.hook_set;
    const self = actuator === 'hook_set' ? i.hook_set : i.retrieval;
    if (other.active) return deny(`${other.actuator} is active: hook-set and retrieval are never commanded simultaneously`);
    ok('other actuator idle (no simultaneous hook-set + retrieval)');
    if (self.active) return deny(`${actuator} already active`);
    if (actuator === 'hook_set') {
      if (i.state !== 'CONFIRM') return deny(`hook_set only allowed from CONFIRM (state ${i.state})`);
      if (i.tension_n > L.hard_max_tension_n) return deny(`tension ${i.tension_n.toFixed(2)} N above hard max`);
      ok(`tension ${i.tension_n.toFixed(2)} N <= hard max ${L.hard_max_tension_n} N`);
    } else {
      if (i.state !== 'HOOK_SET' || i.hook_set.status !== 'complete') return deny('retrieval only allowed after a completed hook-set');
      if (i.tension_n > L.retrieval_force_limit_n)
        return deny(`tension ${i.tension_n.toFixed(2)} N above retrieval force limit ${L.retrieval_force_limit_n} N`);
      ok(`tension ${i.tension_n.toFixed(2)} N <= force limit ${L.retrieval_force_limit_n} N`);
    }
    checks.push('=> PERMIT');
    return { decision: 'permit', reason: `${actuator} permitted within limits`, checks };
  }

  /** Reset is only possible when no live fault remains. Clears latched faults on success. */
  tryReset(i: SafetyInputs): { ok: boolean; live: FaultCode[] } {
    const live = this.liveFaults(i);
    if (live.length) return { ok: false, live };
    this.latchedSet.clear();
    return { ok: true, live };
  }

  clearAll() {
    this.latchedSet.clear();
  }
}
