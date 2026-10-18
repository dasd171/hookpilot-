"""Canonical JSON + record hashing per docs/SHARED_CONTRACT.md.

Contract: sha256 over JSON with keys sorted recursively, separators (',', ':'),
with integrity.record_sha256 = "" during hashing.

Cross-language detail (documented in docs/canonical-hashing.md): numbers are
serialised exactly like ECMAScript ``JSON.stringify`` / ``Number.prototype.toString``
(e.g. 1.0 -> "1", 1e-7 -> "1e-7", 1e21 -> "1e+21"), strings are emitted as UTF-8
without ASCII escaping (like JSON.stringify), so TS, C++ and Python agree.
NaN / Infinity are rejected (not representable in JSON).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any


def js_number(x: float | int) -> str:
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN/Infinity cannot be canonicalised")
    if x == 0:
        return "0"  # JS prints -0 as "0"
    # shortest round-trip digits (same digit string JS uses)
    r = repr(x)
    sign = ""
    if r[0] == "-":
        sign, r = "-", r[1:]
    if "e" in r or "E" in r:
        mant, exp = r.lower().split("e")
        e = int(exp)
    else:
        mant, e = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    digits = (ip + fp).lstrip("0")
    # decimal exponent n such that value = 0.d1d2... * 10^n
    lead_zeros = len(ip + fp) - len((ip + fp).lstrip("0"))
    n = len(ip) - lead_zeros + e
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        exp_v = n - 1
        es = ("+" if exp_v >= 0 else "-") + str(abs(exp_v))
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + es
    return sign + s


def _enc(o: Any, out: list[str]) -> None:
    if o is None:
        out.append("null")
    elif o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif isinstance(o, (int, float)):
        out.append(js_number(o))
    elif isinstance(o, str):
        out.append(json.dumps(o, ensure_ascii=False))
    elif isinstance(o, (list, tuple)):
        out.append("[")
        for i, v in enumerate(o):
            if i:
                out.append(",")
            _enc(v, out)
        out.append("]")
    elif isinstance(o, dict):
        out.append("{")
        for i, k in enumerate(sorted(o.keys())):
            if not isinstance(k, str):
                raise TypeError("object keys must be strings")
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _enc(o[k], out)
        out.append("}")
    else:
        raise TypeError(f"unsupported type {type(o)!r}")


def canonical_json(obj: Any) -> str:
    out: list[str] = []
    _enc(obj, out)
    return "".join(out)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def record_hash(record: dict) -> str:
    """sha256 hex of the canonical record with integrity.record_sha256 = ""."""
    r = copy.deepcopy(record)
    r.setdefault("integrity", {})
    r["integrity"]["record_sha256"] = ""
    return hashlib.sha256(canonical_bytes(r)).hexdigest()


def seal(record: dict, prev_sha256: str | None) -> dict:
    """Set hash_algorithm / prev hash and compute record_sha256 in place; returns record."""
    integ = record.setdefault("integrity", {})
    integ["hash_algorithm"] = "sha256"
    integ["prev_record_sha256"] = prev_sha256
    integ.setdefault("signature", None)
    integ["record_sha256"] = record_hash(record)
    return record


def file_sha256(path: str, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
