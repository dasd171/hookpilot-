"""python -m edge_controller [--store events.jsonl] [--mock-konnex DIR --scenario S] [--seal] [--echo] < firmware_output"""
import argparse
import json
import sys

from .bridge import Bridge


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="edge_controller", description="firmware JSON lines -> evidence store (+ mock Konnex)")
    p.add_argument("--store", default="./evidence/events.jsonl")
    p.add_argument("--input", default="-", help="file to read instead of stdin")
    p.add_argument("--mock-konnex", metavar="DIR", help="forward records to a MockKonnexAdapter job store (SIMULATION)")
    p.add_argument("--job-id", help="existing mock job id (default: create one)")
    p.add_argument("--device-id", default="hookpilot-sim-001")
    p.add_argument("--firmware", default="0.1.0-dev")
    p.add_argument("--scenario", default="unspecified")
    p.add_argument("--raw-dir", help="base dir resolving sensor_data.raw_data_ref of incoming records")
    p.add_argument("--seal", action="store_true", help="seal unsealed records on the host (host becomes attester)")
    p.add_argument("--echo", action="store_true", help="echo non-record lines to stderr")
    p.add_argument("--bundle", metavar="ZIP", help="after EOF: build bundle, mock-prove and verify (needs --mock-konnex)")
    a = p.parse_args(argv)

    adapter = job = None
    if a.mock_konnex:
        from konnex_adapter import DeviceIdentity, MockKonnexAdapter, SIMULATION_BANNER, TaskSpec
        print(SIMULATION_BANNER, file=sys.stderr)
        adapter = MockKonnexAdapter(a.mock_konnex)
        job = a.job_id
        if not job:
            spec = TaskSpec(task_id=f"edge-{a.scenario}", device=DeviceIdentity(a.device_id, a.firmware),
                            test_scenario=a.scenario, seed=None)
            job = adapter.submit_task(spec)
            adapter.attach_robot(job, a.device_id)
    br = Bridge(a.store, adapter=adapter, job_id=job, seal_unsealed=a.seal, echo=a.echo, raw_dir=a.raw_dir)
    stream = sys.stdin if a.input == "-" else open(a.input, encoding="utf-8")
    try:
        br.run(stream)
    finally:
        if stream is not sys.stdin:
            stream.close()
    out = br.summary()
    out["job_id"] = job
    if a.bundle and adapter is not None and br.stats.accepted:
        b = adapter.record(job, a.bundle)
        adapter.prove(job, b["path"])
        res = adapter.verify(job)
        out["bundle"] = b
        out["verification"] = {"score": res["score"], "verdict": res["verdict"],
                               "criteria": {c["name"]: c["status"] for c in res["criteria"]}}
    print(json.dumps(out, indent=2))
    return 0 if br.stats.rejected == 0 else 1


raise SystemExit(main())
