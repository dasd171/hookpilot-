"""Schema validation against packages/event-schema/hookpilot-event.schema.json."""
from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache

import jsonschema
from jsonschema import Draft202012Validator, FormatChecker


def find_schema_path() -> str:
    env = os.environ.get("HOOKPILOT_SCHEMA")
    if env:
        return env
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        cand = os.path.join(d, "packages", "event-schema", "hookpilot-event.schema.json")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    raise FileNotFoundError("hookpilot-event.schema.json not found; set HOOKPILOT_SCHEMA")


_fc = FormatChecker()


@_fc.checks("date-time", raises=ValueError)
def _is_datetime(v) -> bool:
    if not isinstance(v, str):
        return True
    if "T" not in v:
        raise ValueError("date-time needs 'T'")
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("date-time needs a timezone offset (RFC 3339)")
    return True


@lru_cache(maxsize=4)
def get_validator(path: str | None = None) -> Draft202012Validator:
    with open(path or find_schema_path(), encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=_fc)


def validate_record(record: dict, schema_path: str | None = None) -> list[str]:
    """Return a list of human-readable schema errors (empty == valid)."""
    v = get_validator(schema_path)
    errs = []
    for e in sorted(v.iter_errors(record), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        errs.append(f"{loc}: {e.message}")
    return errs


__all__ = ["validate_record", "get_validator", "find_schema_path", "jsonschema"]
