"""DOB complaints (Task 5, step 2) on SYNTHETIC rows, lots and resolutions.

Covers the BIN search set and its evidence (design 6.3, 18.3), attribution, disposition
outcomes against the approved mapping (18.6), order-event wording, status disagreement,
dates, duplicates, pagination failures, schema changes, source outages, restricted BIN
joins and privacy. The five recorded addresses are in test_dob_complaints_cassette.py.
"""

import datetime as dt
import json
import logging
import re

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.dob_complaints import (
    MAX_BINS,
    _quote,
    classify_disposition,
    parse_dob_date,
    reference,
    retrieve_dob_complaints,
)
from nyc_apartment_agent.records.dob_models import (
    BinSource,
    DobComplaintOutcome,
    DobComplaintStatus,
    OrderAction,
    OrderEventKind,
)
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import NEWER_TRUTH, FakeSocrata, building_row, resolution

TODAY = dt.date(2026, 10, 23)
KEY = b"synthetic-test-key"
BBL = "2027540005"
R = "2006185"  # resolved BIN
OTHER = "2117584"
DOB = "eabe-havv"
FP = "5zhs-2jue"
HPD = "kj4p-ruqc"


def dob_row(number: int, bin_: str = R, **overrides) -> dict:
    """A synthetic DOB complaint row shaped like eabe-havv (field names from the dataset)."""
    base = {
        "complaint_number": str(number),
        "status": "CLOSED",
        "date_entered": "01/15/2026",
        "house_number": "1",
        "house_street": "TEST STREET",
        "zip_code": "10460",
        "bin": bin_,
        "community_board": "203",
        "complaint_category": "05",
        "unit": "BRONX",
        "disposition_date": "01/20/2026",
        "disposition_code": "I2",
        "inspection_date": "01/20/2026",
        "dobrundate": "20261022000000",
        ":id": f"row-synthetic-{number}-{bin_}",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}  # Socrata omits nulls


def fp_row(bin_: str, base: str = BBL, mappluto: str | None = None, rid: str = "") -> dict:
    return {
        "bin": bin_,
        "base_bbl": base,
        "mappluto_bbl": mappluto or base,
        ":id": rid or f"row-fp-{bin_}-{base}",
    }


class DobFake(FakeSocrata):
    """Honours the two Footprints filters and `bin in (...)`. Synthetic."""

    @staticmethod
    def _filtered(where: str, rows: list[dict]) -> list[dict]:
        m = re.fullmatch(r"base_bbl='(\d+)' OR mappluto_bbl='(\d+)'", where)
        if m:
            return [r for r in rows if m.group(1) in (r.get("base_bbl"), r.get("mappluto_bbl"))]
        m = re.fullmatch(r"bin in \(([0-9', ]+)\)", where)
        if m:
            wanted = set(re.findall(r"'(\d+)'", m.group(1)))
            return [r for r in rows if r.get("bin") in wanted]
        return rows


def hpd_result(bins=(R,), res=None, **row_overrides):
    rows = [building_row(i + 1, bin=b, **row_overrides) for i, b in enumerate(bins)]
    fake = FakeSocrata(rows)
    with fake.client() as c:
        return retrieve_hpd_buildings(res or resolution(), c, page_size=10, digest_key=KEY)


def retrieve(rows, *, footprints=None, hpd=None, res=None, fake=None, **kw):
    if fake is None:
        fake = DobFake(
            [], datasets={DOB: rows, FP: footprints if footprints is not None else [fp_row(R)]}
        )
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    res = res or resolution()
    if hpd is None and res.resolution_level is ResolutionLevel.BUILDING:
        hpd = hpd_result(res=res)
    with fake.client() as c:
        result = retrieve_dob_complaints(res, c, hpd_buildings=hpd, today=TODAY, **kw)
    return result, fake


def one(r, **kw):
    result, _ = retrieve([r], **kw)
    assert len(result.records) == 1, result.status_detail
    return result.records[0]


def where_of(fake, dataset=DOB):
    return [
        c.url.params["$where"]
        for c in fake.calls
        if dataset in c.url.path and "count(" in c.url.params.get("$select", "")
    ]


# ---- scope and restricted joins --------------------------------------------------------


def test_tax_lot_scope_is_unsupported_and_sends_no_request():
    res = resolution(bbl="2029930031", bin_=None, level=ResolutionLevel.TAX_LOT)
    fake = DobFake([], datasets={DOB: [dob_row(1)], FP: [fp_row(R)]})
    result, fake = retrieve([], res=res, fake=fake)
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN
    assert fake.calls == []
    assert result.join_scope is JoinScope.TAX_LOT and result.resolved_bin is None
    assert result.records == [] and result.search_bins == [] and result.provenance.pages == []


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unresolved_addresses_send_no_request(outcome):
    fake = DobFake([], datasets={DOB: [dob_row(1)], FP: [fp_row(R)]})
    result, fake = retrieve([], res=resolution(outcome=outcome), fake=fake, hpd=hpd_result())
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


def test_model_refuses_bin_search_or_records_below_building_scope():
    result, _ = retrieve([dob_row(1)])
    data = result.model_dump()
    data.update(join_scope=JoinScope.TAX_LOT, resolved_bin=None, summary=None)
    with pytest.raises(ValidationError):
        type(result).model_validate(data)


def test_quote_refuses_anything_but_a_bin():
    assert _quote(["2006185", "2117584"]) == "'2006185', '2117584'"
    for bad in ("2006185' OR '1'='1", "12345", "200618A"):
        with pytest.raises(ValueError):
            _quote([bad])


def test_query_is_by_bin_only_and_never_by_address():
    _, fake = retrieve([dob_row(1)])
    (where,) = where_of(fake)
    assert where == f"bin in ('{R}')"
    for c in fake.calls:
        assert "house" not in str(c.url) and "street" not in str(c.url)


def test_hpd_result_for_another_lot_is_refused():
    other_lot = hpd_result(res=resolution(bbl="2027540006"))
    with pytest.raises(ValueError):
        retrieve([dob_row(1)], hpd=other_lot)


# ---- BIN search set and attribution (6.3, 6.4, 18.3) ------------------------------------


def test_resolved_bin_is_this_building():
    rec = one(dob_row(1))
    assert rec.attribution is Attribution.THIS_BUILDING
    assert rec.attribution_basis is AttributionBasis.ROW_IDENTIFIERS
    assert rec.lot_membership is LotMembership.VIA_BIN_MAPPING
    assert rec.bin_sources == [BinSource.FOOTPRINTS, BinSource.HPD, BinSource.RESOLVER]
    assert rec.evidence_kind is EvidenceKind.REPORTED_ALLEGATION


def test_other_bin_known_to_both_sources_is_other_building():
    result, fake = retrieve(
        [dob_row(1), dob_row(2, OTHER)],
        footprints=[fp_row(R), fp_row(OTHER)],
        hpd=hpd_result(bins=(R, OTHER)),
    )
    by_bin = {r.source_bin: r for r in result.records}
    assert by_bin[OTHER].attribution is Attribution.OTHER_BUILDING_ON_LOT
    assert by_bin[OTHER].attribution_basis is AttributionBasis.BIN_LOT_MAPPING
    assert QualityFlag.BIN_SINGLE_SOURCE not in by_bin[OTHER].quality_flags
    assert where_of(fake) == [f"bin in ('{R}', '{OTHER}')"]


def test_footprints_only_bin_is_single_source_not_conflict():
    result, _ = retrieve([dob_row(1, OTHER)], footprints=[fp_row(R), fp_row(OTHER)])
    rec = result.records[0]
    assert rec.attribution is Attribution.OTHER_BUILDING_ON_LOT
    assert QualityFlag.BIN_SINGLE_SOURCE in rec.quality_flags
    assert rec.bin_sources == [BinSource.FOOTPRINTS]
    assert result.bin_lot_conflicts == []


def test_hpd_only_bin_with_footprints_silent_is_single_source_and_searched():
    result, fake = retrieve([dob_row(1, OTHER)], hpd=hpd_result(bins=(R, OTHER)))
    rec = result.records[0]
    assert rec.attribution is Attribution.OTHER_BUILDING_ON_LOT
    assert QualityFlag.BIN_SINGLE_SOURCE in rec.quality_flags
    assert where_of(fake, FP) == [
        f"base_bbl='{BBL}' OR mappluto_bbl='{BBL}'",
        f"bin in ('{OTHER}')",
    ]


def test_hpd_bin_that_footprints_places_on_another_lot_is_a_conflict_and_not_searched():
    result, fake = retrieve(
        [dob_row(1), dob_row(2, OTHER)],
        footprints=[fp_row(R), fp_row(OTHER, base="2027540099")],
        hpd=hpd_result(bins=(R, OTHER)),
    )
    assert result.bin_lot_conflicts == [OTHER]
    assert OTHER not in result.search_bins
    assert where_of(fake) == [f"bin in ('{R}')"]
    ev = {e.bin: e for e in result.bin_evidence}[OTHER]
    assert not ev.searched and QualityFlag.BIN_LOT_CONFLICT in ev.flags
    assert ev.footprints_lots_for_bin == ["2027540099"]
    assert [r.source_bin for r in result.records] == [R]


def test_resolved_bin_with_conflicting_lot_evidence_is_never_attributed_to_a_building():
    # HPD and the resolver put R on the lot; Footprints puts it on another lot.
    result, _ = retrieve(
        [dob_row(1)], footprints=[fp_row(R, base="2027540099")], hpd=hpd_result(bins=(R,))
    )
    rec = result.records[0]
    assert result.bin_lot_conflicts == [R]
    assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert rec.lot_membership is LotMembership.CONFLICTING
    assert QualityFlag.BIN_LOT_CONFLICT in rec.quality_flags
    assert result.summary.conflict_counts["bin_lot_conflict"] == 1
    with pytest.raises(ValidationError):
        rec.model_copy(update={}).model_validate(
            {
                **rec.model_dump(),
                "attribution": Attribution.THIS_BUILDING,
                "lot_membership": LotMembership.VIA_BIN_MAPPING,
            }
        )


def test_condo_base_lot_is_in_the_lot_family():
    # Footprints rows for the billing lot carry the condo base lot as base_bbl.
    res = resolution(bbl="1018227502", bin_="1054974")
    result, _ = retrieve(
        [dob_row(1, "1054974")],
        res=res,
        footprints=[fp_row("1054974", base="1018220001", mappluto="1018227502")],
        hpd=hpd_result(bins=("1054974",), res=res, boroid="1", block="1822", lot="7502"),
    )
    assert result.lot_family == ["1018220001", "1018227502"]
    assert result.records[0].attribution is Attribution.THIS_BUILDING


def test_placeholder_and_malformed_bins_are_never_searched():
    result, fake = retrieve(
        [dob_row(1)],
        footprints=[fp_row(R), fp_row("2000000"), fp_row("abc")],
        hpd=hpd_result(bins=(R, "2000000")),
    )
    assert result.search_bins == [R]
    assert where_of(fake) == [f"bin in ('{R}')"]


def test_non_current_hpd_records_add_no_bin():
    hpd = hpd_result(bins=(R, OTHER))
    hpd.records[1].is_current = False
    result, _ = retrieve([dob_row(1)], hpd=hpd)
    assert result.search_bins == [R]


def test_bin_cap_searches_the_resolved_bin_and_marks_the_result_incomplete():
    bins = [f"20{i:05d}" for i in range(30)]
    result, fake = retrieve([dob_row(1)], footprints=[fp_row(R)] + [fp_row(b) for b in bins])
    assert len(result.search_bins) == MAX_BINS and R in result.search_bins
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and result.partial
    assert any(r.startswith("bin_set_too_large") for r in result.provenance.incomplete_reasons)


def test_row_bin_outside_the_search_set_is_unattributed():
    # Defensive: the source should only return searched BINs.
    fake = DobFake([], datasets={DOB: [dob_row(1, "2999999")], FP: [fp_row(R)]})
    fake._filtered = staticmethod(lambda where, rows: rows)
    result, _ = retrieve([], fake=fake)
    rec = result.records[0]
    assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert QualityFlag.BIN_NOT_ON_CURRENT_LOT in rec.quality_flags


def test_footprints_unavailable_keeps_the_resolved_bin_and_fails_closed_for_others():
    fake = DobFake([], datasets={DOB: [dob_row(1), dob_row(2, OTHER)], FP: [fp_row(R)]})
    fake.fail_at = {0: 503, 1: 503}  # the Footprints count call, twice
    result, _ = retrieve([], fake=fake, hpd=hpd_result(bins=(R, OTHER)))
    assert result.search_bins == [R]
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert not result.bin_evidence_complete
    assert [r.attribution for r in result.records] == [Attribution.THIS_BUILDING]
    assert QualityFlag.BIN_SINGLE_SOURCE not in result.records[0].quality_flags


def test_hpd_buildings_unavailable_marks_bin_evidence_incomplete():
    fake = FakeSocrata([building_row(1)])
    fake.fail_at = {0: 503, 1: 503}
    with fake.client() as c:
        hpd = retrieve_hpd_buildings(resolution(), c, page_size=10, digest_key=KEY)
    result, _ = retrieve([dob_row(1)], hpd=hpd)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert any(
        "hpd buildings source_unavailable" in r for r in result.provenance.incomplete_reasons
    )


def test_hpd_buildings_are_fetched_when_not_supplied():
    fake = DobFake([], datasets={DOB: [dob_row(1)], FP: [fp_row(R)], HPD: [building_row(1)]})
    with fake.client() as c:
        result = retrieve_dob_complaints(resolution(), c, page_size=10, digest_key=KEY, today=TODAY)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert any(HPD in c.url.path for c in fake.calls)


# ---- disposition outcomes (approved mapping, 18.6) --------------------------------------

EXPECTED = {
    # code: (outcome, order event kind, order action)
    "AA": ("corrective_action_requested", None, None),
    "AB": ("corrective_action_corrected", None, None),
    "AC": ("corrective_action_not_corrected", None, None),
    "B1": ("violation_prepared_not_served", None, None),
    "B2": ("violation_prepared_not_served", None, None),
    "A1": ("violation_served", None, None),
    "A8": ("violation_served", None, None),
    "X1": ("no_violation", None, None),
    "X2": ("violation_issued", None, None),
    "I1": ("unsubstantiated", None, None),
    "I2": ("no_violation", None, None),
    "I3": ("inspection_completed", None, None),
    "V1": ("inspection_passed", None, None),
    "V2": ("inspection_failed", None, None),
    "U4": ("order_event", "stop_work_full_issued", "issued"),
    "U5": ("order_event", "stop_work_partial_issued", "issued"),
    "A3": ("order_event", "stop_work_full_issued", "served"),
    "L1": ("order_event", "stop_work_partial_recorded", "not_stated"),
    "L2": ("order_event", "stop_work_rescinded_full", "rescinded"),
    "L3": ("order_event", "stop_work_rescinded_partial", "rescinded"),
    "K4": ("order_event", "stop_work_crane_no_address_recorded", "not_stated"),
    "K6": ("order_event", "stop_work_partial_issued", "issued"),
    "H5": ("order_event", "stop_all_work_no_tcao", "not_stated"),
    "Y1": ("order_event", "vacate_full_issued", "served"),
    "Y2": ("order_event", "vacate_rescinded_full", "rescinded"),
    "Y3": ("order_event", "vacate_partial_issued", "served"),
    "Y4": ("order_event", "vacate_rescinded_partial", "rescinded"),
    "RZ": ("order_event", "vacate_previously_issued", "previously_issued"),
    "P3": ("order_event", "padlock_issued", "issued"),
    "P4": ("order_event", "padlock_rescinded", "rescinded"),
    "H3": ("violation_issued", "stop_work_noncompliance", "noncompliance_violation"),
    "H4": ("violation_served", "stop_work_noncompliance", "noncompliance_violation"),
    "V3": ("violation_served", "stop_work_noncompliance", "noncompliance_violation"),
    "W1": ("violation_served", "vacate_noncompliance", "noncompliance_violation"),
    "ME": ("violation_issued", "vacate_full_issued", "issued"),
    "MI": ("violation_issued", "vacate_partial_issued", "issued"),
}
STILL_UNKNOWN = "AD AE AF AG AH AI AJ D0 EI H6 H7 H8 L4 N1 N2 N3 Q6 Q7 Y5".split()


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_disposition_outcomes_follow_the_approved_mapping(code):
    outcome, kind, action = EXPECTED[code]
    rec = one(dob_row(1, disposition_code=code))
    assert rec.outcome is DobComplaintOutcome(outcome)
    assert rec.disposition_description == reference()["dispositions"][code]["definition"]
    if kind is None:
        assert rec.order_event is None
    else:
        ev = rec.order_event
        assert (ev.kind, ev.action) == (OrderEventKind(kind), OrderAction(action))
        assert ev.dob_text == rec.disposition_description
        assert ev.still_in_effect == "not_shown_by_source"


@pytest.mark.parametrize("code", [*STILL_UNKNOWN, "ZZ"])
def test_unknown_codes_stay_unknown_without_a_description(code):
    rec = one(dob_row(1, disposition_code=code))
    assert rec.outcome is DobComplaintOutcome.UNKNOWN_CODE
    assert rec.disposition_description is None and rec.disposition_definition_source is None
    assert rec.disposition_code == code
    assert QualityFlag.DISPOSITION_CODE_UNKNOWN in rec.quality_flags
    assert rec.order_event is None


def test_reference_agrees_with_the_approved_mapping_file():
    for code in STILL_UNKNOWN:
        assert reference()["dispositions"][code]["outcome"] == "unknown_code"
    assert classify_disposition(None)["outcome"] is DobComplaintOutcome.NONE


def test_definition_source_is_recorded():
    assert one(dob_row(1, disposition_code="U4")).disposition_definition_source == "dob_pdf_2021"
    assert one(dob_row(1, disposition_code="RZ")).disposition_definition_source == (
        "ref_6v9u_ndjg_2017"
    )


# ---- order events never claim an order is in effect -------------------------------------

IN_EFFECT = re.compile(
    r"(?<!still )\bin effect\b|currently (?:active|in force)|remains? in force|order is active",
    re.I,
)


def test_order_statements_never_claim_an_order_is_in_effect():
    codes = [c for c, v in EXPECTED.items() if v[1]]
    rows = [dob_row(i, disposition_code=c) for i, c in enumerate(codes, start=1)]
    result, _ = retrieve(rows)
    events = result.summary.order_events
    assert len(events) == len(codes)
    for ev in events:
        assert "does not show whether the order is still in effect" in ev.statement
        assert not IN_EFFECT.search(ev.statement.replace("still in effect", ""))
    assert not IN_EFFECT.search(result.model_dump_json().replace("still in effect", ""))


def test_verbless_codes_do_not_assert_issuance():
    for code in ("L1", "K4", "H5"):
        ev = one(dob_row(1, disposition_code=code)).order_event
        assert ev.action is OrderAction.NOT_STATED
        assert "issued" not in ev.kind.value and "served" not in ev.kind.value
        said = ev.statement.split(" for complaint")[0]
        assert ev.dob_text in said


def test_order_events_are_listed_in_date_order_and_never_netted():
    rows = [
        dob_row(1, disposition_code="L2", disposition_date="03/01/2026"),
        dob_row(2, disposition_code="A3", disposition_date="02/01/2026"),
        dob_row(3, disposition_code="L1", disposition_date=None),
    ]
    result, _ = retrieve(rows)
    assert [e.disposition_code for e in result.summary.order_events] == ["A3", "L2", "L1"]


# ---- status vs disposition (4.3) --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "flagged"),
    [
        ("ACTIVE", "I2", True),
        ("ACTIVE", "X1", True),
        ("ACTIVE", "X2", True),
        ("ACTIVE", "A1", True),
        ("ACTIVE", None, False),
        ("ACTIVE", "D1", False),
        ("ACTIVE", "L1", False),
        ("CLOSED", None, True),
        ("CLOSED", "I2", False),
    ],
)
def test_status_and_disposition_are_kept_apart(status, code, flagged):
    rec = one(dob_row(1, status=status, disposition_code=code))
    assert rec.complaint_status is DobComplaintStatus(status.lower())
    assert rec.status_raw == status
    assert (QualityFlag.STATUS_FIELDS_DISAGREE in rec.quality_flags) is flagged


def test_active_complaints_with_final_codes_are_counted_not_reconciled():
    rows = [
        dob_row(1, status="ACTIVE", disposition_code="X1"),
        dob_row(2, status="ACTIVE", disposition_code="X2"),
        dob_row(3, status="CLOSED", disposition_code=None),
    ]
    result, _ = retrieve(rows)
    s = result.summary
    assert (s.active_with_final_disposition, s.closed_with_no_disposition) == (2, 1)
    assert s.status_fields_disagree == 3
    assert s.by_attribution[Attribution.THIS_BUILDING].active == 2


def test_unknown_status_is_flagged():
    rec = one(dob_row(1, status="PENDING"))
    assert rec.complaint_status is DobComplaintStatus.UNKNOWN
    assert QualityFlag.STATUS_UNKNOWN in rec.quality_flags


# ---- dates ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "flag"),
    [
        ("01/15/2026", None),
        ("2026-01-15", QualityFlag.UNPARSEABLE_DATE),
        ("13/45/2020", QualityFlag.UNPARSEABLE_DATE),
        ("02/30/2020", QualityFlag.UNPARSEABLE_DATE),
        ("01/01/1850", QualityFlag.IMPLAUSIBLE_DATE),
        ("01/01/2030", QualityFlag.IMPLAUSIBLE_DATE),
        ("", None),
        (None, None),
    ],
)
def test_dob_dates(raw, flag):
    value, got = parse_dob_date(raw, as_of=TODAY)
    assert got is flag
    if flag:
        assert value.raw == raw and value.value is None


def test_malformed_dates_are_flagged_and_raw_text_kept():
    rec = one(dob_row(1, date_entered="2026-01-15", disposition_date="01/01/1850"))
    assert rec.received_date.raw == "2026-01-15" and rec.received_date.value is None
    assert QualityFlag.UNPARSEABLE_DATE in rec.quality_flags
    assert QualityFlag.IMPLAUSIBLE_DATE in rec.quality_flags


def test_disposition_before_received_is_flagged_not_corrected():
    rec = one(dob_row(1, date_entered="02/01/2026", disposition_date="01/01/2026"))
    assert QualityFlag.DATE_ORDER in rec.quality_flags
    assert rec.disposition_date.value == dt.date(2026, 1, 1)


# ---- duplicates (section 9) ---------------------------------------------------------------


def test_identical_duplicate_numbers_collapse_but_keep_every_source_row():
    a = dob_row(1)
    b = {**a, ":id": "row-synthetic-copy"}
    result, _ = retrieve([a, b])
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.source_row_ids == [a[":id"], "row-synthetic-copy"]
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in rec.quality_flags
    assert result.summary.source_rows == 2 and result.status is RetrievalStatus.RECORDS_FOUND


def test_differing_duplicate_numbers_are_all_kept_and_lose_building_attribution():
    rows = [dob_row(1), dob_row(1, OTHER, disposition_code="A1")]
    result, _ = retrieve(
        rows, footprints=[fp_row(R), fp_row(OTHER)], hpd=hpd_result(bins=(R, OTHER))
    )
    assert len(result.records) == 2
    for rec in result.records:
        assert QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in rec.quality_flags
        assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert result.summary.conflict_counts["duplicate_id_different_content"] == 2


# ---- retrieval status, pagination, schema --------------------------------------------------


def test_records_found_and_no_records_found():
    result, _ = retrieve([dob_row(1), dob_row(2)])
    assert result.status is RetrievalStatus.RECORDS_FOUND and not result.partial
    result, _ = retrieve([])
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    assert result.provenance.source_count == 0


def _fake(rows):
    return DobFake([], datasets={DOB: rows, FP: [fp_row(R)]})


@pytest.mark.parametrize("code", [500, 503, 429])
def test_source_down_before_count_is_unavailable_not_empty(code):
    fake = _fake([dob_row(1)])
    fake.fail_at = {2: code, 3: code}  # calls 0-1 are Footprints; 2 is the DOB count
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.records == [] and result.summary is None
    assert result.search_bins == [R]


def test_failed_page_keeps_partial_rows_marked():
    fake = _fake([dob_row(i) for i in range(1, 16)])
    fake.fail_at = {4: 500, 5: 500}  # second DOB page
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.partial and len(result.records) == 10


def test_short_page_is_incomplete():
    fake = _fake([dob_row(i) for i in range(1, 16)])
    fake.short_at = {3}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION


def test_count_changing_during_retrieval_is_incomplete():
    fake = _fake([dob_row(i) for i in range(1, 16)])
    fake.truth_at = {4: NEWER_TRUTH, 6: "Thu, 22 Oct 2026 01:00:00 GMT"}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert any("changed" in r for r in result.provenance.incomplete_reasons)


def test_row_cap_is_incomplete():
    result, _ = retrieve([dob_row(i) for i in range(1, 13)], row_cap=5)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert len(result.records) == 5


def test_missing_required_field_is_schema_changed():
    rows = [dob_row(1), dob_row(2)]
    for r in rows:
        del r["complaint_category"]
    result, _ = retrieve(rows)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.status_detail.startswith("schema_changed")
    assert result.records == []


# ---- categories ------------------------------------------------------------------------------


def test_known_category_uses_dobs_own_text():
    rec = one(dob_row(1, complaint_category="23"))
    assert rec.complaint_category_description == (
        "Sidewalk Shed/Supported Scaffold/Inadequate/Defective/None/No Permit/No Cert"
    )
    assert rec.complaint_category_priority == "B"
    assert QualityFlag.COMPLAINT_CATEGORY_UNKNOWN not in rec.quality_flags


def test_category_missing_from_dobs_list_is_flagged_without_a_description():
    rec = one(dob_row(1, complaint_category="7J"))
    assert rec.complaint_category == "7J" and rec.complaint_category_description is None
    assert QualityFlag.COMPLAINT_CATEGORY_UNKNOWN in rec.quality_flags


# ---- privacy and evidence -------------------------------------------------------------------


def test_recorded_address_is_redacted_and_extract_time_is_not_public():
    store = InMemoryEvidenceStore()
    rec = one(dob_row(1, house_street="TEST STREET APT 4B"), evidence=store)
    assert "4B" not in (rec.recorded_address or "")
    assert QualityFlag.UNIT_DETAIL_REDACTED in rec.quality_flags
    dumped = rec.model_dump_json()
    assert "dobrundate" not in dumped and "20261022000000" not in dumped
    raw = store.get(rec.original_digest)
    assert raw.original_row["house_street"] == "TEST STREET APT 4B"
    assert verify(raw, KEY)


def test_digest_is_keyed_and_not_a_plain_hash():
    from nyc_apartment_agent.records.redaction import canonical_sha256

    r = dob_row(1)
    rec = one(r)
    assert rec.original_digest != canonical_sha256(r)
    assert rec.redacted_sha256 == canonical_sha256(
        {k: v for k, v in r.items() if k != "dobrundate"}
    )


def test_logs_never_contain_row_contents(caplog):
    fake = _fake([dob_row(1, house_street="SENTINEL STREET")])
    fake.fail_at = {}
    with caplog.at_level(logging.DEBUG):
        retrieve([], fake=fake)
    assert "SENTINEL" not in caplog.text


def test_provenance_records_every_lookup():
    result, _ = retrieve([dob_row(1)])
    datasets = [p.dataset_id for p in result.bin_evidence_provenance]
    assert datasets == [FP, HPD]
    assert result.provenance.dataset_id == DOB and result.provenance.data_as_of
    assert result.coverage_notes and "retired BIN" in result.coverage_notes[0]
    assert json.loads(result.model_dump_json())["bin_evidence_complete"] is True
