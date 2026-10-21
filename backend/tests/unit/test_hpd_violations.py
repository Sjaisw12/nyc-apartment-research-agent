"""Normalization, attribution, flags and statuses on SYNTHETIC rows and resolutions."""

import datetime as dt
import logging

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_violations import (
    lot_where,
    retrieve_hpd_violations,
)
from nyc_apartment_agent.records.join_policy import plan_lot_query
from nyc_apartment_agent.records.models import (
    Attribution,
    LocationScope,
    OrderCategory,
    QualityFlag,
    RetrievalStatus,
    SourceResult,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import FakeSocrata, resolution, row

TODAY = dt.date(2026, 10, 21)


def retrieve(rows, res=None, **kw):
    fake = FakeSocrata(rows)
    with fake.client() as c:
        result = retrieve_hpd_violations(res or resolution(), c, today=TODAY, page_size=10, **kw)
    return result, fake


def one(r, **kw):
    result, _ = retrieve([r], **kw)
    assert len(result.records) == 1
    return result.records[0]


# ---- statuses --------------------------------------------------------------------


def test_records_found():
    result, _ = retrieve([row(1), row(2)])
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert not result.partial


def test_no_records_found_needs_a_zero_count():
    result, fake = retrieve([])
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    assert result.provenance.source_count == 0 and len(fake.calls) == 1


def test_source_unavailable_is_not_no_records():
    fake = FakeSocrata([row(1)])
    fake.fail_at = {0: 503, 1: 503}
    with fake.client() as c:
        result = retrieve_hpd_violations(resolution(), c, today=TODAY)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.records == [] and result.summary is None


def test_incomplete_pagination_keeps_partial_rows_marked():
    fake = FakeSocrata([row(i) for i in range(1, 16)])
    fake.fail_at = {2: 500, 3: 500}
    with fake.client() as c:
        result = retrieve_hpd_violations(resolution(), c, today=TODAY, page_size=10)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.partial and len(result.records) == 10


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unsupported_join_makes_zero_requests(outcome):
    result, fake = retrieve([row(1)], res=resolution(outcome=outcome))
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN
    assert fake.calls == [] and result.records == []


def test_row_cap_gives_incomplete_pagination():
    result, _ = retrieve([row(i) for i in range(1, 13)], row_cap=5)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert len(result.records) == 5


# ---- query ---------------------------------------------------------------------------


def test_query_covers_bbl_and_boro_block_lot():
    plan = plan_lot_query(resolution())
    assert lot_where(plan) == "bbl='2027540005' OR (boroid=2 AND block=2754 AND lot=5)"
    _, fake = retrieve([row(1)])
    assert all(r.url.params["$where"] == lot_where(plan) for r in fake.calls)


def test_bin_is_never_used_in_the_query():
    _, fake = retrieve([row(1)])
    assert all("bin" not in r.url.params["$where"] for r in fake.calls)


# ---- attribution -----------------------------------------------------------------


def test_building_level_buckets():
    rows = [
        row(1),  # same BIN
        row(2, bin="2006186"),  # another building on the lot
        row(3, bin=None, bbl=None),  # no BIN, no BBL (seen live: 10,665 such rows)
        row(4, bin="2000000"),  # placeholder BIN
    ]
    result, _ = retrieve(rows)
    got = {r.violation_id: r.attribution for r in result.records}
    assert got == {
        "1": Attribution.THIS_BUILDING,
        "2": Attribution.OTHER_BUILDING_ON_LOT,
        "3": Attribution.UNATTRIBUTED_ON_LOT,
        "4": Attribution.UNATTRIBUTED_ON_LOT,
    }
    assert result.summary.building_specific_claims_allowed


def test_tax_lot_result_never_produces_building_claims():
    res = resolution(bin_="2006185", level=ResolutionLevel.TAX_LOT)
    rows = [row(1), row(2, bin="2006186"), row(3, bin=None)]
    result, _ = retrieve(rows, res=res)
    assert {r.attribution for r in result.records} == {Attribution.LOT_LEVEL}
    assert result.resolved_bin is None
    assert not result.summary.building_specific_claims_allowed
    assert set(result.summary.by_attribution) == {Attribution.LOT_LEVEL}


def test_model_rejects_building_attribution_at_tax_lot_scope():
    res = resolution(level=ResolutionLevel.TAX_LOT)
    result, _ = retrieve([row(1)], res=res)
    data = result.model_dump()
    data["records"][0]["attribution"] = Attribution.THIS_BUILDING
    with pytest.raises(ValidationError, match="building-level attribution"):
        SourceResult.model_validate(data)


def test_missing_and_zero_bbl_rows_are_kept_via_boro_block_lot():
    a = one(row(1, bbl=None))
    assert a.attribution is Attribution.THIS_BUILDING
    assert QualityFlag.BBL_MISSING in a.quality_flags
    b = one(row(2, bbl="0"))
    assert QualityFlag.BBL_INVALID in b.quality_flags
    assert b.attribution is Attribution.THIS_BUILDING


@pytest.mark.parametrize(
    "overrides",
    [
        {"lot": "6"},  # bbl says our lot, boro-block-lot says another
        {"bbl": "2027540006"},  # boro-block-lot says our lot, bbl says another
    ],
)
def test_bbl_vs_boro_block_lot_conflict_blocks_attribution(overrides):
    for res in (resolution(), resolution(level=ResolutionLevel.TAX_LOT)):
        r = one(row(1, **overrides), res=res)
        assert QualityFlag.LOT_IDENTIFIER_CONFLICT in r.quality_flags
        assert r.attribution is Attribution.UNATTRIBUTED_ON_LOT
        assert r.lot_membership.value == "conflicting"


# ---- status, dates, classes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("vs", "cs", "is_open", "flag"),
    [
        ("Open", "NOV SENT OUT", True, None),
        ("Close", "VIOLATION CLOSED", False, None),
        ("Open", "VIOLATION CLOSED", True, QualityFlag.STATUS_FIELDS_DISAGREE),
        ("Open", "VIOLATION DISMISSED", True, QualityFlag.STATUS_FIELDS_DISAGREE),
        ("Close", "NOV SENT OUT", False, QualityFlag.STATUS_FIELDS_DISAGREE),
        ("Close", "VIOLATION WILL BE REINSPECTED", False, None),  # ambiguous: not judged
        ("Pending", "NOV SENT OUT", None, QualityFlag.STATUS_UNKNOWN),
    ],
)
def test_status_fields(vs, cs, is_open, flag):
    r = one(row(1, violationstatus=vs, currentstatus=cs))
    assert r.is_open is is_open
    assert r.violation_status_raw == vs and r.current_status_raw == cs
    if flag:
        assert flag in r.quality_flags
    else:
        assert QualityFlag.STATUS_FIELDS_DISAGREE not in r.quality_flags


def test_impossible_dates_are_flagged_and_raw_kept():
    r = one(
        row(
            1,
            inspectiondate="0217-07-05T00:00:00.000",
            currentstatusdate="9999-12-11T00:00:00.000",
            newcorrectbydate="2027-03-01T00:00:00.000",  # future deadline is fine
        )
    )
    assert r.dates["inspectiondate"].value is None
    assert r.dates["inspectiondate"].raw == "0217-07-05T00:00:00.000"
    assert r.dates["currentstatusdate"].value is None
    assert r.dates["newcorrectbydate"].value == dt.date(2027, 3, 1)
    assert QualityFlag.IMPLAUSIBLE_DATE in r.quality_flags


def test_future_inspection_date_is_implausible():
    r = one(row(1, inspectiondate="2026-12-01T00:00:00.000"))
    assert r.dates["inspectiondate"].value is None


def test_unparseable_date():
    r = one(row(1, inspectiondate="12/01/2026"))
    assert QualityFlag.UNPARSEABLE_DATE in r.quality_flags


@pytest.mark.parametrize(
    ("order", "text", "category", "consequential"),
    [
        ("765", "§ 27-2142 ADM CODE APTS HAVE BEEN VACATED", OrderCategory.VACATE_ORDER, True),
        (
            "729",
            "§27-2153 ... ALTERNATIVE ENFORCEMENT PROGRAM",
            OrderCategory.ALTERNATIVE_ENFORCEMENT_PROGRAM,
            True,
        ),
        (
            "730",
            "§ 27-2091(C) UNDERLYING CONDITIONS",
            OrderCategory.UNDERLYING_CONDITIONS_ORDER,
            True,
        ),
        ("759", "COMMISSIONER'S ORDER ... RELOCATION", OrderCategory.RELOCATION_ORDER, True),
        (
            "780",
            "§27-2107 FAILED TO FILE A VALID REGISTRATION",
            OrderCategory.REGISTRATION_NONCOMPLIANCE,
            False,
        ),
        ("9999", "THE PREMISES HAVE BEEN VACATED BY AN ORDER", OrderCategory.VACATE_ORDER, True),
        ("583", "REPAIR THE SOURCE OF A WATER LEAK", OrderCategory.OTHER, False),
    ],
)
def test_class_i_orders_are_categorized_not_dismissed(order, text, category, consequential):
    r = one(row(1, **{"class": "I", "ordernumber": order, "novdescription": text}))
    assert r.is_informational
    assert r.order_category is category
    assert r.potentially_consequential is consequential
    assert r.description  # description kept


def test_class_i_is_counted_separately_from_hazard_classes():
    rows = [row(1), row(2, **{"class": "I", "ordernumber": "765", "novdescription": "VACATED"})]
    result, _ = retrieve(rows)
    b = result.summary.by_attribution[Attribution.THIS_BUILDING]
    assert b.hazard_class_counts == {"B": 1}
    assert b.information_order_counts == {"vacate_order": 1}
    assert b.consequential_orders[0]["violation_id"] == "2"


def test_class_on_non_i_rows_has_no_order_category():
    assert one(row(1, ordernumber="765")).order_category is None


# ---- location scope and privacy ------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "scope"),
    [
        ({"apartment": "1H", "novdescription": "MOLD IN BATHROOM"}, LocationScope.UNIT),
        ({"novdescription": "LOCATED AT APT 3B, 2nd STORY"}, LocationScope.UNIT),
        ({"novdescription": "ROACHES THROUGHOUT THE BUILDING"}, LocationScope.BUILDING_WIDE),
        ({"novdescription": "BROKEN STEP AT CELLAR STAIR"}, LocationScope.COMMON_AREA),
        ({"novdescription": "POST A SIGN WITH THE REGISTRATION NUMBER"}, LocationScope.UNSPECIFIED),
        ({"novdescription": None}, LocationScope.UNSPECIFIED),
    ],
)
def test_location_scope_needs_evidence(overrides, scope):
    assert one(row(1, **overrides)).location_scope is scope


def test_public_record_has_no_unit_detail_but_evidence_does():
    store = InMemoryEvidenceStore()
    raw = row(1, apartment="4C", story="4", novdescription="LEAK AT APT 4C, 4th STORY")
    r = one(raw, evidence=store)
    dumped = r.model_dump_json()
    assert "4C" not in dumped and "4th STORY" not in dumped
    assert "apartment" not in r.model_dump() and "story" not in r.model_dump()
    assert QualityFlag.UNIT_DETAIL_REDACTED in r.quality_flags
    item = store.get(r.original_sha256)
    assert item.original_row["apartment"] == "4C"
    assert item.redacted_sha256 == r.redacted_sha256 != r.original_sha256
    assert verify(item)
    assert set(item.redacted_fields) == {"apartment", "story", "novdescription"}


def test_logs_never_contain_row_contents(caplog):
    caplog.set_level(logging.DEBUG)
    retrieve([row(1, apartment="9Z", novdescription="SECRET TEXT AT APT 9Z")])
    assert "9Z" not in caplog.text and "SECRET TEXT" not in caplog.text


# ---- duplicates ------------------------------------------------------------------


def test_identical_duplicates_collapse_but_differing_ones_are_flagged():
    # Built by hand: the pager already refuses duplicate ids, so we exercise the normalizer.
    from nyc_apartment_agent.records.hpd_violations import normalize_fetch
    from nyc_apartment_agent.records.models import PageRecord, RetrievalProvenance
    from nyc_apartment_agent.sources.socrata_pager import PagedFetch

    page = PageRecord(
        index=0,
        url="u",
        rows=4,
        fetched_at="2026-10-21T00:00:00+00:00",
        attempts=1,
        truth_last_modified="t",
        last_modified=None,
        request_id=None,
    )
    fetch = PagedFetch(
        rows=[row(1), row(1), row(2), row(2, violationstatus="Close")],
        provenance=RetrievalProvenance(dataset_id="wvxf-dwi5", pages=[page]),
        row_page=[0, 0, 0, 0],
    )
    out = normalize_fetch(fetch, plan_lot_query(resolution()), as_of=TODAY)
    assert [r.violation_id for r in out] == ["1", "2", "2"]
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in out[0].quality_flags
    assert all(QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags for r in out[1:])
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in out[1:])
