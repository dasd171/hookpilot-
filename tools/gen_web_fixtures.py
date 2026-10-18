#!/usr/bin/env python3
"""Generate the Python-reference fixtures the web demo's cross-implementation test compares against.

    python tools/gen_web_fixtures.py            # writes apps/web-demo/tests/fixtures/python_reference.json
    python tools/gen_web_fixtures.py --check    # exit 1 if the committed file is stale

SIMULATION. The file is deterministic (no timestamps), so tests/integration/test_web_fixtures.py can
check it is up to date with the Python port. Contents:
  signals          hookpilot-synth-v1 samples + drawn params for every scenario x SIGNAL_SEEDS
  classifications  deterministic-baseline-v1 (firmware rules, default thresholds) streaming result,
                   reason, rule_trace, features, decision/trigger times for every scenario x CLASS_SEEDS
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "services", "evidence-service"))

from evidence_service.classifier import (CLASSIFIER_ID, FW_DEFAULTS, IMPLEMENTATION, Thresholds,  # noqa: E402
                                         classify)
from evidence_service.synth import GENERATOR_ID, SCENARIOS, generate  # noqa: E402

OUT = os.path.join(ROOT, "apps", "web-demo", "tests", "fixtures", "python_reference.json")
SIGNAL_SEEDS = (1, 42, 1337)
CLASS_SEEDS = tuple(range(1, 41))


def build() -> dict:
    th = Thresholds.defaults("fw")
    signals = []
    for sc in SCENARIOS:
        for seed in SIGNAL_SEEDS:
            sig = generate(sc, seed)
            signals.append({"scenario": sc, "seed": seed, "params": sig.params, "tension_n": sig.tension_n})
    classes = []
    for sc in SCENARIOS:
        for seed in CLASS_SEEDS:
            d = classify(generate(sc, seed).samples(), th)
            classes.append({"scenario": sc, "seed": seed, "result": d.result, "reason": d.reason,
                            "rule_trace": d.rule_trace, "decision_t_ms": d.decision_t_ms,
                            "trigger_t_ms": d.trigger_t_ms, "features": d.features})
    return {
        "label": "SIMULATION - Python reference for apps/web-demo/tests/cross_impl.test.ts",
        "command": "python tools/gen_web_fixtures.py",
        "generator": GENERATOR_ID, "classifier": CLASSIFIER_ID, "implementation": IMPLEMENTATION["fw"],
        "thresholds": FW_DEFAULTS, "signal_seeds": list(SIGNAL_SEEDS), "class_seeds": list(CLASS_SEEDS),
        "signals": signals, "classifications": classes,
    }


def dumps(d: dict) -> str:
    return json.dumps(d, separators=(",", ":")) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    text = dumps(build())
    if a.check:
        if not os.path.exists(a.out):
            print("fixture not present (it is generated on demand by `npm test` in apps/web-demo)")
            return 0
        cur = open(a.out, encoding="utf-8").read()
        if cur != text:
            print(f"{a.out} is stale; run python tools/gen_web_fixtures.py")
            return 1
        print("fixtures up to date")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {a.out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
