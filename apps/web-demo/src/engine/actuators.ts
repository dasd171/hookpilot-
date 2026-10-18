/**
 * Simulated actuator models (SIMULATION - no hardware). Line-by-line ports of the firmware host mocks
 * (firmware/host/mocks.cpp MockHookActuator / MockRetrieval), identical to evidence_service.engine.
 * hook_set: servo stroke in mm; retrieval: line wound in metres. Kinematic placeholders, not physics.
 */
import type { SimCoupling } from './config';

export interface ActuatorFeedback {
  /** hook set: mm of stroke; retrieval: m of line retrieved */
  position: number;
  current_a: number;
  /** mm/s or m/s */
  speed: number;
  /** energised and moving toward a target */
  active: boolean;
  at_target: boolean;
  position_valid: boolean;
  current_valid: boolean;
  status: string;
}

export function newFeedback(): ActuatorFeedback {
  return { position: 0, current_a: 0, speed: 0, active: false, at_target: false, position_valid: true, current_valid: true, status: 'idle' };
}

/** Servo hook-set actuator: position (mm), slew at commanded speed, current feedback. */
export class MockHook {
  fb: ActuatorFeedback = newFeedback();
  private target = 0;
  private speed = 0;
  private lastMs = 0;
  private haveLast = false;
  jam = false;
  fbLoss = false;
  overshoot = 0;
  moves = 0;
  constructor(public toleranceMm = 0.5) {}

  moveTo(pos: number, speed: number) {
    this.target = pos;
    this.speed = speed;
    this.fb.active = true;
    this.fb.at_target = false;
    this.fb.status = 'moving';
    this.moves++;
  }
  stop() {
    this.fb.active = false;
    this.fb.speed = 0;
    this.fb.current_a = 0;
    if (this.fb.status !== 'idle') this.fb.status = 'stopped';
  }
  update(now: number) {
    const dt = this.haveLast ? (now - this.lastMs) / 1000.0 : 0.0;
    this.lastMs = now;
    this.haveLast = true;
    const fb = this.fb;
    fb.position_valid = !this.fbLoss;
    if (!fb.active) {
      fb.current_a = 0.05;
      fb.speed = 0;
      return;
    }
    if (this.jam) {
      fb.speed = 0;
      fb.current_a = 1.6;
      fb.status = 'jammed';
      return;
    }
    const dir = this.target >= fb.position ? 1.0 : -1.0;
    const step = this.speed * dt;
    const remaining = Math.abs(this.target - fb.position);
    if (this.overshoot > 0 && this.target > 0) {
      fb.position += step;
      if (fb.position >= this.target + this.overshoot) fb.position = this.target + this.overshoot;
    } else if (step >= remaining) {
      fb.position = this.target;
    } else {
      fb.position += dir * step;
    }
    fb.speed = this.speed;
    fb.current_a = 0.8;
    if (Math.abs(fb.position - this.target) <= this.toleranceMm && this.overshoot <= 0) {
      fb.active = false;
      fb.at_target = true;
      fb.speed = 0;
      fb.current_a = 0.1;
      fb.status = 'at_target';
    }
  }
}

/** DC motor + encoder + current-sense retrieval: position (m of line), current (A). */
export class MockRetrieval {
  fb: ActuatorFeedback = newFeedback();
  private target = 0;
  private speed = 0;
  loadN = 0;
  private lastMs = 0;
  private haveLast = false;
  stall = false;
  encLoss = false;
  runaway = false;
  starts = 0;
  private baseA: number;
  private perNA: number;
  constructor(c: SimCoupling) {
    this.baseA = c.retrieval_current_base_a;
    this.perNA = c.retrieval_current_per_n_a;
  }

  start(target: number, speed: number) {
    this.target = target;
    this.speed = speed;
    this.fb.active = true;
    this.fb.at_target = false;
    this.fb.status = 'retrieving';
    this.starts++;
  }
  stop() {
    this.fb.active = false;
    this.fb.speed = 0;
    this.fb.current_a = 0;
    if (this.fb.status !== 'idle' && this.fb.status !== 'at_target') this.fb.status = 'stopped';
  }
  resetPosition() {
    this.fb.position = 0;
  }
  update(now: number) {
    const dt = this.haveLast ? (now - this.lastMs) / 1000.0 : 0.0;
    this.lastMs = now;
    this.haveLast = true;
    const fb = this.fb;
    fb.position_valid = !this.encLoss;
    if (!fb.active) {
      fb.current_a = 0;
      fb.speed = 0;
      return;
    }
    if (this.stall) {
      fb.speed = 0;
      fb.current_a = 2.5;
      fb.status = 'stalled';
      return;
    }
    const step = this.speed * dt;
    if (!this.runaway && fb.position + step >= this.target) {
      fb.position = this.target;
      fb.active = false;
      fb.at_target = true;
      fb.speed = 0;
      fb.current_a = 0;
      fb.status = 'at_target';
      return;
    }
    if (!this.encLoss) fb.position += step;
    fb.speed = this.speed;
    fb.current_a = this.baseA + this.perNA * Math.max(0.0, this.loadN);
    fb.status = 'retrieving';
  }
}
