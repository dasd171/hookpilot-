"""python -m konnex_adapter {demo,verify-bundle,cli-argv}  (SIMULATION unless stated)."""
import argparse
import json
import sys

from . import (DeviceIdentity, KonnexCliAdapter, MockKonnexAdapter, SIMULATION_BANNER, TaskSpec, verify_bundle)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="konnex_adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="SIMULATION: submit -> attach -> execute -> record -> prove -> verify (mock)")
    d.add_argument("--scenario", default="bite")
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--runs", type=int, default=1, help="consecutive seeds executed in one job")
    d.add_argument("--store", default="./.mock-konnex")
    d.add_argument("--device-id", default="hp-sim-001")
    d.add_argument("--firmware", default="sim-py-0.1.0")
    d.add_argument("--disarmed", action="store_true")
    v = sub.add_parser("verify-bundle", help="score a bundle zip against the local criteria")
    v.add_argument("bundle")
    v.add_argument("--json", action="store_true")
    c = sub.add_parser("cli-argv", help="print the documented konnex CLI argv (nothing is executed)")
    c.add_argument("--job-id", default="<JOBID>")
    a = p.parse_args(argv)

    if a.cmd == "demo":
        print(SIMULATION_BANNER, file=sys.stderr)
        ad = MockKonnexAdapter(a.store)
        spec = TaskSpec(task_id=f"hookpilot-{a.scenario}-{a.seed}", device=DeviceIdentity(a.device_id, a.firmware),
                        test_scenario=a.scenario, seed=a.seed)
        jid = ad.submit_task(spec)
        ad.attach_robot(jid, a.device_id)
        outs = []
        for i in range(a.runs):
            outs.append(ad.execute(jid, armed=not a.disarmed, seed=a.seed + i))
        b = ad.record(jid)
        r = ad.prove(jid, b["path"])
        res = ad.verify(jid)
        print(json.dumps({"job_id": jid, "outputs": outs, "bundle": b, "receipt": r,
                          "verification": {k: res[k] for k in ("scorer", "label", "score", "verdict")},
                          "criteria": [(c["id"], c["name"], c["status"]) for c in res["criteria"]]}, indent=2))
        return 0 if res["verdict"] == "accept" else 1
    if a.cmd == "verify-bundle":
        res = verify_bundle(a.bundle)
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            for c in res["criteria"]:
                print(f"{c['id']:>3} {c['name']:<32} {c['status']}")
                for x in c["details"][:5]:
                    print(f"      - {x}")
            print(f"[{res['label']}] score={res['score']} ({res['passed']}/{res['applicable']}) verdict={res['verdict']}")
            print(res["trust_note"])
        return 0 if res["verdict"] == "accept" else 1
    if a.cmd == "cli-argv":
        k = KonnexCliAdapter()
        ok, why = k.enabled_reason()
        print(json.dumps({"status": k.status, "enabled": ok, "reason": why,
                          "attach": k.argv_attach("<robot-id>", "<subnet>", "./keys.json"),
                          "record": k.argv_record(a.job_id, ["tension", "motor_current"], "./powp.zip"),
                          "prove": k.argv_prove(a.job_id, "./powp.zip")}, indent=2))
        return 0
    return 2


raise SystemExit(main())
