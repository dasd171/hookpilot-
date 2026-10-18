/**
 * Independent safety controller + actuator gate - SIMULATION.
 *
 * Line-by-line port of firmware DefaultSafetyController / ActuatorGate (firmware/src/core/safety_controller.cpp),
 * identical to evidence_service.safety. It never sees classifier output: the classifier can only REQUEST an
 * actuation through the gate, this controller PERMITs or DENIEs, latches faults (with the tick they latched)
 * and the state machine enters SAFE_STOP on any critical fault. Limits are configurable placeholders
 * (provisional - derive from calibration + labeled data). See docs/engine-spec.md.
 */
import type { FaultCode, SensorHealth } from '../../../../packages/shared-types/src/index';
import type { SafetyLimits } from './config';
import type { ActuatorFeedback, MockHook, MockRetrieval } from './actuators';
import { cfix } from './fmt';

export const FAULT_NAMES: FaultCode[] = [
  'E_STOP',
  'OVER_TENSION',
  'SENSOR_DISCONNECTED',
  'SENSOR_OUT_OF_RANGE',
  'SENSOR_STUCK',
  'MOTOR_STALL',
  'ACTUATOR_TIMEOUT',
  'TRAVEL_LIMIT',
  'COMM_LOSS',
  'UNDERVOLTAGE',
  'WATCHDOG_RESET',
  'OVER_TEMP',
  'INTERLOCK',
];
export const FB: Record<FaultCode, number> = Object.fromEntries(FAULT_NAMES.map((n, i) => [n, 1 << i])) as Record<FaultCode, number>;
export const CRITICAL_MASK = ~FB.WATCHDOG_RESET;

export function faultList(mask: number): FaultCode[] {
  return FAULT_NAMES.filter((_, i) => (mask & (1 << i)) !== 0);
}
export function joinFaults(mask: number, empty = ''): string {
  const s = faultList(mask).join(',');
  return s || empty;
}

/** Subset of a processed sample the safety controller reads. */
export interface SafetySample {
  raw: number;
  valid: boolean;
  health: SensorHealth;
}

/** Everything the safety controller needs, gathered by the controller each tick (firmware SafetyInputs). */
export interface SafetyInputs {
  now_ms: number;
  sample: SafetySample;
  hook: ActuatorFeedback;
  retrieval: ActuatorFeedback;
  bus_voltage_v: number;
  temperature_c: number;
  estop_active: boolean;
  comm_link_ok: boolean;
  last_heartbeat_ms: number;
}

export interface SafetyDecisionResult {
  permitted: boolean;
  reason: string;
}

const f3 = (v: number) => cfix(v, 3);
const K_HIST = 64;

export class SafetyController {
  readonly lim: SafetyLimits;
  armed = false;
  latched = 0;
  active = 0;
  private sensorBad = 0;
  private lastBadHealth: SensorHealth = 'ok';
  private overCount = 0;
  private uvPending = false;
  private uvSince = 0;
  private hookWasActive = false;
  private retrWasActive = false;
  private hookSince = 0;
  private retrSince = 0;
  private hookRan = false;
  private hookIdleSince = 0;
  private lastNow = 0;
  readonly latchT: number[] = FAULT_NAMES.map(() => 0);
  private histT: number[] = new Array(K_HIST).fill(0);
  private histP: number[] = new Array(K_HIST).fill(0);
  private histN = 0;
  private histHead = 0;

  constructor(lim: SafetyLimits) {
    this.lim = lim;
  }

  private evaluate(i: SafetyInputs): number {
    const L = this.lim;
    let c = 0;
    const s = i.sample;
    const now = i.now_ms;
    if (i.estop_active) c |= FB.E_STOP;
    if (!s.valid) {
      this.sensorBad++;
      this.lastBadHealth = s.health;
    } else {
      this.sensorBad = 0;
    }
    if (this.sensorBad >= L.sensor_fault_samples) {
      if (this.lastBadHealth === 'out_of_range') c |= FB.SENSOR_OUT_OF_RANGE;
      else if (this.lastBadHealth === 'stuck') c |= FB.SENSOR_STUCK;
      else c |= FB.SENSOR_DISCONNECTED;
    }
    let over = false;
    if (s.valid) {
      if (s.raw > L.hard_max_tension_n) over = true;
      if (i.retrieval.active && s.raw > L.retrieval_force_limit_n) over = true;
    }
    this.overCount = over ? this.overCount + 1 : 0;
    if (this.overCount >= L.over_tension_samples) c |= FB.OVER_TENSION;
    if (i.hook.active) {
      if (!this.hookWasActive) this.hookSince = now;
      if (now - this.hookSince > L.hook_set_timeout_ms) c |= FB.ACTUATOR_TIMEOUT;
    } else if (this.hookWasActive) {
      this.hookRan = true;
      this.hookIdleSince = now;
    }
    this.hookWasActive = i.hook.active;
    if (i.retrieval.active) {
      if (!this.retrWasActive) {
        this.retrSince = now;
        this.histN = this.histHead = 0;
      }
      if (now - this.retrSince > L.retrieval_timeout_ms) c |= FB.ACTUATOR_TIMEOUT;
    }
    this.retrWasActive = i.retrieval.active;
    if (i.hook.position_valid && (i.hook.position > L.hook_set_travel_limit_mm || i.hook.position < L.hook_set_travel_min_mm - 1.0))
      c |= FB.TRAVEL_LIMIT;
    if (i.retrieval.position_valid && i.retrieval.position > L.retrieval_travel_limit_m) c |= FB.TRAVEL_LIMIT;
    if (i.retrieval.active) {
      this.histT[this.histHead] = now;
      this.histP[this.histHead] = i.retrieval.position;
      this.histHead = (this.histHead + 1) % K_HIST;
      if (this.histN < K_HIST) this.histN++;
      if (now - this.retrSince >= L.stall_grace_ms) {
        let progress = -1;
        let progressLong = -1;
        for (let k = 0; k < this.histN; k++) {
          const idx = (this.histHead - 1 - k + K_HIST) % K_HIST;
          const age = now - this.histT[idx];
          if (age >= L.stall_window_ms && progress < 0) progress = i.retrieval.position - this.histP[idx];
          if (age >= 3 * L.stall_window_ms && progressLong < 0) progressLong = i.retrieval.position - this.histP[idx];
        }
        const high = !i.retrieval.current_valid || i.retrieval.current_a >= L.stall_current_a;
        if (progress >= 0 && progress < L.stall_min_progress_m && high) c |= FB.MOTOR_STALL;
        if (progressLong >= 0 && progressLong < L.stall_min_progress_m) c |= FB.MOTOR_STALL;
      }
    }
    if (i.hook.active && i.retrieval.active) c |= FB.INTERLOCK;
    if (!i.comm_link_ok || now - i.last_heartbeat_ms > L.comm_timeout_ms) c |= FB.COMM_LOSS;
    if (i.bus_voltage_v < L.undervoltage_v) {
      if (!this.uvPending) {
        this.uvPending = true;
        this.uvSince = now;
      }
      if (now - this.uvSince >= L.undervoltage_debounce_ms) c |= FB.UNDERVOLTAGE;
    } else {
      this.uvPending = false;
    }
    if (i.temperature_c > L.over_temp_c) c |= FB.OVER_TEMP;
    return c;
  }

  private stamp(newly: number, now: number) {
    for (let b = 0; b < FAULT_NAMES.length; b++) if (newly & (1 << b)) this.latchT[b] = now;
  }

  latchTimeMs(f: FaultCode): number {
    return this.latchT[FAULT_NAMES.indexOf(f)];
  }

  /** Evaluate all interlocks for this tick; returns the latched fault mask. */
  update(i: SafetyInputs): number {
    this.lastNow = i.now_ms;
    this.active = this.evaluate(i);
    let latch = this.active;
    if (!this.armed) latch &= ~FB.COMM_LOSS; // comm loss is only critical while armed (documented)
    this.stamp(latch & ~this.latched, i.now_ms);
    this.latched |= latch;
    if (this.latched) this.armed = false;
    return this.latched;
  }

  permit(actuator: 'hook_set' | 'retrieval', target: number, speed: number, i: SafetyInputs): SafetyDecisionResult {
    const L = this.lim;
    const deny = (reason: string) => ({ permitted: false, reason });
    if (!this.armed) return deny('deny: not armed');
    if (this.latched) return deny('deny: latched faults ' + joinFaults(this.latched));
    if (this.active) return deny('deny: active conditions ' + joinFaults(this.active));
    if (!i.sample.valid) return deny(`deny: sensor not valid (${i.sample.health})`);
    if (i.sample.raw > L.hard_max_tension_n) return deny('deny: tension above hard_max_tension_n');
    if (actuator === 'hook_set') {
      if (i.retrieval.active) return deny('deny: mutual exclusion (retrieval active)');
      if (!(target >= L.hook_set_travel_min_mm && target <= L.hook_set_travel_limit_mm))
        return deny(`deny: hook_set target ${f3(target)} mm outside travel limits`);
      if (!(speed > 0 && speed <= L.hook_set_max_speed_mm_s)) return deny(`deny: hook_set speed ${f3(speed)} mm/s outside limits`);
    } else {
      if (i.hook.active) return deny('deny: mutual exclusion (hook_set active)');
      if (this.hookRan && i.now_ms - this.hookIdleSince < L.hook_to_retrieval_gap_ms)
        return deny(`deny: hook_set stopped ${i.now_ms - this.hookIdleSince} ms ago (< hook_to_retrieval_gap_ms)`);
      if (!(target > 0 && target <= L.retrieval_travel_limit_m)) return deny(`deny: retrieval target ${f3(target)} m outside travel limits`);
      if (!(speed > 0 && speed <= L.retrieval_max_speed_m_s)) return deny(`deny: retrieval speed ${f3(speed)} m/s outside limits`);
      if (i.sample.raw > L.retrieval_force_limit_n) return deny(`deny: tension ${f3(i.sample.raw)} N above retrieval_force_limit_n`);
    }
    return { permitted: true, reason: 'permit: all interlocks clear' };
  }

  arm(i: SafetyInputs): { ok: boolean; reason: string } {
    if (this.latched) return { ok: false, reason: `latched faults ${joinFaults(this.latched)} (explicit reset required)` };
    if (this.active) return { ok: false, reason: `active conditions ${joinFaults(this.active)}` };
    if (!i.sample.valid) return { ok: false, reason: 'sensor not valid' };
    if (i.bus_voltage_v < this.lim.undervoltage_rearm_v) return { ok: false, reason: 'bus voltage below undervoltage_rearm_v' };
    if (i.estop_active) return { ok: false, reason: 'e-stop active' };
    if (i.hook.active || i.retrieval.active) return { ok: false, reason: 'actuator active' };
    this.armed = true;
    return { ok: true, reason: 'armed' };
  }

  disarm() {
    this.armed = false;
  }

  /** Clears latched faults whose condition is gone (WATCHDOG_RESET is acknowledgement-only). */
  clearFaults(): boolean {
    this.latched &= this.active & ~FB.WATCHDOG_RESET;
    return this.latched === 0;
  }

  latchFault(f: number) {
    this.stamp(f & ~this.latched, this.lastNow);
    this.latched |= f;
    this.armed = false;
  }

  get faults(): FaultCode[] {
    return faultList(this.latched);
  }
}

/** The only path from the controller to the actuators (firmware ActuatorGate). stop() is never gated. */
export class ActuatorGate {
  issued = 0;
  denied = 0;
  constructor(
    private safety: SafetyController,
    private hook: MockHook,
    private retr: MockRetrieval,
  ) {}

  request(actuator: 'hook_set' | 'retrieval', target: number, speed: number, i: SafetyInputs): SafetyDecisionResult {
    let d = this.safety.permit(actuator, target, speed, i);
    if (d.permitted) {
      if (actuator === 'hook_set' && this.retr.fb.active) d = { permitted: false, reason: 'deny: gate mutual exclusion (retrieval active)' };
      else if (actuator === 'retrieval' && this.hook.fb.active) d = { permitted: false, reason: 'deny: gate mutual exclusion (hook_set active)' };
    }
    if (!d.permitted) {
      this.denied++;
      return d;
    }
    this.issued++;
    if (actuator === 'hook_set') this.hook.moveTo(target, speed);
    else this.retr.start(target, speed);
    return d;
  }

  stopAll() {
    this.hook.stop();
    this.retr.stop();
  }

  update(now: number) {
    this.hook.update(now);
    this.retr.update(now);
  }
}
