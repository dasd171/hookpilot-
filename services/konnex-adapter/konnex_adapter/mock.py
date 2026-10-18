"""MockKonnexAdapter — *** SIMULATION *** local, file-backed stand-in for Konnex.

Nothing here talks to Konnex. No network, no chain, no tokens. Job ids, receipts
and "proofs" are local JSON files whose only meaning is inside this repository.
The method names echo the documented CLI verbs (task submit / robot attach /
robot record / prove / validators.verify) so the flow is recognisable, but the
data shapes are HookPilot's own (docs/konnex-integration.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

from evidence_service.canonical import canonical_json
from evidence_service.engine import EventLog, build_event
from evidence_service.classifier import Thresholds

from .bundle import build_bundle
from .task import TaskSpec, task_result_from_record
from .verification import verify_bundle

SIMULATION_BANNER = "SIMULATION – MockKonnexAdapter: local file-backed mock, NOT connected to Konnex"


class MockKonnexAdapter:
    label = "SIMULATION"

    def __init__(self, store_dir: str, deterministic: bool = False) -> None:
        self.store = os.path.abspath(store_dir)
        self.deterministic = deterministic
        os.makedirs(os.path.join(self.store, "jobs"), exist_ok=True)
        self._n = 0

    # ---- job store -----------------------------------------------------------
    def _job_dir(self, job_id: str) -> str:
        return os.path.join(self.store, "jobs", job_id)

    def _load(self, job_id: str) -> dict:
        p = os.path.join(self._job_dir(job_id), "job.json")
        if not os.path.exists(p):
            raise KeyError(f"unknown mock job {job_id}")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, job: dict) -> None:
        d = self._job_dir(job["job_id"])
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2)
        os.replace(tmp, os.path.join(d, "job.json"))

    def _now(self) -> str:
        return "1970-01-01T00:00:00Z" if self.deterministic else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _event(self, job: dict, kind: str, **kw) -> None:
        job.setdefault("history", []).append({"t": self._now(), "event": kind, **kw})

    def get_job(self, job_id: str) -> dict:
        return self._load(job_id)

    # ---- flow ---------------------------------------------------------------
    def submit_task(self, spec: TaskSpec) -> str:
        """Mock of `konnex task submit` – registers the proposed task, returns a LOCAL job id."""
        task = spec.to_dict()
        tid = hashlib.sha256(canonical_json(task).encode()).hexdigest()
        if self.deterministic:
            self._n += 1
            job_id = f"mock-job-{tid[:12]}-{self._n:03d}"
        else:
            job_id = f"mock-job-{uuid.uuid4().hex[:16]}"
        job = {"job_id": job_id, "label": "SIMULATION", "banner": SIMULATION_BANNER,
               "task": task, "task_sha256": tid, "status": "submitted", "robot": None,
               "events_path": os.path.join(self._job_dir(job_id), "events.jsonl"),
               "raw_dir": os.path.join(self._job_dir(job_id), "raw"), "outputs": [], "bundle": None,
               "receipt": None, "verification": None}
        self._event(job, "submitted")
        self._save(job)
        return job_id

    def attach_robot(self, job_id: str, robot_id: str, subnet: str = "mock.hookpilot.sim") -> dict:
        """Mock of `konnex robot attach --robot-id <id> --subnet <subnet> --keys ./keys.json` (no keys used)."""
        job = self._load(job_id)
        dev = job["task"]["device"]
        if robot_id != dev["device_id"]:
            raise ValueError(f"robot id {robot_id} != task device {dev['device_id']}")
        job["robot"] = {"robot_id": robot_id, "subnet": subnet, "keys": None,
                        "note": "mock attach; no key material, no secure element"}
        job["status"] = "attached"
        self._event(job, "attached", robot_id=robot_id, subnet=subnet)
        self._save(job)
        return job["robot"]

    def ingest_record(self, job_id: str, record: dict, raw_src_dir: str | None = None) -> dict:
        """Append an already-sealed record (e.g. from firmware via edge-controller); returns task outputs.
        Raw files referenced by the record are copied from raw_src_dir into the job store."""
        job = self._load(job_id)
        ref = (record.get("sensor_data") or {}).get("raw_data_ref")
        if ref and raw_src_dir:
            import shutil
            for c in (os.path.join(raw_src_dir, ref), os.path.join(raw_src_dir, os.path.basename(ref))):
                if os.path.exists(c):
                    os.makedirs(job["raw_dir"], exist_ok=True)
                    shutil.copyfile(c, os.path.join(job["raw_dir"], os.path.basename(ref)))
                    break
        with open(job["events_path"], "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        out = task_result_from_record(record, chain_head=record["integrity"].get("record_sha256"))
        job["outputs"].append(out)
        job["status"] = "executed"
        self._event(job, "record_ingested", event_id=record.get("event_id"))
        self._save(job)
        return out

    def execute(self, job_id: str, armed: bool = True, thresholds: Thresholds | None = None,
                seed: int | None = None) -> dict:
        """Run the task in SIMULATION using the Python reference engine; returns the task OUTPUTS."""
        job = self._load(job_id)
        if job["robot"] is None:
            raise RuntimeError("attach_robot first")
        t = job["task"]
        if t["mode"] != "simulation" or t.get("seed") is None:
            raise RuntimeError("MockKonnexAdapter.execute only runs simulation tasks with a seed")
        log = EventLog(job["events_path"])
        run_seed = int(t["seed"]) if seed is None else int(seed)
        rec = build_event(t["test_scenario"], run_seed, armed=armed, counter=len(log.records) + 1,
                          device=dict(t["device"]), thresholds=thresholds, raw_dir=job["raw_dir"])
        rec["device"] = {k: t["device"][k] for k in ("device_id", "firmware_version", "hardware_revision")}
        log.append(rec)
        out = task_result_from_record(rec, chain_head=log.prev)
        job = self._load(job_id)
        job["outputs"].append(out)
        job["status"] = "executed"
        self._event(job, "executed", event_id=rec["event_id"], detection=out["detection_result"])
        self._save(job)
        return out

    def record(self, job_id: str, out_zip: str | None = None) -> dict:
        """Mock of `konnex robot record --job-id <JOBID> ... --out ./powp.zip` (our own bundle layout)."""
        from evidence_service.chain import load_jsonl
        job = self._load(job_id)
        recs = load_jsonl(job["events_path"]) if os.path.exists(job["events_path"]) else []
        if not recs:
            raise RuntimeError("no records to bundle")
        out_zip = out_zip or os.path.join(self._job_dir(job_id), "powp.zip")
        b = build_bundle(recs, out_zip, raw_dir=job["raw_dir"], task=job["task"], job_id=job_id,
                         created_utc=self._now())
        job["bundle"] = {"path": out_zip, "bundle_sha256": b["bundle_sha256"],
                         "manifest_sha256": b["manifest"]["manifest_sha256"], "record_count": len(recs)}
        job["status"] = "recorded"
        self._event(job, "recorded", bundle_sha256=b["bundle_sha256"])
        self._save(job)
        return job["bundle"]

    def prove(self, job_id: str, bundle_path: str) -> dict:
        """Mock of `konnex prove --job-id <JOBID> --bundle ./powp.zip`. Produces a LOCAL receipt only."""
        job = self._load(job_id)
        with open(bundle_path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        receipt = {"label": "SIMULATION", "job_id": job_id, "bundle_sha256": sha,
                   "task_sha256": job["task_sha256"], "submitted_utc": self._now(),
                   "onchain_tx": None,
                   "note": "mock receipt – nothing was submitted to Konnex or any chain"}
        job["receipt"] = receipt
        job["status"] = "proof_submitted_mock"
        self._event(job, "prove_mock", bundle_sha256=sha)
        self._save(job)
        return receipt

    def verify(self, job_id: str) -> dict:
        """Mock of validators.verify(job_id, bundle) – runs HookPilot's LOCAL criteria scoring."""
        job = self._load(job_id)
        if not job.get("receipt"):
            raise RuntimeError("prove first")
        res = verify_bundle(job["bundle"]["path"], task=job["task"])
        if res["bundle_sha256"] != job["receipt"]["bundle_sha256"]:
            res["verdict"] = "reject"
            res["criteria"].append({"id": "C0", "name": "bundle_matches_receipt", "status": "fail",
                                    "details": ["bundle changed after prove"]})
        summary = {k: res[k] for k in ("scorer", "label", "score", "passed", "applicable", "verdict", "bundle_sha256")}
        summary["criteria"] = [{"id": c["id"], "name": c["name"], "status": c["status"]} for c in res["criteria"]]
        job["verification"] = summary
        job["status"] = "verified_mock" if res["verdict"] == "accept" else "rejected_mock"
        self._event(job, "verify_mock", verdict=res["verdict"], score=res["score"])
        self._save(job)
        return res
