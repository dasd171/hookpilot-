"""The web demo's cross-implementation fixtures must match the current Python port (SIMULATION)."""
import os
import subprocess
import sys


def test_web_fixtures_up_to_date(repo_root):
    p = subprocess.run([sys.executable, os.path.join(repo_root, "tools", "gen_web_fixtures.py"), "--check"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
