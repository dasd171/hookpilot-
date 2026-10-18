import pytest

from evidence_service.classifier import (FW_DEFAULTS, PROVISIONAL_NOTE, Thresholds, classify, decide_fw,
                                         decide_from_features, detect_variant)
from evidence_service.synth import SCENARIOS, generate


def test_generator_is_deterministic_and_seed_sensitive():
    a, b, c = generate("bite", 5), generate("bite", 5), generate("bite", 6)
    assert a.tension_n == b.tension_n
    assert a.tension_n != c.tension_n
    assert len(a.tension_n) == 400 and a.t_ms[1] == 20.0


def test_longer_duration_only_appends():
    a, b = generate("noise_current", 3), generate("noise_current", 3, duration_ms=10000)
    assert b.tension_n[:400] == a.tension_n


def test_unknown_scenario():
    with pytest.raises(ValueError):
        generate("shark_attack", 1)


@pytest.mark.parametrize("scenario,seed,expected", [
    ("idle", 1, "no_event"), ("bite", 1, "bite_candidate_confirmed"), ("manual_handling", 1, "rejected_manual_handling"),
    ("sensor_failure", 1, "rejected_sensor_fault"), ("excessive_tension", 1, "bite_candidate_confirmed"),
])
def test_known_outcomes(scenario, seed, expected):
    assert classify(generate(scenario, seed).samples()).result == expected


def test_every_decision_has_reason_and_trace():
    for sc in SCENARIOS:
        d = classify(generate(sc, 11).samples())
        assert d.reason and d.rule_trace


def test_thresholds_labelled_provisional():
    rec = Thresholds.defaults("fw").to_record()
    assert rec["status"] == PROVISIONAL_NOTE
    assert rec["decision_window_ms"] == FW_DEFAULTS["decision_window_ms"]


def test_threshold_change_changes_outcome():
    sig = generate("bite", 1).samples()
    th = Thresholds.defaults("fw")
    th.trigger_delta_n = 50.0
    assert classify(sig, th).result == "no_event"


def test_single_rule_set():
    # one classifier rule set since 2026-10-18: every record (firmware, web demo, Python) replays with it
    assert detect_variant({"classification": {"thresholds": {"decision_window_ms": 600}}}) == "fw"
    assert detect_variant({"classification": {"implementation": "ts-port-of-firmware-cpp@2026-10-18"}}) == "fw"
    with pytest.raises(ValueError):
        Thresholds.defaults("web")


def test_rule_order_fw():
    th = Thresholds.defaults("fw")
    base = dict(sensor_health="ok", peak_delta_n=2.0, duration_above_ms=400, continuous_above_ms=400,
                oscillation_count=0, max_rate_n_per_s=10.0)
    assert decide_fw(base, th)[0] == "bite_candidate_confirmed"
    assert decide_fw(base | {"sensor_health": "stuck", "peak_delta_n": 99}, th)[0] == "rejected_sensor_fault"
    assert decide_fw(base | {"peak_delta_n": 6.0, "oscillation_count": 9}, th)[0] == "rejected_manual_handling"
    assert decide_fw(base | {"oscillation_count": 9, "max_rate_n_per_s": 0.1}, th)[0] == "rejected_vibration"
    # rate checked before transient (firmware order since 2026-10-18)
    assert decide_fw(base | {"max_rate_n_per_s": 0.1, "continuous_above_ms": 40}, th)[0] == "rejected_noise"
    assert decide_fw(base | {"continuous_above_ms": 40}, th)[0] == "rejected_transient"


def test_thresholds_from_record_ignores_legacy_variant_key():
    th = Thresholds.from_record({"variant": "web", "manual_handling_delta_n": 7.5})
    assert th.variant == "fw" and th.manual_handling_delta_n == 7.5 and th.decision_window_ms == 600.0


def test_rejects_non_50hz():
    from evidence_service.classifier import StreamingClassifier
    with pytest.raises(ValueError):
        StreamingClassifier(sample_rate_hz=100.0)
