"""HookPilot edge controller bridge (host side): firmware JSON lines -> evidence store + mock Konnex adapter."""
import os as _os
import sys as _sys

_here = _os.path.dirname(__file__)
for _rel in ("../../evidence-service", "../../konnex-adapter"):
    _p = _os.path.abspath(_os.path.join(_here, _rel))
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from .bridge import Bridge, BridgeStats  # noqa: E402

__all__ = ["Bridge", "BridgeStats"]
__version__ = "0.1.0"
