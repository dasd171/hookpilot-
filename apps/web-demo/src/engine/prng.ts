/**
 * mulberry32 PRNG - identical algorithm in TS, Python and C++ (see SHARED_CONTRACT.md).
 *
 *   a |= 0; a = a + 0x6D2B79F5 | 0;
 *   t = Math.imul(a ^ a >>> 15, 1 | a);
 *   t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
 *   return ((t ^ t >>> 14) >>> 0) / 4294967296;
 *
 * Helper conventions (match services/evidence-service prng.py):
 *   uniform(lo, hi) consumes exactly one draw;
 *   gauss() = Box-Muller cosine branch, no caching, consumes exactly two draws, u1 clamped to 1e-12.
 */
export class Mulberry32 {
  private a: number;
  constructor(seed: number) {
    this.a = seed | 0;
  }
  nextU32(): number {
    this.a = (this.a + 0x6d2b79f5) | 0;
    let t = Math.imul(this.a ^ (this.a >>> 15), 1 | this.a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return (t ^ (t >>> 14)) >>> 0;
  }
  next(): number {
    return this.nextU32() / 4294967296;
  }
  uniform(lo: number, hi: number): number {
    return lo + (hi - lo) * this.next();
  }
  gauss(): number {
    let u1 = this.next();
    const u2 = this.next();
    if (u1 < 1e-12) u1 = 1e-12;
    return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  }
}

export function mulberry32(seed: number): () => number {
  const g = new Mulberry32(seed);
  return () => g.next();
}
