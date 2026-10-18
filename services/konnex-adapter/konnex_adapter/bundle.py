"""Evidence bundle builder — "powp-style" zip, OUR OWN LAYOUT.

Konnex documents `konnex robot record ... --out ./powp.zip` but has NOT published
the internal layout of a PoPW bundle (docs/konnex-docs-findings.md). This module
therefore defines a HookPilot layout, "hookpilot-evidence-bundle-v0":

    manifest.json          layout id, job/task ids, file list (path, bytes, sha256),
                           record count, chain head, SIMULATION flag, manifest_sha256
    task.json              the proposed task spec the run was executed against
    events/events.jsonl    hash-chained HookPilot event records (schema 1.0.0)
    raw/<event_id>.csv     raw tension data referenced by sensor_data.raw_data_ref
    LAYOUT.txt             human-readable statement that this is not the official layout

The zip is deterministic (fixed timestamps, sorted entries) so the bundle sha256
is reproducible for identical content.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

from evidence_service.canonical import canonical_bytes, canonical_json

LAYOUT_ID = "hookpilot-evidence-bundle-v0"
LAYOUT_NOTE = ("NOT the official Konnex PoPW bundle layout (unpublished as of 2026-10-18). "
               "HookPilot's own layout, named 'powp-style' only because the documented CLI writes powp.zip.")
_ZIP_DT = (2026, 1, 1, 0, 0, 0)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest_hash(manifest: dict) -> str:
    m = dict(manifest)
    m["manifest_sha256"] = ""
    return _sha(canonical_bytes(m))


def build_bundle(records: list[dict], out_zip: str, *, raw_dir: str | None = None,
                 task: dict | None = None, job_id: str | None = None, created_utc: str | None = None) -> dict:
    files: dict[str, bytes] = {}
    files["events/events.jsonl"] = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")
    if task is not None:
        files["task.json"] = (canonical_json(task) + "\n").encode("utf-8")
    missing_raw = []
    for r in records:
        ref = (r.get("sensor_data") or {}).get("raw_data_ref")
        if ref:
            cands = [os.path.join(raw_dir, ref), os.path.join(raw_dir, os.path.basename(ref))] if raw_dir else []
            p = next((c for c in cands if os.path.exists(c)), None)
            if p is None:
                missing_raw.append(ref)  # recorded in the manifest; verification C4 will fail
                continue
            with open(p, "rb") as f:
                files["raw/" + os.path.basename(ref)] = f.read()
    files["LAYOUT.txt"] = (LAYOUT_ID + "\n" + LAYOUT_NOTE + "\n").encode("utf-8")
    modes = sorted({(r.get("source") or {}).get("mode", "?") for r in records})
    manifest = {
        "layout": LAYOUT_ID,
        "layout_note": LAYOUT_NOTE,
        "job_id": job_id,
        "task_id": (task or {}).get("task_id"),
        "task_type": (task or {}).get("task_type"),
        "created_utc": created_utc,
        "modes": modes,
        "simulation": modes == ["simulation"],
        "label": "SIMULATION" if modes == ["simulation"] else "MIXED_OR_REAL",
        "record_count": len(records),
        "chain_genesis_prev": (records[0]["integrity"].get("prev_record_sha256") if records else None),
        "chain_head": (records[-1]["integrity"]["record_sha256"] if records else None),
        "files": [{"path": k, "bytes": len(v), "sha256": _sha(v)} for k, v in sorted(files.items())],
        "missing_raw": missing_raw,
        "signature": None,
        "signature_note": "unsigned: no device secure element / TEE in the prototype",
        "manifest_sha256": "",
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    files["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(out_zip)), exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=_ZIP_DT)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, files[name])
    with open(out_zip, "rb") as f:
        bundle_sha = _sha(f.read())
    return {"path": out_zip, "bundle_sha256": bundle_sha, "manifest": manifest}


def read_bundle(zip_path: str) -> dict:
    """Return {'manifest', 'task', 'records', 'files': {name: bytes}}."""
    with zipfile.ZipFile(zip_path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files["manifest.json"]) if "manifest.json" in files else None
    task = json.loads(files["task.json"]) if "task.json" in files else None
    records = []
    if "events/events.jsonl" in files:
        for line in io.StringIO(files["events/events.jsonl"].decode("utf-8")):
            if line.strip():
                records.append(json.loads(line))
    return {"manifest": manifest, "task": task, "records": records, "files": files}
