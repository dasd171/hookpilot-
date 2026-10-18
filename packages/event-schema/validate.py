#!/usr/bin/env python3
"""Standalone validator for HookPilot event records (schema 1.0.0).

    python packages/event-schema/validate.py FILE [FILE ...] [--hash] [--chain]

FILE may be a single JSON record, a JSON array, or JSONL. Requires only `jsonschema`.
--hash   also recompute integrity.record_sha256 (canonical JSON per docs/canonical-hashing.md)
--chain  also check prev_record_sha256 links within each file (first record: prev must be null)
Exit 0 = all valid, 1 = invalid, 2 = usage error.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hookpilot-event.schema.json")
fc = FormatChecker()


@fc.checks("date-time", raises=ValueError)
def _dt(v):
    if isinstance(v, str):
        if "T" not in v or datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("RFC 3339 date-time with offset required")
    return True


def js_number(x):
    """ECMAScript Number::toString (identical to services/evidence-service canonical.py)."""
    if isinstance(x, int) and not isinstance(x, bool):
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN/Infinity")
    if x == 0:
        return "0"
    r = repr(x)
    sign = ""
    if r[0] == "-":
        sign, r = "-", r[1:]
    mant, e = (r.lower().split("e") + ["0"])[:2] if "e" in r.lower() else (r, "0")
    e = int(e)
    ip, fp = (mant.split(".") + [""])[:2] if "." in mant else (mant, "")
    alld = ip + fp
    lead = len(alld) - len(alld.lstrip("0"))
    digits = alld.lstrip("0").rstrip("0") or "0"
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


def canon(o):
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
    return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canon(o[k]) for k in sorted(o)) + "}"


def record_hash(r):
    r = copy.deepcopy(r)
    r.setdefault("integrity", {})["record_sha256"] = ""
    return hashlib.sha256(canon(r).encode("utf-8")).hexdigest()


def load(path):
    txt = open(path, encoding="utf-8").read().strip()
    if txt.startswith("["):
        return json.loads(txt)
    try:
        return [json.loads(txt)]
    except json.JSONDecodeError:
        return [json.loads(l) for l in txt.splitlines() if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--hash", action="store_true")
    ap.add_argument("--chain", action="store_true")
    a = ap.parse_args(argv)
    v = Draft202012Validator(json.load(open(SCHEMA)), format_checker=fc)
    bad = 0
    for path in a.files:
        try:
            recs = load(path)
        except (OSError, json.JSONDecodeError) as e:
            print(f"{path}: cannot read: {e}")
            return 2
        prev = None
        for i, r in enumerate(recs):
            errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(r)]
            if a.hash and not errs and record_hash(r) != r["integrity"]["record_sha256"]:
                errs.append("integrity.record_sha256 does not match canonical hash")
            if a.chain and not errs and r["integrity"].get("prev_record_sha256") != prev:
                errs.append("prev_record_sha256 does not link to previous record")
            prev = (r.get("integrity") or {}).get("record_sha256")
            tag = f"{path}#{i} {r.get('event_id', '?')}"
            if errs:
                bad += 1
                print(f"INVALID {tag}")
                for e in errs[:10]:
                    print(f"   - {e}")
            else:
                print(f"valid   {tag}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
