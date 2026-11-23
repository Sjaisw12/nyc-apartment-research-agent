"""Phase 3 Step 0: deterministic cited report on the five recorded addresses (offline).

Replays the committed Phase 2 cassettes through the real resolver and adapters, builds the
fixed-order report, and re-verifies every finding against the evidence it cites.
Expected identities are the approved Phase 1 labels; attribution expectations are the
accepted Phase 2 ground truth (e.g. BIN 2117584 at 1134 West Farms Rd is another building).
"""

import json

import pytest

from nyc_apartment_agent.investigation.baseline import build_baseline_report
from nyc_apartment_agent.investigation.collect import DEMO_ADDRESSES, collect_demo
from nyc_apartment_agent.investigation.render import render_markdown
from nyc_apartment_agent.investigation.schema import (
    Category,
    FactCheck,
    Finding,
    Importance,
    Scope,
    SectionId,
)
from nyc_apartment_agent.investigation.verify import verify_report, wording_problems
from nyc_apartment_agent.records.redaction import needs_redaction

IDENTITY = {
    "west_farms_1134": ("2027540005", "2006185", "building"),
    "west_17_220": ("1007660055", "1081543", "building"),
    "west_farms_1217": ("2029930031", None, "tax_lot"),
    "fifth_ave_350": ("1008350041", "1015862", "building"),
    "acp_1845": ("1018227502", "1054974", "building"),
}


FREE_TEXT = (
    "Description (unit detail redacted)",
    "Agency resolution (allowlisted text)",
    "DOB recorded address",
)


@pytest.fixture(scope="module")
def reports():
    return {k: build_baseline_report(collect_demo(a)) for k, a in DEMO_ADDRESSES.items()}


@pytest.mark.parametrize("key", list(IDENTITY))
def test_identity_matches_approved_labels(reports, key):
    r = reports[key].resolution
    assert (r.bbl, r.bin, r.level) == IDENTITY[key]
    assert r.outcome == "verified"


@pytest.mark.parametrize("key", list(IDENTITY))
def test_every_finding_verifies_against_its_evidence(reports, key):
    v = verify_report(reports[key])
    assert v.passed, v.issues[:5]
    assert v.facts_checked >= 5


@pytest.mark.parametrize("key", list(IDENTITY))
def test_sections_are_in_fixed_order(reports, key):
    order = list(SectionId)
    ids = [s.id for s in reports[key].sections]
    assert ids == sorted(ids, key=order.index)
    assert ids[0] is SectionId.SUMMARY and ids[1] is SectionId.IDENTITY


def test_other_building_records_never_count_as_this_building(reports):
    r = reports["west_farms_1134"]
    ev = r.evidence_index()
    # Ground truth row 2: complaint 2411142 is at 1135 Bryant Ave, BIN 2117584.
    assert ev["dob_complaint:2411142"].attribution == "other_building_on_lot"
    for f in r.findings:
        if f.scope is Scope.THIS_BUILDING:
            assert all(
                ev[i].attribution == "this_building"
                for i in f.evidence_ids
                if ev[i].source not in ("hpd_buildings", "hpd_registrations")
            ), f.id
    vacate = [f for f in r.findings if "dob_complaint:2411142" in f.evidence_ids]
    assert vacate and all(f.section is SectionId.OTHER_BUILDINGS for f in vacate)
    assert all("different building" in f.statement for f in vacate)


def test_orders_are_historical_never_current(reports):
    seen = 0
    for r in reports.values():
        for f in r.findings:
            if f.section is SectionId.ORDERS or "order:" in " ".join(
                t for i in f.evidence_ids for t in r.evidence_index()[i].tags
            ):
                if f.category is Category.HISTORICAL_EVENT:
                    seen += 1
                    assert not wording_problems(f.statement), f.statement
        assert all(
            f.category is Category.HISTORICAL_EVENT
            for f in r.findings
            if f.section is SectionId.ORDERS
        )
    assert seen >= 10


def test_tax_lot_resolution_makes_no_building_claims(reports):
    r = reports["west_farms_1217"]
    assert all(f.scope is not Scope.THIS_BUILDING for f in r.findings)
    assert not any("this building" in f.statement for f in r.findings)
    dob = next(s for s in r.sources if s.source == "dob_complaints")
    assert dob.status == "unsupported_join"
    assert any(f.category is Category.UNAVAILABLE and "DOB" in f.title for f in r.findings)


def test_incomplete_sources_stay_visibly_incomplete(reports):
    for key in ("west_farms_1134", "acp_1845"):
        r = reports[key]
        sr = next(s for s in r.sources if s.source == "sr311")
        assert sr.status == "incomplete_pagination" and not sr.complete
        assert any("partial retrieval" in f.title for f in r.findings)
        stat = next(s for s in r.stats if s.label.startswith("311"))
        assert stat.value.endswith("+")


def test_zero_records_is_a_checked_zero_not_silence(reports):
    r = reports["fifth_ave_350"]
    f = next(f for f in r.findings if f.title == "No HPD violations on file")
    assert f.fact.value == 0 and f.category is Category.VERIFIED_RECORD


def test_condo_billing_lot_resolution(reports):
    r = reports["acp_1845"]
    assert r.resolution.bbl.endswith("7502")
    assert any(f.scope is Scope.OTHER_BUILDING_ON_LOT for f in r.findings)


def test_ambiguous_address_offers_options_and_attaches_nothing():
    r = build_baseline_report(collect_demo("100 Broadway"))
    assert r.resolution.outcome == "ambiguous" and len(r.resolution.options) == 2
    assert r.evidence == [] and r.findings == [] and r.questions_to_ask == []


def test_selected_option_is_resolved_at_the_level_its_evidence_supports():
    amb = collect_demo("100 Broadway")
    opt = amb.resolution.options[0]
    r = build_baseline_report(
        collect_demo("100 Broadway", selected={"bbl": opt.bbl, "bin": opt.bin})
    )
    assert r.resolution.outcome == "verified" and r.resolution.user_selected
    # GeoSearch-only BIN evidence (not cross-verified) never becomes building level.
    assert r.resolution.level == (
        "building" if opt.bin_evidence.value == "cross_verified" else "tax_lot"
    )


def test_unknown_option_is_ignored():
    r = build_baseline_report(
        collect_demo("100 Broadway", selected={"bbl": "1000000000", "bin": None})
    )
    assert r.resolution.outcome == "ambiguous"


def test_no_match_abstains():
    r = build_baseline_report(collect_demo("zzqx qqzz nowhere"))
    assert r.resolution.outcome == "no_reliable_match" and r.resolution.confidence == "none"
    assert r.evidence == [] and r.findings == []


def test_address_outside_demo_set_is_not_guessed():
    r = build_baseline_report(collect_demo("530 2nd Street, Brooklyn, NY 11215"))
    assert r.resolution.outcome == "verified" and not r.resolution.in_demo_dataset
    assert all(s.status == "not_in_demo" for s in r.sources)
    assert r.evidence == []
    assert any(
        f.importance is Importance.HIGH and f.category is Category.UNAVAILABLE for f in r.findings
    )


def test_report_contains_no_unit_detail_or_311_keys(reports):
    for r in reports.values():
        for e in r.evidence:
            # Free-text fields (official category names such as "over 6 Story" are fine).
            for k in FREE_TEXT:
                v = e.details.get(k)
                if isinstance(v, str):
                    assert not needs_redaction(v), (e.id, v)
            if e.source == "sr311":
                assert e.record_id.startswith("k311_") and e.source_url is None
        blob = r.model_dump_json()
        assert '"apartment"' not in blob and '"unique_key"' not in blob


def test_verifier_catches_tampering(reports):
    r = reports["west_farms_1134"].model_copy(deep=True)
    f = next(f for f in r.findings if f.title == "Open HPD violations")
    f.fact = FactCheck(**{**f.fact.model_dump(), "value": f.fact.value + 1})
    other = next(e.id for e in r.evidence if e.attribution == "other_building_on_lot")
    g = next(f for f in r.findings if f.title == "HPD violations on record")
    g.evidence_ids.append(other)
    r.findings.append(
        Finding(
            id="FX",
            section=SectionId.ORDERS,
            category=Category.HISTORICAL_EVENT,
            scope=Scope.THIS_BUILDING,
            importance=Importance.HIGH,
            title="bad",
            statement="The vacate order is currently in effect, so the unit is unsafe.",
            evidence_ids=["hpd_violation:does-not-exist"],
        )
    )
    problems = {i.finding_id: i.problem for i in verify_report(r).issues}
    assert f.id in problems and g.id in problems and "FX" in problems


def test_markdown_export_has_every_section(reports):
    r = reports["west_farms_1134"]
    md = render_markdown(r)
    for s in r.sections:
        assert f"## {s.title}" in md
    assert "Questions to ask" in md and "Sources checked" in md


def test_report_is_deterministic():
    a = build_baseline_report(collect_demo(DEMO_ADDRESSES["west_17_220"]))
    b = build_baseline_report(collect_demo(DEMO_ADDRESSES["west_17_220"]))
    strip = lambda r: json.dumps([(f.id, f.statement, f.evidence_ids) for f in r.findings])  # noqa: E731
    assert strip(a) == strip(b)


def test_demo_miss_is_not_reported_as_an_outage():
    """An address outside the recordings is a demo limitation, not a GeoSearch outage."""
    from nyc_apartment_agent.config import Settings
    from nyc_apartment_agent.investigation.agent import investigate

    r = investigate(
        "542 Senator Street",
        use_llm=False,
        settings=Settings(_env_file=None, data_mode="demo"),
    )
    assert r.resolution.outcome == "source_unavailable"
    assert r.resolution.in_demo_dataset is False
    assert "not in the offline demo dataset" in r.resolution.explanation
    assert "could not be reached" not in r.resolution.explanation


# ---- Building Attention Summary on the recorded buildings ---------------------------------


def test_attention_summary_on_recorded_buildings(reports):
    labels = {k: r.attention.label for k, r in reports.items()}
    assert labels == {
        "west_farms_1134": "priority_follow_up",
        "acp_1845": "priority_follow_up",
        "west_farms_1217": "insufficient_information",  # tax lot only
        "west_17_220": "some_items_to_check",
        "fifth_ave_350": "some_items_to_check",
    }
    a = reports["west_farms_1134"].attention
    hpd = next(p for p in a.points if p.kind == "housing_violations")
    assert "111 open violations" in hpd.text and "40 in Class C" in hpd.text
    for r in reports.values():
        assert verify_report(r).passed
        counted = {
            i for p in r.attention.points if p.kind != "other_buildings" for i in p.evidence_ids
        }
        # Ground truth: DOB complaint 2411142 is BIN 2117584's, never counted for 1134.
        assert not any("2411142" in i for i in counted)
        assert "Building Attention Summary" in render_markdown(r)
