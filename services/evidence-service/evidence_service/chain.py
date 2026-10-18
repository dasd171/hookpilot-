"""Hash-chain + raw-data integrity verification."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .canonical import file_sha256, record_hash
from .schema import validate_record


def load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: invalid JSON: {e}") from e
    return out


@dataclass
class RecordCheck:
    index: int
    event_id: str
    schema_errors: list[str] = field(default_factory=list)
    hash_ok: bool = False
    computed_sha256: str = ""
    chain_ok: bool = False
    raw_ok: bool | None = None  # None = no raw ref / not checked
    raw_detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.schema_errors and self.hash_ok and self.chain_ok and self.raw_ok is not False

    def to_dict(self) -> dict:
        return {"index": self.index, "event_id": self.event_id, "ok": self.ok,
                "schema_errors": self.schema_errors, "hash_ok": self.hash_ok,
                "computed_sha256": self.computed_sha256, "chain_ok": self.chain_ok,
                "raw_ok": self.raw_ok, "raw_detail": self.raw_detail}


def verify_records(records: list[dict], raw_base_dir: str | None = None,
                   genesis_prev: str | None = None) -> list[RecordCheck]:
    """Verify schema, record hash, chain linkage (prev == previous record hash;
    first record's prev must equal genesis_prev, default null) and raw file sha256."""
    checks = []
    prev_expected = genesis_prev
    for i, r in enumerate(records):
        c = RecordCheck(i, str(r.get("event_id", "?")))
        c.schema_errors = validate_record(r)
        integ = r.get("integrity", {}) or {}
        try:
            c.computed_sha256 = record_hash(r)
        except (TypeError, ValueError) as e:
            c.schema_errors.append(f"canonicalisation failed: {e}")
        c.hash_ok = bool(c.computed_sha256) and integ.get("record_sha256") == c.computed_sha256
        c.chain_ok = integ.get("prev_record_sha256") == prev_expected
        prev_expected = integ.get("record_sha256")
        sd = r.get("sensor_data", {}) or {}
        ref, want = sd.get("raw_data_ref"), sd.get("raw_data_sha256")
        if ref:
            if raw_base_dir is None:
                c.raw_ok, c.raw_detail = None, "raw_data_ref present but no raw base dir given"
            else:
                p = os.path.join(raw_base_dir, ref)
                if not os.path.exists(p):
                    c.raw_ok, c.raw_detail = False, f"missing raw file {ref}"
                else:
                    got = file_sha256(p)
                    c.raw_ok = got == want
                    c.raw_detail = "sha256 match" if c.raw_ok else f"sha256 mismatch: file {got} != record {want}"
        checks.append(c)
    return checks
