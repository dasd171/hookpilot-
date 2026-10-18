"""Shared pytest setup: make the three Python services importable from a source checkout."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for d in ("services/evidence-service", "services/konnex-adapter", "services/edge-controller"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402


@pytest.fixture
def repo_root():
    return ROOT


@pytest.fixture
def py_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(ROOT, d) for d in ("services/evidence-service", "services/konnex-adapter", "services/edge-controller")]
        + [env.get("PYTHONPATH", "")])
    env.pop("HOOKPILOT_ENABLE_REAL_KONNEX", None)
    return env
