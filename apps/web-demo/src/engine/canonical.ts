import { sha256Hex } from './sha256';
import type { HookPilotEvent } from '../../../../packages/shared-types/src/index';

/**
 * Canonical JSON per SHARED_CONTRACT.md: keys sorted recursively (by UTF-16 code unit, identical to
 * code-point order for the ASCII keys used here), separators ',' and ':', numbers as JSON.stringify,
 * strings not ASCII-escaped. NaN/Infinity are rejected.
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('canonicalJson: non-finite number');
    return JSON.stringify(value);
  }
  if (typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map((v) => canonicalJson(v)).join(',') + ']';
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
  }
  throw new Error(`canonicalJson: unsupported type ${typeof value}`);
}

/** sha256 of canonical JSON of the record with integrity.record_sha256 = "". */
export function recordHash(record: HookPilotEvent): string {
  const copy = JSON.parse(JSON.stringify(record)) as HookPilotEvent;
  copy.integrity = { ...copy.integrity, record_sha256: '' };
  return sha256Hex(canonicalJson(copy));
}

export interface ChainCheck {
  ok: boolean;
  index: number; // first bad index, -1 when ok
  problem: string | null;
}

export function verifyChain(records: HookPilotEvent[]): ChainCheck {
  let prev: string | null = null;
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    if ((r.integrity.prev_record_sha256 ?? null) !== prev) {
      return { ok: false, index: i, problem: 'prev_record_sha256 does not match previous record' };
    }
    const h = recordHash(r);
    if (h !== r.integrity.record_sha256) return { ok: false, index: i, problem: 'record_sha256 mismatch' };
    prev = r.integrity.record_sha256;
  }
  return { ok: true, index: -1, problem: null };
}
