"""Review PR-3: the judges' table is scored like the product works (H-01, H-03, H-04, O-03,
F-04, L-11). Every test here failed on main before PR-3."""

import json

import pytest

from rescue_evals.baselines.stored import StoredSystem
from rescue_evals.loaders import EvalSetError, load_cases
from rescue_evals.report import build_summary, pool_metrics
from rescue_evals.runner import GoldExtractionSystem, run_set
from rescue_shared.models import Extracted


def ex(loc, people=2, vulnerable=(), hazard="fire", conf=0.9):
    return {
        "location": {"text": loc, "type": "address", "confidence": 0.9},
        "people_count": {"value": people, "confidence": 0.9},
        "vulnerable": [{"kind": k, "confidence": 0.9} for k in vulnerable],
        "hazards": [{"kind": hazard, "confidence": conf}],
        "time_ref": {"text": None, "is_stale_post": False, "confidence": 0.9},
        "is_request": {"value": True, "confidence": 0.95},
        "spans": [],
        "needs": [],
        "request_kind": "rescue",
    }


def case(cid, extracted, outcome="prioritized", tier="P1", group=None, set_="B", **extra):
    d = {"outcome": outcome}
    if tier:
        d["priority_tier"] = tier
    return {
        "id": cid,
        "raw_text": extra.pop("text", f"text of {cid}"),
        "gold_extracted": extracted,
        "gold_decision": d,
        "gold_duplicate_group": group,
        "set": set_,
        "split": "test",
        **extra,
    }


def load(tmp_path, rows, name="b.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return load_cases(p)


class Extractor:
    """Returns a fixed extraction per case id (or raises for ids in ``crash``); real engine."""

    name = "stub"
    model_version = "stub:per-case"

    def __init__(self, by_id, crash=()):
        self.by_id, self.crash = by_id, set(crash)

    def extract_case(self, c):
        if c.id in self.crash:
            raise RuntimeError("model fell over")
        return Extracted.model_validate(self.by_id[c.id])

    def extract(self, raw_text):  # pragma: no cover - the runner prefers extract_case
        raise AssertionError("runner must call extract_case")

    def decide(self, engine_input):
        return GoldExtractionSystem([]).decide(engine_input)


# --- H-01: duplicate context in arrival order --------------------------------------------------


def test_same_household_by_count_is_a_scored_duplicate(tmp_path):
    rows = [
        case("a", ex("4300 Dixie Dr", people=4), group="g1"),
        case("b", ex("4300 Dixie Dr.", people=4), outcome="duplicate", group="g1"),
        case("c", ex("12 Elm St", people=4)),
    ]
    cases = load(tmp_path, rows)
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    m = res["metrics"]
    assert m["decision.duplicate_pairwise_f1"] == 1.0  # was 0.0: candidates were always []
    assert res["metric_counts"]["decision.duplicate_pairwise_f1"] == {"tp": 1, "fp": 0, "fn": 0}
    assert m["decision.outcome_accuracy"] == 1.0
    assert res["failures"]["decision.duplicate_pairwise_f1"] == []


def test_contact_and_received_at_are_read_and_drive_duplicates(tmp_path):
    # one person (below the count rule) matches by contact; file order is not arrival order
    rows = [
        case(
            "late",
            ex("25 Sunset Ct", people=1),
            outcome="duplicate",
            group="g",
            contact="+1 (713) 555-0101",
            received_at="2026-11-07T10:05:00Z",
        ),
        case(
            "early",
            ex("25 Sunset Ct", people=1),
            group="g",
            contact="713-555-0101",
            received_at="2026-11-07T10:00:00Z",
        ),
        # same household text 13 h later: outside the 12 h window, a new request
        case(
            "next_day",
            ex("25 Sunset Ct", people=1),
            contact="7135550101",
            received_at="2026-11-07T23:01:00Z",
        ),
    ]
    cases = load(tmp_path, rows)
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert res["metrics"]["decision.outcome_accuracy"] == 1.0, res["failures"]
    assert res["metrics"]["decision.duplicate_pairwise_f1"] == 1.0
    assert res["case_ids"] == ["late", "early", "next_day"]  # reported in the given order


def test_bad_received_at_is_refused(tmp_path):
    with pytest.raises(EvalSetError, match="received_at"):
        load(tmp_path, [case("a", ex("1 Oak St"), received_at="2026-11-07 10:00")])


def test_duplicate_f1_left_out_when_nothing_to_pair(tmp_path):
    cases = load(tmp_path, [case("a", ex("1 Oak St")), case("b", ex("2 Oak St"))])
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert "decision.duplicate_pairwise_f1" not in res["metrics"]  # was a vacuous 1.0


def test_duplicate_chain_is_one_group(tmp_path):
    rows = [
        case(i, ex("9 Pine St", people=3), outcome="duplicate" if i != "a" else "prioritized",
             group="g")
        for i in "abc"
    ]  # fmt: skip
    cases = load(tmp_path, rows)
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert res["metric_counts"]["decision.duplicate_pairwise_f1"] == {"tp": 3, "fp": 0, "fn": 0}


# --- H-03: per-metric denominators -------------------------------------------------------------


def test_set_without_gold_extraction_does_not_dilute_the_pool(tmp_path):
    a_rows = [case(f"a{i}", None, set_="A") for i in range(100)]
    b_rows = [case(f"b{i}", ex(f"{i} Oak St")) for i in range(10)]
    a = load(tmp_path, a_rows, "a.jsonl")
    b = load(tmp_path, b_rows, "b.jsonl")
    perfect = Extractor({c.id: ex("x") for c in a} | {c.id: c.gold_extracted for c in b})
    doc_a, doc_b = run_set(perfect, a, "A"), run_set(perfect, b, "B")
    for key in ("extraction.location_exact", "extraction.vulnerable_f1", "calibration.ece"):
        assert key not in doc_a["metrics"]  # was 0.0 / 1.0 written with weight 100
    assert doc_b["metric_n"]["extraction.location_exact"] == 10
    pooled, ns = pool_metrics([doc_a, doc_b])
    assert pooled["extraction.location_exact"] == 1.0  # was 0.0909
    assert ns["extraction.location_exact"] == 10
    assert ns["decision.outcome_accuracy"] == 110


def test_crashed_extractions_are_f1_misses(tmp_path):
    rows = [case(f"c{i}", ex(f"{i} Oak St", vulnerable=["child"])) for i in range(4)]
    cases = load(tmp_path, rows)
    sys = Extractor({c.id: c.gold_extracted for c in cases}, crash={"c0", "c1"})
    res = run_set(sys, cases, "B")
    # 2 found, 2 missed (crashed): P=1, R=0.5 -> F1 2/3 (was 1.0: crashes left the denominator)
    assert res["metrics"]["extraction.vulnerable_f1"] == pytest.approx(2 / 3)
    assert res["metric_counts"]["extraction.vulnerable_f1"] == {"tp": 2, "fp": 0, "fn": 2}
    assert set(res["failures"]["extraction.vulnerable_f1"]) == {"c0", "c1"}
    assert res["metrics"]["extraction.location_exact"] == 0.5


def test_empty_denominators_are_left_out_not_zero(tmp_path):
    cases = load(tmp_path, [case("a", ex("1 Oak St"))])
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    m = res["metrics"]
    for key in (
        "decision.abstain_recall",  # no gold abstain
        "decision.abstain_precision",  # no predicted abstain
        "decision.follow_up_field_rate",
        "decision.false_alarm_rate",  # no non-P1 request
    ):
        assert key not in m and key not in res["metric_n"] and key not in res["failures"]
    assert set(res["failures"]) == set(m) == set(res["metric_n"])


def test_report_pools_f1_from_counts_and_rows_carry_n(tmp_path):
    def doc(set_, n, counts, value):
        return {
            "system": "ours",
            "set": set_,
            "n": n,
            "model_version": "m",
            "metrics": {"extraction.hazard_f1": value, "decision.tier_accuracy": 0.5},
            "metric_n": {"extraction.hazard_f1": sum(counts.values()), "decision.tier_accuracy": 2},
            "metric_counts": {"extraction.hazard_f1": counts},
        }

    docs = [
        doc("B", 50, {"tp": 9, "fp": 1, "fn": 0}, 0.947),
        doc("C", 5, {"tp": 0, "fp": 0, "fn": 10}, 0.0),
    ]
    summary = build_summary(docs)
    rows = {r["metric"]: r for r in summary["main"]["rows"]}
    # tp=9 fp=1 fn=10 -> P=0.9 R=0.474 F1=0.621 (an n-weighted mean of F1s says 0.86)
    assert rows["extraction.hazard_f1"]["values"]["ours"] == pytest.approx(2 * 9 / (18 + 1 + 10))
    assert rows["extraction.hazard_f1"]["n"]["ours"] == 20
    assert rows["decision.tier_accuracy"]["n"]["ours"] == 4
    assert summary["results"][0]["metric_n"]["decision.tier_accuracy"] == 2


def test_old_documents_without_metric_n_still_pool_by_n():
    docs = [
        {"system": "ours", "set": "B", "n": 30, "metrics": {"decision.outcome_accuracy": 1.0}},
        {"system": "ours", "set": "C", "n": 10, "metrics": {"decision.outcome_accuracy": 0.0}},
    ]
    pooled, ns = pool_metrics(docs)
    assert pooled["decision.outcome_accuracy"] == 0.75 and ns["decision.outcome_accuracy"] == 40


# --- H-04: Top-20 from the board order ---------------------------------------------------------


def test_top20_follows_the_board_order(tmp_path):
    # 22 P1 fires at different addresses; the two that arrive last have a child, so the board
    # puts them first (more vulnerable people first, then more people, then older first).
    rows = [
        case(f"r{i:02d}", ex(f"{i} Main St", vulnerable=["child"] if i >= 20 else ()))
        for i in range(22)
    ]
    cases = load(tmp_path, rows)
    gold = run_set(GoldExtractionSystem(cases), cases, "B")
    assert gold["metrics"]["decision.top20_accuracy"] == 1.0
    assert gold["metric_n"]["decision.top20_accuracy"] == 20
    # a system that misses every child ranks by arrival and drops the two from its top 20
    blind = {c.id: {**c.gold_extracted.model_dump(mode="json"), "vulnerable": []} for c in cases}
    res = run_set(Extractor(blind), cases, "B")
    assert res["metrics"]["decision.top20_accuracy"] == pytest.approx(18 / 20)
    assert res["failures"]["decision.top20_accuracy"] == ["r20", "r21"]


def test_top20_counts_a_household_once(tmp_path):
    # gold: one household (two messages) + 20 others. A system that misses the merge shows the
    # household twice, pushing a real row out of its top 20.
    rows = [case("h1", ex("7 Bay St", people=1, vulnerable=["child", "elderly"]), group="h")]
    rows.append(
        case("h2", ex("7 Bay St", people=1, vulnerable=["child", "elderly"]),
             outcome="duplicate", group="h", contact="555-0100")
    )  # fmt: skip
    rows[0]["contact"] = "555-0100"
    rows += [case(f"r{i:02d}", ex(f"{i} Main St")) for i in range(20)]
    cases = load(tmp_path, rows)
    assert run_set(GoldExtractionSystem(cases), cases, "B")["metrics"][
        "decision.top20_accuracy"
    ] == 1.0
    split = {c.id: c.gold_extracted for c in cases}
    split["h2"] = Extracted.model_validate(ex("7 Bay Street East", 1, ["child", "elderly"]))
    res = run_set(Extractor(split), cases, "B")
    assert res["metrics"]["decision.top20_accuracy"] == pytest.approx(19 / 20)


# --- F-04: identical texts are separate cases --------------------------------------------------


def test_identical_texts_are_not_conflated(tmp_path):
    rows = [
        case("a", ex("1 Oak St"), text="SOS"),
        case("b", ex("99 Elm St"), text="SOS"),
    ]
    cases = load(tmp_path, rows)
    gold = run_set(GoldExtractionSystem(cases), cases, "B")
    assert gold["metrics"]["extraction.location_exact"] == 1.0  # was 0.5
    stored = tmp_path / "stored.jsonl"
    stored.write_text(
        "\n".join(
            json.dumps({"id": c.id, "extracted": c.gold_extracted.model_dump(mode="json")})
            for c in cases
        )
    )
    res = run_set(StoredSystem(cases, stored), cases, "B")
    assert res["metrics"]["extraction.location_exact"] == 1.0  # was 0.5


# --- O-03 / L-11: headline denominator, duplicates' tiers, provisional P1, median -------------


def test_headline_is_per_urgent_request(tmp_path):
    rows = [case("p1", ex("1 Oak St"))] + [
        case(f"n{i}", ex(f"{i} Elm St"), outcome="not_a_request", tier=None) for i in range(3)
    ]
    cases = load(tmp_path, rows)
    blind = {c.id: c.gold_extracted for c in cases}
    blind["p1"] = Extracted.model_validate(ex("1 Oak St", hazard="trapped"))  # P2: a miss
    res = run_set(Extractor(blind), cases, "B")
    assert res["metrics"]["decision.p1_misses_per_1000"] == 1000.0  # 1 of 1 urgent
    assert res["metrics"]["decision.p1_misses_per_1000_requests"] == 250.0  # 1 of 4 requests
    assert res["metric_n"]["decision.p1_misses_per_1000"] == 1


def test_gold_p1_duplicate_on_the_p1_row_is_not_a_miss(tmp_path):
    rows = [
        case("a", ex("4300 Dixie Dr", people=4), group="g"),
        case("b", ex("4300 Dixie Dr", people=4), outcome="duplicate", tier="P1", group="g"),
    ]
    cases = load(tmp_path, rows)
    res = run_set(GoldExtractionSystem(cases), cases, "B")
    assert res["metrics"]["decision.p1_misses_per_1000"] == 0.0
    assert res["metric_n"]["decision.p1_misses_per_1000"] == 2  # was 1: b's tier was dropped
    assert res["metrics"]["decision.tier_accuracy"] == 1.0


def test_p1_abstain_with_provisional_p1_is_reported_apart(tmp_path):
    rows = [case("a", ex("1 Oak St")), case("b", ex("2 Oak St"))]
    cases = load(tmp_path, rows)
    preds = {c.id: c.gold_extracted.model_dump(mode="json") for c in cases}
    preds["a"]["location"] = {"text": None, "type": "none", "confidence": 0.9}  # abstain, P1
    preds["b"]["hazards"] = [{"kind": "trapped", "confidence": 0.9}]  # P2: a hard miss
    res = run_set(Extractor(preds), cases, "B")
    assert res["metrics"]["decision.p1_misses_per_1000"] == 1000.0
    assert res["metrics"]["decision.p1_hard_misses_per_1000"] == 500.0
    assert res["failures"]["decision.p1_hard_misses_per_1000"] == ["b"]


def test_latency_has_the_median(tmp_path):
    cases = load(tmp_path, [case("a", ex("1 Oak St"))])
    lat = run_set(GoldExtractionSystem(cases), cases, "B")["latency"]
    assert lat["median_ms_per_request"] >= 0
