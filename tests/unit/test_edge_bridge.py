import io
import json

from evidence_service.engine import EventLog, build_event
from edge_controller import Bridge


def _lines(n=3):
    log = EventLog()
    for i in range(n):
        log.append(build_event("bite", i + 1, counter=i + 1))
    return [json.dumps(r) for r in log.records]


def test_mixed_stream(tmp_path):
    err = io.StringIO()
    br = Bridge(str(tmp_path / "ev.jsonl"), err=err, echo=True)
    lines = ["booting...", '{"type":"telemetry","tension_n":0.4}', "{not json"] + _lines()
    br.run(lines)
    st = br.stats
    assert (st.accepted, st.rejected, st.telemetry, st.text_lines) == (3, 0, 1, 2)
    assert len(open(tmp_path / "ev.jsonl").read().splitlines()) == 3
    assert "[fw] booting..." in err.getvalue()


def test_json_array_line(tmp_path):
    br = Bridge(str(tmp_path / "ev.jsonl"), err=io.StringIO())
    br.run(["[" + ",".join(_lines(2)) + "]"])
    assert br.stats.accepted == 2


def test_tampered_record_rejected_and_kept(tmp_path):
    ls = _lines(2)
    r = json.loads(ls[1])
    r["classification"]["result"] = "rejected_noise"
    br = Bridge(str(tmp_path / "ev.jsonl"), err=io.StringIO())
    br.run([ls[0], json.dumps(r)])
    assert br.stats.accepted == 1 and br.stats.rejected == 1 and br.stats.hash_mismatches == 1
    assert (tmp_path / "ev.jsonl.rejected.jsonl").exists()


def test_chain_break_detected(tmp_path):
    ls = _lines(3)
    br = Bridge(str(tmp_path / "ev.jsonl"), err=io.StringIO())
    br.run([ls[0], ls[2]])
    assert br.stats.rejected == 1 and br.stats.chain_breaks == 1


def test_unsealed_needs_explicit_seal(tmp_path):
    r = build_event("bite", 1)  # unsealed
    br = Bridge(str(tmp_path / "a.jsonl"), err=io.StringIO())
    br.run([json.dumps(r)])
    assert br.stats.rejected == 1
    br2 = Bridge(str(tmp_path / "b.jsonl"), err=io.StringIO(), seal_unsealed=True)
    br2.run([json.dumps(build_event("bite", 1))])
    assert br2.stats.accepted == 1 and br2.stats.sealed_by_host == 1
    rec = json.loads(open(tmp_path / "b.jsonl").readline())
    assert rec["integrity"]["sealed_by"].startswith("edge-controller-host")


def test_resume_existing_store(tmp_path):
    ls = _lines(3)
    p = str(tmp_path / "ev.jsonl")
    Bridge(p, err=io.StringIO()).run(ls[:2])
    br = Bridge(p, err=io.StringIO())
    br.run(ls[2:])
    assert br.stats.accepted == 1 and br.stats.chain_breaks == 0
