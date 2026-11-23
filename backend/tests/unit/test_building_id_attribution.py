"""D9: HPD BuildingID as a secondary attribution key, on SYNTHETIC rows only."""

import datetime as dt

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.building_id_attribution import (
    _mapping_problem,
    apply_to_complaints,
    apply_to_violations,
)
from nyc_apartment_agent.records.complaint_models import ComplaintAttribution
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    RetrievalStatus,
    SourceResult,
)
from nyc_apartment_agent.resolver.models import ResolutionLevel
from tests.hpd_helpers import FakeSocrata, building_row, complaint_row, resolution, row

TODAY = dt.date(2026, 10, 22)
KW = {"digest_key": b"k", "page_size": 50, "today": TODAY}
U, THIS, OTHER = (
    Attribution.UNATTRIBUTED_ON_LOT,
    Attribution.THIS_BUILDING,
    Attribution.OTHER_BUILDING_ON_LOT,
)
# Lot 2027540005: building 1 is the requested BIN 2006185, building 2 is BIN 2006186.
BUILDINGS = [building_row(1), building_row(2, bin="2006186")]


def fetch(fn, rows, res=None, fake=None):
    with (fake or FakeSocrata(rows)).client() as c:
        return fn(res or resolution(), c, **KW)


def run(vrows, brows=BUILDINGS, res=None, bfake=None):
    v = fetch(retrieve_hpd_violations, vrows, res)
    b = fetch(retrieve_hpd_buildings, brows, res, bfake)
    return v, apply_to_violations(v, b, as_of=TODAY)


def by_id(result):
    return {r.violation_id: r for r in result.records}


# ---- the upgrade itself --------------------------------------------------------------


@pytest.mark.parametrize(("bid", "expected"), [("1", THIS), ("2", OTHER)])
@pytest.mark.parametrize("bin_", [None, "2000000"])
def test_missing_or_placeholder_bin_is_resolved_through_building_id(bid, expected, bin_):
    before, after = run([row(1, bin=bin_, buildingid=bid)])
    assert before.records[0].attribution is U
    r = after.records[0]
    assert r.attribution is expected
    assert r.attribution_basis is AttributionBasis.HPD_BUILDING_ID
    assert r.original_attribution is U
    assert r.mapped_bin == {"1": "2006185", "2": "2006186"}[bid]
    assert r.source_bin == before.records[0].source_bin  # the original value is untouched


def test_before_after_counts_and_summary_are_recomputed():
    rows = [
        row(1),  # own BIN: this building
        row(2, bin=None, buildingid="2"),
        row(3, bin=None, buildingid="2"),
        row(4, bin=None),  # no building id
    ]
    before, after = run(rows)
    rep = after.building_id_attribution
    assert rep.applied and rep.before == {"this_building": 1, "unattributed_on_lot": 3}
    assert rep.after == {
        "other_building_on_lot": 2,
        "this_building": 1,
        "unattributed_on_lot": 1,
    }
    assert rep.upgraded == {"other_building_on_lot": 2}
    assert rep.upgraded_by_building_id == {
        "2": {"mapped_bin": "2006186", "attribution": "other_building_on_lot", "rows": 2}
    }
    assert rep.kept_unattributed == {"row_has_no_building_id": 1}
    assert after.summary.by_attribution[OTHER].records == 2
    assert U not in before.summary.by_attribution or before.summary.by_attribution[U].records == 3
    # The input result is not modified.
    assert [r.attribution for r in before.records].count(U) == 3


def test_rows_attributed_by_their_own_bin_are_never_changed():
    # BIN says this building, BuildingID says building 2: report it, do not change it.
    before, after = run([row(1, buildingid="2")])
    r = after.records[0]
    assert r.attribution is THIS and r.attribution_basis is AttributionBasis.ROW_IDENTIFIERS
    assert r.original_attribution is None
    assert after.building_id_attribution.bin_building_id_disagreements == 1


# ---- conditions that block the upgrade -----------------------------------------------


@pytest.mark.parametrize(
    ("vrow", "brows", "reason"),
    [
        (row(1, bin=None, buildingid="9"), BUILDINGS, "no_hpd_building_record_on_lot"),
        (row(1, bin=None, buildingid="0"), BUILDINGS, "row_has_no_building_id"),
        (row(1, bin="12", buildingid="2"), BUILDINGS, "row_bin_malformed"),
        (row(1, bin=None, buildingid="2", lot="6"), BUILDINGS, "row_lot_not_confirmed"),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin="2006186", lifecycle="Demolished", recordstatus="Inactive")],
            "hpd_building_record_not_current",
        ),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin="2006186", recordstatus="Inactive")],
            "hpd_building_record_not_current",
        ),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin=None)],
            "mapped_bin_missing_placeholder_or_malformed",
        ),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin="2000000")],
            "mapped_bin_missing_placeholder_or_malformed",
        ),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin="2006186", lot="6")],
            "hpd_building_record_not_on_lot",
        ),
        (
            row(1, bin=None, buildingid="2"),
            [building_row(2, bin="2006186"), building_row(3, bin="2006186")],
            "mapped_bin_on_several_current_records",
        ),
    ],
)
def test_blocked_rows_stay_unattributed(vrow, brows, reason):
    _, after = run([vrow], brows)
    r = after.records[0]
    assert r.attribution is U and r.attribution_basis is AttributionBasis.ROW_IDENTIFIERS
    assert after.building_id_attribution.kept_unattributed == {reason: 1}


def test_conflicting_duplicate_rows_are_not_upgraded():
    rows = [
        row(1, bin=None, buildingid="2", **{"class": "B"}),
        row(1, bin=None, buildingid="2", **{"class": "C"}),
    ]
    _, after = run(rows)
    assert {r.attribution for r in after.records} == {U}
    assert after.building_id_attribution.kept_unattributed == {"row_duplicate_id_conflict": 2}


def test_duplicate_building_id_in_building_records_is_ambiguous():
    brows = [building_row(2, bin="2006186"), building_row(2, bin="2006187")]
    _, after = run([row(1, bin=None, buildingid="2")], brows)
    assert after.records[0].attribution is U
    # The pager already refuses a repeated unique key, so the whole step is skipped...
    assert after.building_id_attribution.not_applied_reason == (
        "hpd_building_records_incomplete_pagination"
    )
    # ...and the mapping rule refuses it on its own as well.
    b = fetch(retrieve_hpd_buildings, BUILDINGS)
    assert _mapping_problem([b.records[1], b.records[1]]) == "several_hpd_building_records"


# ---- whole-result preconditions ------------------------------------------------------


def test_tax_lot_results_are_never_upgraded():
    res = resolution(bin_="2000000", level=ResolutionLevel.TAX_LOT)
    _, after = run([row(1, bin=None, buildingid="1")], res=res)
    assert after.records[0].attribution is Attribution.LOT_LEVEL
    rep = after.building_id_attribution
    assert not rep.applied and rep.not_applied_reason == "not_a_building_level_join"
    assert rep.before == rep.after == {"lot_level": 1}


def test_incomplete_or_unavailable_building_records_block_the_step():
    fake = FakeSocrata(BUILDINGS)
    fake.fail_at = {0: 503, 1: 503}
    _, after = run([row(1, bin=None, buildingid="2")], bfake=fake)
    rep = after.building_id_attribution
    assert not rep.applied and rep.not_applied_reason == "hpd_building_records_source_unavailable"
    assert after.records[0].attribution is U
    fake = FakeSocrata(BUILDINGS)
    fake.short_at = {1}
    _, after = run([row(1, bin=None, buildingid="2")], bfake=fake)
    assert after.building_id_attribution.not_applied_reason == (
        "hpd_building_records_incomplete_pagination"
    )


def test_building_records_for_another_resolution_are_refused():
    v = fetch(retrieve_hpd_violations, [row(1, bin=None, buildingid="2")])
    b = fetch(retrieve_hpd_buildings, BUILDINGS, resolution(bin_="2006186"))
    rep = apply_to_violations(v, b, as_of=TODAY).building_id_attribution
    assert rep.not_applied_reason == "hpd_building_records_for_a_different_resolution"
    assert apply_to_violations(v, None, as_of=TODAY).building_id_attribution.not_applied_reason


# ---- invariants on the models --------------------------------------------------------


def test_model_rejects_a_building_id_basis_on_a_row_that_was_not_unattributed():
    _, after = run([row(1, bin=None, buildingid="2")])
    data = after.model_dump(by_alias=True)
    data["records"][0]["original_attribution"] = "this_building"
    with pytest.raises(ValidationError, match="unattributed"):
        SourceResult.model_validate(data)
    data["records"][0]["original_attribution"] = None
    data["records"][0]["attribution_basis"] = "row_identifiers"
    with pytest.raises(ValidationError, match="basis change"):
        SourceResult.model_validate(data)


# ---- complaints ----------------------------------------------------------------------


def test_complaints_regroup_after_the_upgrade():
    rows = [
        complaint_row(1, cid=100, bin=None, building_id="2"),
        complaint_row(2, cid=100, bin="2006186", building_id="2"),
        complaint_row(3, cid=200, bin=None, building_id="9"),
    ]
    c = fetch(retrieve_hpd_complaints, rows)
    b = fetch(retrieve_hpd_buildings, BUILDINGS)
    assert {g.complaint_id: g.attribution for g in c.complaints}["100"] is (
        ComplaintAttribution.MIXED
    )
    after = apply_to_complaints(c, b, as_of=TODAY)
    groups = {g.complaint_id: g.attribution for g in after.complaints}
    assert groups == {
        "100": ComplaintAttribution.OTHER_BUILDING_ON_LOT,
        "200": ComplaintAttribution.UNATTRIBUTED_ON_LOT,
    }
    rep = after.building_id_attribution
    assert rep.complaints_before == {"mixed": 1, "unattributed_on_lot": 1}
    assert rep.complaints_after == {"other_building_on_lot": 1, "unattributed_on_lot": 1}
    assert rep.kept_unattributed == {"no_hpd_building_record_on_lot": 1}
    assert after.summary.total_complaints == 2
    assert after.status is RetrievalStatus.RECORDS_FOUND
