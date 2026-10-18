/**
 * Simulated actuator models (SIMULATION - no hardware). Each produces position/current feedback.
 * hook_set: linear sweep in mm with travel limit; retrieval: line wound in metres.
 */
import type { ActuatorName } from '../../../../packages/shared-types/src/index';
import type { ActuatorParams, SafetyLimits } from './config';
import { DT_MS } from './config';

export type ActuatorStatus = 'idle' | 'moving' | 'complete' | 'stopped' | 'jammed';

export interface ActuatorTelemetry {
  actuator: ActuatorName;
  position: number; // mm for hook_set, m for retrieval
  current_a: number;
  status: ActuatorStatus;
  elapsed_ms: number;
  active: boolean;
}

export class SimActuator {
  readonly name: ActuatorName;
  position = 0;
  current = 0;
  status: ActuatorStatus = 'idle';
  elapsed = 0;
  private target = 0;
  private speedPerMs = 0;
  private jammed = false;
  /** history of (elapsed, position) for stall detection */
  readonly history: Array<[number, number]> = [];

  constructor(name: ActuatorName) {
    this.name = name;
  }

  get active() {
    return this.status === 'moving' || this.status === 'jammed';
  }

  start(target: number, speedPerS: number) {
    this.position = 0;
    this.target = target;
    this.speedPerMs = speedPerS / 1000;
    this.status = 'moving';
    this.elapsed = 0;
    this.jammed = false;
    this.history.length = 0;
  }

  /** inject a mechanical jam (scenario motor_stall) */
  jam() {
    if (this.active) this.jammed = true;
  }

  stop() {
    if (this.active) this.status = 'stopped';
    this.current = 0;
  }

  idle() {
    this.status = 'idle';
    this.current = 0;
    this.position = 0;
    this.elapsed = 0;
    this.history.length = 0;
  }

  /** advance one tick; lineTensionN is the measured tension (load) */
  step(lineTensionN: number, travelLimit: number) {
    if (!this.active) {
      this.current = 0;
      return;
    }
    this.elapsed += DT_MS;
    const load = Number.isFinite(lineTensionN) ? Math.max(0, lineTensionN) : 0;
    if (this.jammed) {
      this.status = 'jammed';
      // current ramps toward stall current while position does not advance
      this.current = Math.min(2.6, this.current + 0.12);
    } else {
      this.position = Math.min(this.target, this.position + this.speedPerMs * DT_MS, travelLimit);
      this.current = (this.name === 'hook_set' ? 0.5 : 0.3) + (this.name === 'hook_set' ? 0.08 : 0.15) * load;
      if (this.position >= this.target) {
        this.status = 'complete';
        this.current = 0.05;
      }
    }
    this.history.push([this.elapsed, this.position]);
    if (this.history.length > 50) this.history.shift();
  }

  /** extra line tension (N) this actuator adds while moving */
  lineLoad(p: ActuatorParams): number {
    if (this.status !== 'moving' && this.status !== 'jammed') return 0;
    return this.name === 'hook_set' ? p.hook_set_line_load_n : p.retrieval_line_load_n;
  }

  telemetry(): ActuatorTelemetry {
    return {
      actuator: this.name,
      position: this.position,
      current_a: this.current,
      status: this.status,
      elapsed_ms: this.elapsed,
      active: this.active,
    };
  }

  travelLimit(lim: SafetyLimits): number {
    return this.name === 'hook_set' ? lim.hook_set_travel_limit_mm : Number.POSITIVE_INFINITY;
  }
}
