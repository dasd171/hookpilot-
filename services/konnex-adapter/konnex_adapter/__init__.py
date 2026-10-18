"""HookPilot <-> Konnex adapter.

* MockKonnexAdapter  – SIMULATION, local file-backed job store (default, the only working path).
* KonnexCliAdapter   – UNVERIFIED skeleton of documented CLI shapes, disabled unless
                       HOOKPILOT_ENABLE_REAL_KONNEX=1 and a konnex binary exists.
"""
import os as _os
import sys as _sys

try:  # allow running from a source checkout without pip install
    import evidence_service  # noqa: F401
except ImportError:  # pragma: no cover
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "..", "evidence-service"))

from .task import TaskSpec, DeviceIdentity, TASK_TYPE, task_result_from_record  # noqa: E402
from .bundle import build_bundle, read_bundle, LAYOUT_ID  # noqa: E402
from .verification import verify_bundle, verify_evidence, check_safety  # noqa: E402
from .mock import MockKonnexAdapter, SIMULATION_BANNER  # noqa: E402
from .cli_adapter import KonnexCliAdapter, RealKonnexDisabled  # noqa: E402

__all__ = ["TaskSpec", "DeviceIdentity", "TASK_TYPE", "task_result_from_record", "build_bundle", "read_bundle",
           "LAYOUT_ID", "verify_bundle", "verify_evidence", "check_safety", "MockKonnexAdapter",
           "SIMULATION_BANNER", "KonnexCliAdapter", "RealKonnexDisabled"]
__version__ = "0.1.0"
