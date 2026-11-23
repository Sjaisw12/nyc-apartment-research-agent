"""Building Attention Summary: deterministic labels, provenance and wording. Synthetic data."""

from __future__ import annotations

import re

import pytest

from nyc_apartment_agent.investigation.attention import compute_attention
from nyc_apartment_agent.investigation.schema import (
    EvidenceItem,
    ResearchReport,
    ResolutionView,
    RunInfo,
    SourceStatus,
)
from nyc_apartment_agent.investigation.verify import check_attention, wording_problems

KEY = ("hpd_violations", "hpd_complaints", "dob_complaints", "ecb_violations")
PROHIBITED = re.compile(
    r"\b(safe|unsafe|dangerous|avoid renting|should not rent|negligent|no risk|approved|"
    r"score|percent|probability)\b",
    re.I,
)


def ev(i, source, tags, attribution="this_building", kind="official_finding"):
    return EvidenceItem(
        id=f"{source}:{i}",
        source=source,
        agency="HPD",
        dataset_id="x",
        dataset_name="x",
        record_id=str(i),
        evidence_kind=kind,
        attribution=attribution,
        title="t",
        tags=tags,
        dataset_url="https://data.cityofnewyork.us/",
    )


def src(name, status="records_found", complete=True, detail=""):
    return SourceStatus(
        source=name,
        agency="HPD",
        dataset_id="x",
        dataset_name=name,
        dataset_url="https://data.cityofnewyork.us/",
        status=status,
        status_label=status,
        status_detail=detail,
        complete=complete,
        records=0,
        data_as_of="2026-11-20T00:00:00",
    )


def report(evidence, sources=None, level="building", questions=None):
    return ResearchReport(
        report_id="r",
        generated_at="2026-11-23T00:00:00",
        input_address="1 Test St",
        resolution=ResolutionView(
            outcome="verified",
            explanation="",
            level=level,
            confidence="high",
            confidence_label="High",
            bbl="1000010001",
            bin="1000001" if level == "building" else None,
        ),
        headline="h",
        evidence=evidence,
        sources=sources if sources is not None else [src(s) for s in KEY],
        questions_to_ask=questions or ["Ask who the managing agent is."],
        run=RunInfo(mode="live", method="deterministic_baseline"),
    )


def summarize(r):
    a = compute_attention(r)
    r.attention = a
    assert check_attention(r, r.evidence_index()) == []
    for t in [a.headline, a.label_meaning, *a.reasons, *(p.text for p in a.points)]:
        assert not PROHIBITED.search(t), t
        assert wording_problems(t) == [], t
    return a


def test_open_class_c_is_priority_with_verified_counts():
    evidence = [ev(i, "hpd_violations", ["open", "class:C"]) for i in range(3)]
    evidence += [ev(10 + i, "hpd_violations", ["open", "class:B"]) for i in range(2)]
    evidence += [ev(20, "hpd_violations", ["closed", "class:C"])]
    a = summarize(report(evidence))
    assert a.label == "priority_follow_up"
    p = a.points[0]
    assert p.level == "priority" and "5 open violations" in p.text and "3 in Class C" in p.text
    assert len(p.evidence_ids) == 5 and "hpd_violations:20" not in p.evidence_ids


def test_complaints_alone_are_items_to_check_and_called_reports():
    evidence = [
        ev(i, "hpd_complaints", ["recent_12m", "category:HEAT/HOT WATER"]) for i in range(4)
    ]
    a = summarize(report(evidence))
    assert a.label == "some_items_to_check"
    text = a.points[0].text
    assert "not proof of a problem" in text and "violation" not in text.lower()


def test_unavailable_key_source_never_reassures():
    sources = [src(s) for s in KEY[:3]] + [src("ecb_violations", "source_unavailable", False)]
    a = summarize(report([], sources))
    assert a.label == "insufficient_information"
    assert a.coverage.level == "limited"


def test_priority_still_shown_when_another_source_is_unavailable():
    sources = [src(s) for s in KEY[:3]] + [src("ecb_violations", "source_unavailable", False)]
    a = summarize(report([ev(1, "hpd_violations", ["open", "class:C"])], sources))
    assert a.label == "priority_follow_up" and a.coverage.level == "limited"


def test_partial_source_with_nothing_found_is_insufficient():
    sources = [src(s) for s in KEY[:2]] + [
        src(
            "dob_complaints",
            "incomplete_pagination",
            False,
            "upstream_recent_gap: the DOB dataset currently holds no complaints entered in 2025",
        ),
        src("ecb_violations"),
    ]
    a = summarize(report([], sources))
    assert a.label == "insufficient_information"
    dob = next(c for c in a.coverage.sources if c.source == "dob_complaints")
    assert dob.state == "partial" and "missing recent complaints" in dob.note


def test_historical_orders_are_never_called_current():
    evidence = [ev(1, "dob_complaints", ["order_event", "closed"], kind="reported_allegation")]
    a = summarize(report(evidence))
    p = next(p for p in a.points if p.kind == "historical_orders")
    assert "historical" in p.text and "does not show whether any order applies today" in p.text
    assert a.label == "some_items_to_check"


def test_other_building_records_are_not_counted():
    evidence = [
        ev(i, "hpd_violations", ["open", "class:C"], "other_building_on_lot") for i in range(5)
    ]
    a = summarize(report(evidence))
    assert a.label == "no_priority_items"
    assert any(p.kind == "other_buildings" for p in a.points)
    assert not any(p.kind == "housing_violations" for p in a.points)


def test_no_hpd_record_explains_hpd_may_not_apply():
    sources = [src(s) for s in KEY] + [src("hpd_buildings", "no_records_found")]
    a = summarize(report([], sources))
    assert a.label == "no_priority_items"
    assert any(p.kind == "hpd_not_applicable" for p in a.points)
    assert "not a safety rating" in a.label_meaning


def test_no_records_wording_does_not_claim_no_history():
    a = summarize(report([ev(1, "hpd_violations", ["closed", "class:B"])]))
    assert a.label == "no_priority_items"
    assert "Older, closed records may still" in a.headline


def test_tax_lot_only_is_insufficient():
    evidence = [ev(1, "hpd_violations", ["open", "class:C"], "lot_level")]
    a = summarize(report(evidence, level="tax_lot"))
    assert a.label == "insufficient_information"
    assert "this tax lot" in a.points[0].text


def test_unverified_resolution_has_no_summary():
    r = report([])
    r.resolution.outcome = "ambiguous"
    assert compute_attention(r) is None


def test_tampered_count_fails_verification():
    r = report([ev(i, "hpd_violations", ["open", "class:C"]) for i in range(2)])
    r.attention = compute_attention(r)
    r.attention.points[0].facts[0].fact.value = 9
    assert any("says 9" in p for p in check_attention(r, r.evidence_index()))


def test_unsupported_number_in_text_fails_verification():
    r = report([ev(1, "hpd_violations", ["open", "class:C"])])
    r.attention = compute_attention(r)
    r.attention.points[0].text += " About 70 more are expected."
    assert any("unsupported number" in p for p in check_attention(r, r.evidence_index()))


@pytest.mark.parametrize("phrase", ["This building is safe.", "You should not rent here."])
def test_prohibited_claims_are_caught(phrase):
    r = report([ev(1, "hpd_violations", ["open", "class:C"])])
    r.attention = compute_attention(r)
    r.attention.headline = phrase
    assert any("attention wording" in p for p in check_attention(r, r.evidence_index()))


def test_summary_is_labelled_as_automated_not_ai():
    a = summarize(report([]))
    assert a.generated_by == "automated_evidence_summary"
    assert "not an AI judgment" in a.disclaimer
    assert all("demograph" not in r or "no neighbourhood" in r for r in a.rules)


def test_coverage_dates_accept_http_and_iso_formats():
    sources = [src(s) for s in KEY]
    sources[0].data_as_of = "Sun, 22 Nov 2026 17:25:19 GMT"
    sources[1].data_as_of = "2026-11-04T00:00:00"
    a = summarize(report([], sources))
    assert a.coverage.oldest_data_as_of == "2026-11-04"
    assert a.coverage.newest_data_as_of == "2026-11-22"
