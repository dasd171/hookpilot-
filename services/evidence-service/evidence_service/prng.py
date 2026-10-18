"""mulberry32 PRNG — bit-identical port of the reference JavaScript algorithm.

Reference (JavaScript, the contract's canonical form):

    function mulberry32(a) {
      return function () {
        a |= 0; a = a + 0x6D2B79F5 | 0;
        var t = Math.imul(a ^ a >>> 15, 1 | a);
        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
        return ((t ^ t >>> 14) >>> 0) / 4294967296;
      };
    }

All arithmetic is done on unsigned 32-bit integers; the low 32 bits of every
intermediate are identical to the JS int32 result, so outputs match exactly.
Test vectors (computed with Node.js) live in tests/unit/test_prng.py.
"""
from __future__ import annotations

import math

MASK32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return (a * b) & MASK32


class Mulberry32:
    """Stateful mulberry32 generator. ``next()`` returns a float in [0, 1)."""

    __slots__ = ("state",)

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK32

    def next_u32(self) -> int:
        self.state = (self.state + 0x6D2B79F5) & MASK32
        t = self.state
        t = _imul(t ^ (t >> 15), t | 1)
        t = (((t + _imul(t ^ (t >> 7), t | 61)) & MASK32) ^ t) & MASK32
        return (t ^ (t >> 14)) & MASK32

    def next(self) -> float:
        return self.next_u32() / 4294967296.0

    # --- helpers defined by docs/synthetic-signals.md (hookpilot-synth-v1) ---
    def uniform(self, lo: float, hi: float) -> float:
        """lo + (hi - lo) * u  (consumes exactly one draw)."""
        return lo + (hi - lo) * self.next()

    def gauss(self) -> float:
        """Box-Muller, cosine branch only, no caching: consumes exactly two draws."""
        u1 = self.next()
        u2 = self.next()
        if u1 < 1e-12:
            u1 = 1e-12
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def mulberry32(seed: int):
    """Functional form mirroring the JS reference: returns a zero-arg callable."""
    g = Mulberry32(seed)
    return g.next
