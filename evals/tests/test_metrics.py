"""Every metric against a tiny hand-computed case."""

import pytest

import metrics as m

approx = pytest.approx


def test_p1_misses_and_false_alarms():
    # n=5. True P1 x3: one ok, one predicted P2, one abstained -> 2 misses -> 400 per 1000.
    true = ["P1", "P1", "P1", "P2", "P3"]
    pred = ["P1", "P2", None, "P1", "P3"]
    assert m.p1_misses_per_1000(true, pred) == approx(400.0)
    # Non-P1 truths: 2; one predicted P1 -> 0.5
    assert m.false_alarm_rate(true, pred) == approx(0.5)


def test_empty_inputs_are_zero():
    assert m.p1_misses_per_1000([], []) == 0.0
    assert m.false_alarm_rate([], []) == 0.0


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        m.p1_misses_per_1000(["P1"], [])


def test_sweep_trades_misses_for_false_alarms():
    items = [
        {"true_tier": "P1", "tier": "P2", "escalated_tier": "P1", "uncertainty": 0.6},
        {"true_tier": "P1", "tier": "P1", "escalated_tier": "P1", "uncertainty": 0.1},
        {"true_tier": "P3", "tier": "P3", "escalated_tier": "P2", "uncertainty": 0.4},
        {"true_tier": "P2", "tier": "P2", "escalated_tier": "P1", "uncertainty": 0.8},
    ]
    rows = m.sweep(items, [0.9, 0.7, 0.5, 0.0])
    # th=0.9: none escalate -> 1 miss of 4 = 250; no false alarms.
    assert rows[0] == {"threshold": 0.9, "p1_misses_per_1000": 250.0, "false_alarm_rate": 0.0}
    # th=0.7: item4 escalates -> P2 truth becomes P1 alarm: 1 of 2 non-P1 = 0.5; miss remains.
    assert rows[1]["p1_misses_per_1000"] == approx(250.0)
    assert rows[1]["false_alarm_rate"] == approx(0.5)
    # th=0.5: item1 escalates -> no misses.
    assert rows[2]["p1_misses_per_1000"] == 0.0
    assert rows[2]["false_alarm_rate"] == approx(0.5)
    # th=0: item3 escalates to P2 (not P1) -> same.
    assert rows[3]["p1_misses_per_1000"] == 0.0
    assert rows[3]["false_alarm_rate"] == approx(0.5)


def test_top_k_accuracy():
    true = ["a", "b", "c", "d"]
    pred = ["b", "a", "d", "c"]
    assert m.top_k_accuracy(true, pred, k=2) == 1.0  # {a,b} vs {b,a}
    assert m.top_k_accuracy(true, pred, k=3) == approx(2 / 3)  # {a,b,c} vs {b,a,d}
    assert m.top_k_accuracy([], [], k=20) == 0.0


def test_location_accuracy():
    pairs = [
        ("4410 Bissonnet St", "4410 bissonnet st."),  # exact
        ("behind the Kroger on Westheimer", "Kroger on Westheimer"),  # partial
        ("8120 Fondren Rd", "9000 Main St"),  # wrong
        ("12 Oak Ave", None),  # missing
    ]
    r = m.location_accuracy(pairs)
    assert r["location_exact"] == approx(0.25)
    assert r["location_partial"] == approx(0.5)


def test_count_exact():
    assert m.count_exact([(3, 3), (2, 3), (None, None), (4, None)]) == approx(0.5)


def test_set_f1():
    true = [{"child", "elderly"}, {"medical"}]
    pred = [{"child"}, {"medical", "disabled"}]
    # tp=2, fp=1, fn=1 -> P=2/3 R=2/3 F1=2/3
    assert m.set_f1(true, pred) == approx(2 / 3)
    assert m.set_f1([set()], [set()]) == 1.0
    assert m.set_f1([{"a"}], [set()]) == 0.0


def test_duplicate_pairwise_f1():
    true = [["a", "b", "c"], ["d", "e"]]  # pairs: ab ac bc de
    pred = [["a", "b"], ["d", "e", "f"]]  # pairs: ab de df ef
    r = m.duplicate_pairwise_f1(true, pred)  # tp=2
    assert r["precision"] == approx(0.5)
    assert r["recall"] == approx(0.5)
    assert r["f1"] == approx(0.5)
    assert m.duplicate_pairwise_f1([], [])["f1"] == 1.0
    assert m.duplicate_pairwise_f1([["a", "b"]], [])["f1"] == 0.0


def test_abstain_metrics():
    true = [True, True, True, False, False]
    pred = [True, True, False, True, False]
    tf = ["location", "counts", "location", None, None]
    pf = ["location", "location", None, "location", None]
    r = m.abstain_metrics(true, pred, tf, pf)
    assert r["abstain_precision"] == approx(2 / 3)  # tp=2 of 3 predicted
    assert r["abstain_recall"] == approx(2 / 3)  # tp=2 of 3 true
    assert r["follow_up_field_rate"] == approx(0.5)  # 1 of the 2 matched
    assert m.abstain_metrics(true, pred)["follow_up_field_rate"] == 0.0


def test_reliability_bins_and_ece():
    conf = [0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 1.0]
    ok = [True, True, True, False, False, False, True]
    bins = m.reliability_bins(conf, ok, n_bins=10)
    assert [b["count"] for b in bins] == [2, 5]  # 0.1 bin; 0.9 and 1.0 share the last bin
    assert bins[1]["accuracy"] == approx(0.8)  # 4 of 5
    assert bins[1]["confidence"] == approx(0.92)  # (4*0.9+1.0)/5
    # ECE = 2/7*|0-0.1| + 5/7*|0.8-0.92| = 0.2/7 + 0.6/7 = 0.8/7
    assert m.expected_calibration_error(conf, ok) == approx(0.8 / 7)


def test_consistency_pct():
    groups = [["P1", "P1", "P1"], ["P2", "P3"], ["P1", "P1"], ["P3"]]
    assert m.consistency_pct(groups) == approx(200 / 3)  # 2 of 3 multi-member groups agree
    assert m.consistency_pct([]) == 0.0
