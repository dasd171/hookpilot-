#!/usr/bin/env python3
"""Verify firmware JSON event lines (SIMULATION output) against the shared contract.

Checks per file:
  * every line parses and validates against packages/event-schema/hookpilot-event.schema.json
    (requires the `jsonschema` package; skipped with a warning if absent)
  * integrity.record_sha256 == sha256(canonical JSON with record_sha256 = "")
    canonical = keys sorted recursively, separators (',', ':'), numbers as ECMAScript
    Number.prototype.toString, strings UTF-8 unescaped (same as JSON.stringify)
  * hash chain: prev_record_sha256 of record i == record_sha256 of record i-1 (first: null)
  * outcome.milestones.fish_landed == "unknown"; source.mode == "simulation"
  * if services/evidence-service is importable, its canonical.record_hash must agree too

Usage: verify_events.py <schema.json> <file.jsonl> [...]
Exit code 0 only when every check passed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys


def js_number(x):
    if isinstance(x, bool):
        raise TypeError("bool")
    if isinstance(x, int):
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("non-finite")
    if x == 0:
        return "0"
    r = repr(x)
    sign = ""
    if r[0] == "-":
        sign, r = "-", r[1:]
    if "e" in r:
        mant, exp = r.split("e")
        e = int(exp)
    else:
        mant, e = r, 0
    ip, fp = (mant.split(".") + [""])[:2] if "." in mant else (mant, "")
    allds = ip + fp
    lead = len(allds) - len(allds.lstrip("0"))
    digits = allds.lstrip("0").rstrip("0") or "0"
    n = len(ip) - lead + e
    k = len(digits)
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        ev = n - 1
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if ev >= 0 else "-") + str(abs(ev))
    return sign + s


def canon(o) -> str:
    if o is None:
        return "null"
    if o is True:
        return "true"
    if o is False:
        return "false"
    if isinstance(o, (int, float)):
        return js_number(o)
    if isinstance(o, str):
        return json.dumps(o, ensure_ascii=False)
    if isinstance(o, list):
        return "[" + ",".join(canon(v) for v in o) + "]"
    if isinstance(o, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canon(o[k]) for k in sorted(o)) + "}"
    raise TypeError(type(o))


def record_hash(rec: dict) -> str:
    r = copy.deepcopy(rec)
    r["integrity"]["record_sha256"] = ""
    return hashlib.sha256(canon(r).encode("utf-8")).hexdigest()


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    schema = json.load(open(argv[1]))
    validator = None
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    except Exception as exc:  # pragma: no cover
        print(f"WARNING: jsonschema unavailable ({exc}); schema validation skipped")
    svc_hash = None
    here = os.path.dirname(os.path.abspath(__file__))
    svc = os.path.normpath(os.path.join(here, "..", "..", "services", "evidence-service"))
    if os.path.isdir(svc):
        sys.path.insert(0, svc)
        try:
            from evidence_service.canonical import record_hash as svc_hash  # type: ignore
        except Exception as exc:
            print(f"note: evidence-service canonical not importable ({exc}); cross-check skipped")
            svc_hash = None

    total = bad = 0
    for path in argv[2:]:
        prev = None
        lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
        for i, line in enumerate(lines):
            total += 1
            errs = []
            rec = json.loads(line)
            if validator is not None:
                errs += [f"schema: {e.message} at {list(e.path)}" for e in validator.iter_errors(rec)]
            h = record_hash(rec)
            if h != rec["integrity"]["record_sha256"]:
                errs.append("record_sha256 mismatch")
            if canon(rec) != line:
                errs.append("emitted line is not byte-identical to its canonical form")
            if svc_hash is not None and svc_hash(rec) != rec["integrity"]["record_sha256"]:
                errs.append("evidence-service canonical.record_hash disagrees")
            if rec["integrity"].get("prev_record_sha256") != prev:
                errs.append("hash chain broken")
            if rec["outcome"]["milestones"]["fish_landed"] != "unknown":
                errs.append("fish_landed must be 'unknown'")
            if rec["source"]["mode"] != "simulation":
                errs.append("source.mode must be 'simulation' for host sim output")
            prev = rec["integrity"]["record_sha256"]
            if errs:
                bad += 1
                print(f"FAIL {path}:{i + 1}: " + "; ".join(errs))
        print(f"{path}: {len(lines)} record(s) checked")
    print(f"verify_events: {total} record(s), {bad} failing, schema={'on' if validator else 'off'}, "
          f"evidence-service cross-check={'on' if svc_hash else 'off'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
