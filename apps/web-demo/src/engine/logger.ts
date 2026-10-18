/** EventLogger: seals schema-compliant records into a sha256 hash chain. */
import type { HookPilotEvent } from '../../../../packages/shared-types/src/index';
import { recordHash } from './canonical';

export type UnsealedEvent = Omit<HookPilotEvent, 'integrity'>;

/** Round to 3 decimals; keeps JSON number text identical across TS / Python / C++ canonicalisers. */
export function r3(v: number): number {
  const r = Math.round(v * 1000) / 1000;
  return Object.is(r, -0) ? 0 : r;
}

export class EventLogger {
  readonly records: HookPilotEvent[] = [];
  private prev: string | null = null;

  get lastHash(): string | null {
    return this.prev;
  }

  append(e: UnsealedEvent): HookPilotEvent {
    const rec: HookPilotEvent = {
      ...e,
      integrity: { hash_algorithm: 'sha256', record_sha256: '', prev_record_sha256: this.prev, signature: null },
    };
    rec.integrity.record_sha256 = recordHash(rec);
    this.prev = rec.integrity.record_sha256;
    this.records.push(rec);
    return rec;
  }

  clear() {
    this.records.length = 0;
    this.prev = null;
  }
}
