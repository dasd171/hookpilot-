import glob
import json
import os
import subprocess
import sys

from evidence_service.canonical import record_hash
from evidence_service.replay import replay


def _ex(repo_root, *p):
    return os.path.join(repo_root, "packages", "event-schema", "examples", *p)


def test_valid_examples(repo_root):
    files = sorted(glob.glob(_ex(repo_root, "*.json")))
    assert len(files) >= 5
    r = subprocess.run([sys.executable, os.path.join(repo_root, "packages", "event-schema", "validate.py"), *files, "--hash"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    for f in files:
        assert replay(json.load(open(f)))["ok"], f


def test_chain_example(repo_root):
    r = subprocess.run([sys.executable, os.path.join(repo_root, "packages", "event-schema", "validate.py"),
                        _ex(repo_root, "chain_example.jsonl"), "--hash", "--chain"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_invalid_examples_fail(repo_root):
    for f in glob.glob(_ex(repo_root, "invalid", "*.json")):
        r = subprocess.run([sys.executable, os.path.join(repo_root, "packages", "event-schema", "validate.py"), f, "--hash"],
                           capture_output=True, text=True)
        assert r.returncode == 1, f


def test_standalone_hash_equals_service_hash(repo_root):
    sys.path.insert(0, os.path.join(repo_root, "packages", "event-schema"))
    import validate as v
    for f in glob.glob(_ex(repo_root, "*.json")):
        rec = json.load(open(f))
        assert v.record_hash(rec) == record_hash(rec) == rec["integrity"]["record_sha256"]
