import json
import subprocess
import shutil

import pytest

from evidence_service.canonical import canonical_json, js_number, record_hash, seal


@pytest.mark.parametrize("x,s", [(1.0, "1"), (0.1, "0.1"), (1e-7, "1e-7"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
                                 (-0.0, "0"), (123456789012345680000.0, "123456789012345680000"), (0.000001, "0.000001"),
                                 (5e-324, "5e-324"), (-3e22, "-3e+22"), (12, "12"), (2.5e-10, "2.5e-10")])
def test_js_number_formatting(x, s):
    assert js_number(x) == s


def test_rejects_nan_inf():
    with pytest.raises(ValueError):
        js_number(float("nan"))
    with pytest.raises(ValueError):
        canonical_json({"a": float("inf")})


def test_sorted_keys_no_whitespace_utf8():
    assert canonical_json({"b": 1, "a": {"d": [1.5, None, True], "c": "é"}}) == '{"a":{"c":"é","d":[1.5,null,true]},"b":1}'


def test_record_hash_ignores_existing_hash_and_is_stable():
    r = {"x": 1, "integrity": {"record_sha256": "abc", "hash_algorithm": "sha256"}}
    h1 = record_hash(r)
    r["integrity"]["record_sha256"] = "zzz"
    assert record_hash(r) == h1
    assert r["integrity"]["record_sha256"] == "zzz"  # not mutated


def test_int_vs_float_equivalence():
    assert record_hash({"a": 1, "integrity": {}}) == record_hash({"a": 1.0, "integrity": {}})


def test_seal_sets_fields():
    r = seal({"a": 2}, "prev")
    assert r["integrity"]["prev_record_sha256"] == "prev"
    assert r["integrity"]["record_sha256"] == record_hash(r)
    assert r["integrity"]["signature"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_matches_json_stringify_on_random_numbers():
    import random
    random.seed(3)
    vals = [random.uniform(-1e3, 1e3) for _ in range(300)] + [random.random() * 10 ** random.randint(-25, 25) for _ in range(300)]
    out = subprocess.run(["node", "-e", "let v=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(v.map(String)))"],
                         input=json.dumps(vals), capture_output=True, text=True, check=True).stdout
    assert [js_number(v) for v in vals] == json.loads(out)
