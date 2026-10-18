"""KonnexCliAdapter — SKELETON. UNVERIFIED. Disabled by default.

Mirrors ONLY the CLI shapes documented at docs.konnex.world (fetched 2026-10-18):

    konnex robot attach --robot-id <id> --subnet <subnet> --keys ./keys.json
    konnex robot record --job-id <JOBID> --sensors camera,imu,torque,thermal --out ./powp.zip
    konnex prove --job-id <JOBID> --bundle ./powp.zip

The Konnex validators page calls these commands "illustrative"; the SDK repo
returned HTTP 404 from our environment, so none of this was ever executed.
Unknowns: output format, exit codes, keys.json format, whether --sensors accepts
custom channels ("tension", "motor_current"), bundle layout, auth, base URL.

Guard: every method refuses unless HOOKPILOT_ENABLE_REAL_KONNEX=1 AND a `konnex`
binary is on PATH. Default is dry-run (returns argv, runs nothing). Anything not
documented (HTTP, Python client, parsing results) raises NotImplementedError("unverified").
"""
from __future__ import annotations

import os
import shutil
import subprocess

ENV_FLAG = "HOOKPILOT_ENABLE_REAL_KONNEX"
STATUS = "UNVERIFIED skeleton – documented CLI shapes only, never executed against Konnex"


class RealKonnexDisabled(RuntimeError):
    pass


class KonnexCliAdapter:
    status = STATUS

    def __init__(self, binary: str = "konnex", dry_run: bool = True) -> None:
        self.binary = binary
        self.dry_run = dry_run

    # ---- guard -------------------------------------------------------------
    @staticmethod
    def enabled_reason(binary: str = "konnex") -> tuple[bool, str]:
        if os.environ.get(ENV_FLAG) != "1":
            return False, f"{ENV_FLAG}!=1 (real Konnex integration is disabled by default)"
        if shutil.which(binary) is None:
            return False, f"'{binary}' binary not found on PATH (SDK not installed; repo was 404 on 2026-10-18)"
        return True, "enabled"

    def _guard(self) -> None:
        ok, why = self.enabled_reason(self.binary)
        if not ok:
            raise RealKonnexDisabled(f"refusing real Konnex call: {why}")

    # ---- documented argv builders (pure; safe to unit-test) ----------------
    def argv_attach(self, robot_id: str, subnet: str, keys_path: str) -> list[str]:
        return [self.binary, "robot", "attach", "--robot-id", robot_id, "--subnet", subnet, "--keys", keys_path]

    def argv_record(self, job_id: str, sensors: list[str], out_zip: str) -> list[str]:
        return [self.binary, "robot", "record", "--job-id", job_id, "--sensors", ",".join(sensors), "--out", out_zip]

    def argv_prove(self, job_id: str, bundle_path: str) -> list[str]:
        return [self.binary, "prove", "--job-id", job_id, "--bundle", bundle_path]

    # ---- guarded execution ---------------------------------------------------
    def _run(self, argv: list[str]) -> dict:
        self._guard()
        if self.dry_run:
            return {"dry_run": True, "argv": argv, "status": STATUS}
        p = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        # Output format is undocumented: return it raw, never parse/interpret it.
        return {"dry_run": False, "argv": argv, "returncode": p.returncode, "stdout": p.stdout,
                "stderr": p.stderr, "status": STATUS + " – output not interpreted"}

    def attach(self, robot_id: str, subnet: str, keys_path: str) -> dict:
        return self._run(self.argv_attach(robot_id, subnet, keys_path))

    def record(self, job_id: str, sensors: list[str], out_zip: str) -> dict:
        # NOTE: documented examples list camera,imu,torque,thermal; custom channels unverified.
        return self._run(self.argv_record(job_id, sensors, out_zip))

    def prove(self, job_id: str, bundle_path: str) -> dict:
        return self._run(self.argv_prove(job_id, bundle_path))

    # ---- everything else is unverified --------------------------------------
    def submit_task(self, *a, **kw):
        self._guard()
        raise NotImplementedError("unverified: task submission for a non-VLA HookPilot task has no published schema")

    def verify(self, *a, **kw):
        self._guard()
        raise NotImplementedError("unverified: validator scoring endpoint/auth/base URL not published")

    def parse_job_result(self, *a, **kw):
        raise NotImplementedError("unverified: CLI output format not documented")
