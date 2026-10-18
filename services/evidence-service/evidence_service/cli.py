"""CLI: python -m evidence_service {verify,replay,export,generate,hash}

Exit codes: 0 = all checks passed, 1 = at least one check failed, 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .canonical import record_hash
from .chain import load_jsonl, verify_records
from .export import to_csv
from .replay import replay


def _load_any(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    s = txt.lstrip()
    if s.startswith("["):
        return json.loads(s)
    if s.startswith("{"):
        try:
            return [json.loads(s)]
        except json.JSONDecodeError:
            pass
    return load_jsonl(path)


def cmd_verify(a) -> int:
    recs = _load_any(a.events)
    raw_dir = a.raw_dir or os.path.dirname(os.path.abspath(a.events))
    checks = verify_records(recs, raw_base_dir=raw_dir, genesis_prev=a.genesis_prev)
    rep = []
    if a.replay:
        rep = [replay(r, raw_base_dir=raw_dir) for r in recs]
    ok = all(c.ok for c in checks) and all(x["ok"] for x in rep)
    if a.json:
        print(json.dumps({"ok": ok, "records": [c.to_dict() for c in checks], "replay": rep}, indent=2))
    else:
        for c in checks:
            flag = "OK  " if c.ok else "FAIL"
            raw = {True: "raw:ok", False: "raw:FAIL", None: "raw:-"}[c.raw_ok]
            print(f"[{flag}] #{c.index} {c.event_id} schema:{'ok' if not c.schema_errors else 'FAIL'} "
                  f"hash:{'ok' if c.hash_ok else 'FAIL'} chain:{'ok' if c.chain_ok else 'FAIL'} {raw}")
            for e in c.schema_errors[:10]:
                print(f"       schema: {e}")
            if not c.hash_ok:
                print(f"       computed sha256 {c.computed_sha256}")
            if c.raw_ok is False:
                print(f"       {c.raw_detail}")
        for x in rep:
            print(f"[{'OK  ' if x['ok'] else 'FAIL'}] replay {x['event_id']} signal_match={x['signal_match']} "
                  f"classification_match={x['classification_match']} recorded={x['recorded_result']}")
        print(f"{'PASS' if ok else 'FAIL'}: {sum(c.ok for c in checks)}/{len(checks)} records verified"
              + (f", {sum(x['ok'] for x in rep)}/{len(rep)} replays matched" if rep else ""))
    return 0 if ok else 1


def cmd_replay(a) -> int:
    recs = _load_any(a.event)
    raw_dir = a.raw_dir or os.path.dirname(os.path.abspath(a.event))
    res = [replay(r, tolerance=a.tolerance, raw_base_dir=raw_dir) for r in recs]
    print(json.dumps(res if len(res) > 1 else res[0], indent=2))
    return 0 if all(r["ok"] for r in res) else 1


def cmd_export(a) -> int:
    recs = _load_any(a.events)
    n = to_csv(recs, a.out)
    print(f"wrote {n} rows to {a.out}")
    return 0


def cmd_generate(a) -> int:
    from .engine import EventLog, run_scenario
    if os.path.exists(a.out) and not a.append:
        os.remove(a.out)
    log = EventLog(a.out)
    raw_dir = os.path.join(os.path.dirname(os.path.abspath(a.out)), "raw") if a.raw else None
    counter = len(log.records)
    overrides = {}
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        overrides[k] = float(v)
    scenarios = a.scenario.split(",")
    written = 0
    trace_f = open(a.trace, "w", encoding="utf-8") if a.trace else None
    for sc in scenarios:
        for seed in range(a.seed, a.seed + a.count):
            run = run_scenario(sc, seed, armed=not a.disarmed, injections=a.inject or [], overrides=overrides,
                               counter=counter + 1, raw_dir=raw_dir, trace=bool(trace_f))
            for rec in run.records:  # every record of the run (multi-event runs included)
                counter += 1
                written += 1
                log.append(rec)
            if trace_f:
                for line in run.trace:
                    trace_f.write(json.dumps(dict(line, scenario=sc, seed=seed), separators=(",", ":")) + "\n")
    if trace_f:
        trace_f.close()
    print(f"SIMULATION: wrote {written} records to {a.out}" + (f" (+ raw CSVs in {raw_dir})" if raw_dir else ""))
    return 0


def cmd_hash(a) -> int:
    for r in _load_any(a.event):
        print(record_hash(r), r.get("event_id"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evidence_service", description="HookPilot evidence tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="schema + record hash + hash chain + raw sha256")
    v.add_argument("events")
    v.add_argument("--raw-dir", help="base dir for raw_data_ref (default: dir of events file)")
    v.add_argument("--genesis-prev", default=None, help="expected prev hash of first record (default null)")
    v.add_argument("--replay", action="store_true", help="also run deterministic replay on every record")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("replay", help="regenerate signal from (scenario, seed) and re-run classifier")
    r.add_argument("event")
    r.add_argument("--raw-dir")
    r.add_argument("--tolerance", type=float, default=1e-6)
    r.set_defaults(fn=cmd_replay)
    e = sub.add_parser("export", help="export records to CSV")
    e.add_argument("events")
    e.add_argument("--out", required=True)
    e.set_defaults(fn=cmd_export)
    g = sub.add_parser("generate", help="SIMULATION: generate hash-chained records via the Python engine")
    g.add_argument("--scenario", default="bite", help="comma-separated scenario list")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--count", type=int, default=1)
    g.add_argument("--out", required=True)
    g.add_argument("--raw", action="store_true", help="also write raw CSV files and raw_data_sha256")
    g.add_argument("--disarmed", action="store_true")
    g.add_argument("--append", action="store_true")
    g.add_argument("--inject", action="append", help="fault injection kind@t_ms|retrieval+ms[:value] (repeatable)")
    g.add_argument("--set", action="append", help="parameter override name=value (repeatable)")
    g.add_argument("--trace", help="write the per-tick trace (JSON lines) to this file")
    g.set_defaults(fn=cmd_generate)
    h = sub.add_parser("hash", help="print canonical record sha256")
    h.add_argument("event")
    h.set_defaults(fn=cmd_hash)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (OSError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
