# Canonical hashing (implementation notes for the contract rule)

Contract: sha256 over JSON with keys sorted recursively, separators `,` `:`, with
`integrity.record_sha256 = ""` during hashing.

Details needed for TS / C++ / Python to agree (implemented in
`services/evidence-service/evidence_service/canonical.py` and `packages/event-schema/validate.py`):

1. **Numbers** are written exactly as ECMAScript `JSON.stringify` does: shortest round-trip digits;
   integral values without a fraction (`1.0 → 1`); exponent form only for |x| ≥ 1e21 or < 1e-6
   (`1e-7`, `1e+21`); `-0 → 0`. NaN/Infinity are rejected.
2. **Strings** as UTF-8 without `\u` escaping of non-ASCII (JSON.stringify behaviour); standard
   escaping of `"`, `\` and control characters.
3. **Keys** sorted by code point (identical to JS default sort for the ASCII keys used).
4. Only `integrity.record_sha256` is blanked; `prev_record_sha256`, `signature` and any extra
   integrity fields (e.g. `sealed_by`) are hashed.
5. Chain: first record of a log has `prev_record_sha256 = null`; each next record carries the previous
   record's `record_sha256`. A firmware restart may start a new segment (prev = null); the edge bridge
   logs this instead of treating it as tampering.

Cross-checks performed (2026-10-18): Python formatting equals Node.js `String(x)` on 9,512 random
doubles; records produced by the TS web demo (6 scenarios) and by the C++ firmware sim (32 records)
verify byte-for-byte under the Python implementation.
