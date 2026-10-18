"""Edge bridge: read firmware JSON lines (e.g. `make sim | python -m edge_controller`) from a stream.

Line handling (the firmware line protocol is not frozen yet, so the bridge is tolerant):
  * JSON object with "schema_version"                 -> HookPilot event record
  * JSON array of event records                       -> each element handled as a record
  * JSON object with "type"/"kind" (telemetry, log, state, heartbeat, ...) -> counted, optionally echoed
  * anything else (plain text / malformed JSON)       -> counted as text, echoed to stderr with --echo

For every event record the bridge:
  1. validates it against the shared schema,
  2. recomputes the canonical hash; if the record is unsealed (record_sha256 == "")
     it is sealed into the bridge's chain ONLY with --seal (then the HOST is the
     attesting party, which is recorded in integrity.sealed_by),
  3. checks chain continuity against the previous accepted record,
  4. appends it verbatim to the evidence JSONL store,
  5. forwards it to MockKonnexAdapter.ingest_record (SIMULATION) when a job is configured.
Records are never modified except by explicit --seal. Invalid records are still stored
(in <store>.rejected.jsonl) so nothing is silently dropped.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import IO, Iterable

from evidence_service.canonical import record_hash, seal
from evidence_service.schema import validate_record


@dataclass
class BridgeStats:
    lines: int = 0
    records: int = 0
    accepted: int = 0
    rejected: int = 0
    sealed_by_host: int = 0
    chain_breaks: int = 0
    hash_mismatches: int = 0
    telemetry: int = 0
    text_lines: int = 0
    forwarded: int = 0
    by_result: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


class Bridge:
    def __init__(self, store_path: str, adapter=None, job_id: str | None = None, seal_unsealed: bool = False,
                 echo: bool = False, err: IO = sys.stderr, raw_dir: str | None = None) -> None:
        self.raw_dir = raw_dir
        self.store_path = store_path
        self.rejected_path = store_path + ".rejected.jsonl"
        self.adapter = adapter
        self.job_id = job_id
        self.seal_unsealed = seal_unsealed
        self.echo = echo
        self.err = err
        self.stats = BridgeStats()
        self.prev: str | None = None
        os.makedirs(os.path.dirname(os.path.abspath(store_path)), exist_ok=True)
        if os.path.exists(store_path):
            with open(store_path, encoding="utf-8") as f:
                last = None
                for line in f:
                    if line.strip():
                        last = line
                if last:
                    self.prev = json.loads(last)["integrity"]["record_sha256"]

    def _append(self, path: str, obj: dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def handle_record(self, rec: dict) -> bool:
        st = self.stats
        st.records += 1
        problems = validate_record(rec)
        integ = rec.get("integrity") or {}
        if integ.get("record_sha256", "") == "" and not problems:
            if self.seal_unsealed:
                rec["integrity"]["sealed_by"] = "edge-controller-host (not device)"  # part of the hashed content
                seal(rec, self.prev)
                st.sealed_by_host += 1
            else:
                problems.append("unsealed record (record_sha256 empty); rerun with --seal to seal on host")
        if not problems:
            try:
                h = record_hash(rec)
            except (TypeError, ValueError) as e:
                problems.append(f"canonicalisation failed: {e}")
                h = None
            if h is not None and h != rec["integrity"]["record_sha256"]:
                st.hash_mismatches += 1
                problems.append(f"record hash mismatch (computed {h})")
        if not problems and rec["integrity"].get("prev_record_sha256") != self.prev:
            # A firmware reboot legitimately starts a new chain (prev null); everything else is a break.
            st.chain_breaks += 1
            if rec["integrity"].get("prev_record_sha256") is None:
                self.err.write(f"[edge] new chain segment at {rec.get('event_id')} (prev=null; firmware restart?)\n")
            else:
                problems.append(f"chain break: prev {rec['integrity'].get('prev_record_sha256')} != last accepted {self.prev}")
        if problems:
            st.rejected += 1
            st.errors.append({"event_id": rec.get("event_id"), "problems": problems[:5]})
            self._append(self.rejected_path, {"problems": problems, "record": rec})
            self.err.write(f"[edge] REJECTED {rec.get('event_id')}: {problems[0]}\n")
            return False
        self._append(self.store_path, rec)
        self.prev = rec["integrity"]["record_sha256"]
        st.accepted += 1
        res = (rec.get("classification") or {}).get("result", "?")
        st.by_result[res] = st.by_result.get(res, 0) + 1
        if self.adapter is not None and self.job_id:
            self.adapter.ingest_record(self.job_id, rec, raw_src_dir=self.raw_dir)
            st.forwarded += 1
        return True

    def handle_line(self, line: str) -> None:
        st = self.stats
        st.lines += 1
        s = line.strip()
        if not s:
            return
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            st.text_lines += 1
            if self.echo:
                self.err.write(f"[fw] {s}\n")
            return
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if isinstance(it, dict) and "schema_version" in it:
                self.handle_record(it)
            elif isinstance(it, dict):
                typ = str(it.get("type") or it.get("kind") or "unknown")
                st.telemetry += 1
                st.by_type[typ] = st.by_type.get(typ, 0) + 1
                if self.echo:
                    self.err.write(f"[fw:{typ}] {s[:200]}\n")
            else:
                st.text_lines += 1

    def run(self, stream: Iterable[str]) -> BridgeStats:
        for line in stream:
            self.handle_line(line)
        return self.stats

    def summary(self) -> dict:
        d = asdict(self.stats)
        d["store"] = self.store_path
        d["chain_head"] = self.prev
        d["label"] = "SIMULATION adapter" if self.adapter is not None else "store only"
        return d
