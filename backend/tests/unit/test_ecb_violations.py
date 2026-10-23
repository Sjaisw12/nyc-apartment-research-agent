"""DOB ECB violations (Task 5, step 3) on SYNTHETIC rows, lots and resolutions.

Covers the two-format lot query, the attribution decision table (design 18.3 and 18.8),
condo lot families, retired and unknown BINs, duplicates, the three separate statuses
and the approved adjudication mapping, Decimal money, dates, privacy (sentinels in every
public text field), outages, incomplete fetches, schema changes and model invariants.
The five recorded addresses are in test_ecb_violations_cassette.py.
"""

import datetime as dt
import json
import logging
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.ecb_models import (
    WITHHELD_VALUE,
    Adjudication,
    CorrectionStatus,
    LotFormat,
    MatchedBy,
    Severity,
)
from nyc_apartment_agent.records.ecb_violations import (
    build_where,
    classify_hearing,
    lot_clause,
    parse_ecb_date,
    parse_lot,
    parse_money,
    reference,
    retrieve_ecb_violations,
)
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    DigestBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import NEWER_TRUTH, resolution
from tests.unit.test_dob_complaints import FP, HPD, DobFake, fp_row, hpd_result

TODAY = dt.date(2026, 10, 23)
KEY = b"synthetic-test-key"
BBL = "2027540005"
R = "2006185"  # resolved BIN
OTHER = "2117584"
ECB = "6bgk-3dad"
THIS, OTHER_B = Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT
UNATTR, LOT = Attribution.UNATTRIBUTED_ON_LOT, Attribution.LOT_LEVEL
TEXT_220 = (
    "§28-105.11                                                                  "
    "FAILURE TO POST PERMIT FOR WORK AT PREMISES"
)


def ecb_row(isn: int, bin_: str | None = R, lot: str | None = "0005", **overrides) -> dict:
    """A synthetic ECB row shaped like 6bgk-3dad (field names from X-SODA2-Fields)."""
    base = {
        "isn_dob_bis_extract": str(isn),
        "ecb_violation_number": f"{39000000 + isn}P",
        "ecb_violation_status": "RESOLVE",
        "dob_violation_number": f"0115{isn:04d}E1352A01",
        "bin": bin_,
        "boro": "2",
        "block": "02754",
        "lot": lot,
        "hearing_date": "20260301",
        "hearing_time": "830",
        "served_date": "20260115",
        "issue_date": "20260115",
        "severity": "Non-Hazardous",
        "violation_type": "Elevators",
        "respondent_name": "SYNTHETIC OWNER LLC",
        "respondent_house_number": "1",
        "respondent_street": "TEST STREET",
        "respondent_city": "BRONX",
        "respondent_zip": "10460",
        "violation_description": "SYNTHETIC DESCRIPTION",
        "penality_imposed": "150",
        "amount_paid": "150",
        "balance_due": "0",
        "infraction_code1": "220",
        "section_law_description1": TEXT_220,
        "aggravated_level": "NO",
        "hearing_status": "IN VIOLATION",
        "certification_status": "CERTIFICATE ACCEPTED",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}  # Socrata omits nulls


_LOT = re.compile(r"\(boro='(\d)' AND block='(\d{5})' AND lot in \('(\d{4})', '(\d{5})'\)\)")


class EcbFake(DobFake):
    """Also honours the ECB lot-family OR clause and `bin='R'`. Synthetic."""

    @staticmethod
    def _filtered(where: str, rows: list[dict]) -> list[dict]:
        lots = _LOT.findall(where)
        if lots:
            m = re.search(r" OR bin='(\d{7})'$", where)
            want_bin = m.group(1) if m else None

            def hit(r):
                on_lot = any(
                    r.get("boro") == b and r.get("block") == blk and r.get("lot") in (l4, l5)
                    for b, blk, l4, l5 in lots
                )
                return on_lot or (want_bin is not None and r.get("bin") == want_bin)

            return [r for r in rows if hit(r)]
        return DobFake._filtered(where, rows)


def retrieve(rows, *, footprints=None, hpd=None, res=None, fake=None, **kw):
    if fake is None:
        fp = footprints if footprints is not None else [fp_row(R)]
        fake = EcbFake([], datasets={ECB: rows, FP: fp})
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    res = res or resolution()
    if hpd is None and res.resolution_level is ResolutionLevel.BUILDING:
        hpd = hpd_result(res=res)
    with fake.client() as c:
        result = retrieve_ecb_violations(res, c, hpd_buildings=hpd, today=TODAY, **kw)
    return result, fake


def one(r, **kw):
    result, _ = retrieve([r], **kw)
    assert len(result.records) == 1, result.status_detail
    return result.records[0]


def one_of_two(r, **kw):
    """`r` next to an ordinary row, so a field absent from `r` is not a schema change."""
    result, _ = retrieve([r, ecb_row(99)], **kw)
    assert result.status is RetrievalStatus.RECORDS_FOUND, result.status_detail
    return next(x for x in result.records if x.isn_dob_bis_extract == r["isn_dob_bis_extract"])


def where_of(fake, dataset=ECB):
    return [
        c.url.params["$where"]
        for c in fake.calls
        if dataset in c.url.path and "count(" in c.url.params.get("$select", "")
    ]


TAX = dict(res=resolution(bbl="2027540005", bin_=None, level=ResolutionLevel.TAX_LOT))


# ---- query shape and scope ----------------------------------------------------------------


def test_query_uses_both_lot_formats_and_the_resolved_bin():
    _, fake = retrieve([ecb_row(1)])
    assert where_of(fake) == [
        f"(boro='2' AND block='02754' AND lot in ('0005', '00005')) OR bin='{R}'"
    ]
    (data,) = [c for c in fake.calls if ECB in c.url.path and "$order" in c.url.params]
    assert data.url.params["$order"] == "isn_dob_bis_extract"
    assert "$select" not in data.url.params


def test_every_family_lot_is_queried_in_both_formats():
    assert lot_clause(["1018220001", "1018227502"]) == (
        "(boro='1' AND block='01822' AND lot in ('0001', '00001')) OR "
        "(boro='1' AND block='01822' AND lot in ('7502', '07502'))"
    )
    assert build_where(["2027540005"], None) == (
        "(boro='2' AND block='02754' AND lot in ('0005', '00005'))"
    )


@pytest.mark.parametrize("bad", ["0027540005", "202754000", "2027540005' OR '1'='1", "6027540005"])
def test_lot_clause_refuses_anything_but_a_bbl(bad):
    with pytest.raises(ValueError):
        lot_clause([bad])


def test_tax_lot_queries_by_lot_only_and_every_row_is_lot_level():
    rows = [ecb_row(1), ecb_row(2, bin_=OTHER), ecb_row(3, bin_=None)]
    result, fake = retrieve(rows, footprints=[fp_row(R), fp_row(OTHER)], **TAX)
    assert where_of(fake) == ["(boro='2' AND block='02754' AND lot in ('0005', '00005'))"]
    assert {r.attribution for r in result.records} == {LOT}
    assert result.join_scope is JoinScope.TAX_LOT and result.resolved_bin is None
    assert result.bin_clause is None and result.bin_evidence == []
    assert not result.summary.building_specific_claims_allowed
    assert result.summary.requested_building_financials is None
    assert not any(HPD in c.url.path for c in fake.calls)  # no HPD lookup at tax lot


def test_tax_lot_never_searches_by_bin_even_for_a_bin_on_the_resolver_record():
    res = resolution(bbl=BBL, bin_=R, level=ResolutionLevel.TAX_LOT)
    stray = ecb_row(9, bin_=R, boro="1", block="00001", lot="0001")  # matches only by BIN
    result, fake = retrieve([ecb_row(1), stray], res=res)
    assert len(result.records) == 1
    for c in fake.calls:
        assert "bin=" not in c.url.params.get("$where", "")


def test_tax_lot_unknown_bin_is_noted_but_never_looked_up_or_promoted():
    result, fake = retrieve([ecb_row(1, bin_="2999999")], **TAX)
    (rec,) = result.records
    assert rec.attribution is LOT
    assert QualityFlag.BIN_NOT_ON_CURRENT_LOT in rec.quality_flags
    assert [b.classification for b in result.row_bins_not_on_lot] == ["not_checked"]
    assert not any("bin in" in c.url.params.get("$where", "") for c in fake.calls)


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unresolved_addresses_send_no_request(outcome):
    fake = EcbFake([], datasets={ECB: [ecb_row(1)], FP: [fp_row(R)]})
    result, fake = retrieve([], res=resolution(outcome=outcome), fake=fake, hpd=hpd_result())
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


def test_hpd_result_for_another_lot_is_refused():
    with pytest.raises(ValueError):
        retrieve([ecb_row(1)], hpd=hpd_result(res=resolution(bbl="2027540006")))


# ---- attribution decision table -----------------------------------------------------------


def test_resolved_bin_on_the_lot_is_this_building():
    rec = one(ecb_row(1))
    assert rec.attribution is THIS and rec.lot_membership is LotMembership.CONFIRMED
    assert rec.attribution_basis is AttributionBasis.ROW_IDENTIFIERS
    assert rec.evidence_kind is EvidenceKind.ENFORCEMENT_ACTION
    assert rec.matched_by == [MatchedBy.LOT, MatchedBy.RESOLVED_BIN]


def test_other_corroborated_bin_on_the_lot_is_another_building():
    hpd = hpd_result(bins=(R, OTHER))
    rec = one(ecb_row(1, bin_=OTHER), footprints=[fp_row(R), fp_row(OTHER)], hpd=hpd)
    assert rec.attribution is OTHER_B and rec.lot_membership is LotMembership.CONFIRMED
    assert rec.matched_by == [MatchedBy.LOT]
    assert QualityFlag.BIN_SINGLE_SOURCE not in rec.quality_flags


def test_footprints_only_bin_is_another_building_flagged_single_source():
    rec = one(ecb_row(1, bin_=OTHER), footprints=[fp_row(R), fp_row(OTHER)])
    assert rec.attribution is OTHER_B
    assert QualityFlag.BIN_SINGLE_SOURCE in rec.quality_flags


def test_bin_in_no_current_source_is_retired_or_unknown_and_not_searched_for_dob():
    result, fake = retrieve([ecb_row(1), ecb_row(2, bin_="2010717")])
    rec = next(r for r in result.records if r.source_bin == "2010717")
    assert rec.attribution is UNATTR and rec.lot_membership is LotMembership.CONFIRMED
    assert QualityFlag.BIN_RETIRED_OR_UNKNOWN in rec.quality_flags
    (seen,) = result.row_bins_not_on_lot
    assert (seen.bin, seen.rows, seen.classification) == ("2010717", 1, "retired_or_unknown")
    assert "not added to the DOB complaint BIN search" in seen.note
    assert "2010717" not in {e.bin for e in result.bin_evidence}
    lookups = [c for c in fake.calls if "bin in ('2010717')" in c.url.params.get("$where", "")]
    assert lookups  # one Footprints lookup by BIN, recorded in provenance
    assert [p.dataset_id for p in result.bin_evidence_provenance] == [FP, HPD, FP]


def test_bin_that_footprints_places_on_another_lot_is_flagged_and_unattributed():
    fp = [fp_row(R), fp_row("2222222", base="2030000001")]
    result, _ = retrieve([ecb_row(1, bin_="2222222")], footprints=fp)
    (rec,) = result.records
    assert rec.attribution is UNATTR and rec.lot_membership is LotMembership.CONFLICTING
    assert QualityFlag.BIN_ON_OTHER_LOT in rec.quality_flags
    assert result.row_bins_not_on_lot[0].classification == "on_other_lot"
    assert result.row_bins_not_on_lot[0].footprints_lots_for_bin == ["2030000001"]


@pytest.mark.parametrize(
    "bin_,flag",
    [
        (None, QualityFlag.BIN_MISSING),
        ("2000000", QualityFlag.BIN_PLACEHOLDER),
        ("20061X5", QualityFlag.BIN_INVALID),
    ],
)
def test_missing_placeholder_or_invalid_bin_is_unattributed(bin_, flag):
    rec = one_of_two(ecb_row(1, bin_=bin_))
    assert rec.attribution is UNATTR and rec.lot_membership is LotMembership.CONFIRMED
    assert flag in rec.quality_flags


def test_two_source_conflict_on_another_bin_never_yields_a_building():
    # HPD places 2333333 on the lot; Footprints places it on another lot.
    hpd = hpd_result(bins=(R, "2333333"))
    fp = [fp_row(R), fp_row("2333333", base="2030000001")]
    result, _ = retrieve([ecb_row(1, bin_="2333333")], footprints=fp, hpd=hpd)
    (rec,) = result.records
    assert rec.attribution is UNATTR and rec.lot_membership is LotMembership.CONFLICTING
    assert QualityFlag.BIN_LOT_CONFLICT in rec.quality_flags
    assert result.bin_lot_conflicts == ["2333333"]
    assert result.summary.building_specific_claims_allowed  # R itself is fine


def test_conflict_on_the_resolved_bin_denies_every_building_claim_and_financials():
    # Resolver and HPD say R is on the lot; Footprints places R on another lot.
    fp = [fp_row(OTHER), fp_row(R, base="2030000001")]
    hpd = hpd_result(bins=(R, OTHER))
    rows = [ecb_row(1), ecb_row(2, lot=None), ecb_row(3, bin_=OTHER)]
    result, _ = retrieve(rows, footprints=fp, hpd=hpd)
    by_bin = {r.isn_dob_bis_extract: r for r in result.records}
    for isn in ("1", "2"):
        assert by_bin[isn].attribution is UNATTR
        assert QualityFlag.BIN_LOT_CONFLICT in by_bin[isn].quality_flags
    assert by_bin["3"].attribution is OTHER_B
    s = result.summary
    assert not s.building_specific_claims_allowed and s.requested_building_financials is None
    assert "bin_lot_conflict" in s.building_claims_denied_reason


def test_row_lot_outside_the_family_is_a_lot_identifier_conflict():
    rec = one(ecb_row(1, block="02754", lot="0061"))  # returned only by bin='R'
    assert rec.attribution is UNATTR and rec.lot_membership is LotMembership.CONFLICTING
    assert QualityFlag.LOT_IDENTIFIER_CONFLICT in rec.quality_flags
    assert rec.matched_by == [MatchedBy.RESOLVED_BIN]


@pytest.mark.parametrize(
    "overrides,fmt",
    [
        ({"lot": None}, LotFormat.MISSING),
        ({"lot": "12"}, LotFormat.MALFORMED),
        ({"block": None}, LotFormat.MISSING),
        ({"boro": "6"}, LotFormat.MALFORMED),
        ({"block": "2754"}, LotFormat.MALFORMED),
    ],
)
def test_missing_or_malformed_lot_with_the_resolved_bin_is_this_building_via_bin(overrides, fmt):
    rec = one_of_two(ecb_row(1, **overrides))
    assert rec.lot_format is fmt and rec.row_bbl is None
    assert rec.attribution is THIS and rec.lot_membership is LotMembership.VIA_BIN_MAPPING
    assert QualityFlag.LOT_PARTS_MISSING in rec.quality_flags


def test_condo_base_lot_keeps_bin_attribution_and_never_reassigns_the_other_building():
    # Billing lot 7502 resolved; Footprints gives base lot 0001 for both buildings.
    res = resolution(bbl="1018227502", bin_="1054974")
    fp = [
        fp_row("1054974", base="1018220001", mappluto="1018227502"),
        fp_row("1055002", base="1018220001", mappluto="1018227502"),
    ]
    hpd = hpd_result(bins=("1054974", "1055002"), res=res, boroid="1", block="1822", lot="7502")
    rows = [
        ecb_row(1, bin_="1054974", boro="1", block="01822", lot="00001"),
        ecb_row(2, bin_="1054974", boro="1", block="01822", lot="7502"),
        ecb_row(3, bin_="1055002", boro="1", block="01822", lot="0001"),
    ]
    result, fake = retrieve(rows, footprints=fp, hpd=hpd, res=res)
    got = {r.isn_dob_bis_extract: r for r in result.records}
    assert got["1"].attribution is THIS and QualityFlag.CONDO_LOT_FAMILY in got["1"].quality_flags
    assert (
        got["2"].attribution is THIS and QualityFlag.CONDO_LOT_FAMILY not in got["2"].quality_flags
    )
    assert got["3"].attribution is OTHER_B
    assert result.lot_family == ["1018220001", "1018227502"]
    assert where_of(fake)[0].count("lot in") == 2


# ---- duplicates ---------------------------------------------------------------------------


def test_row_satisfying_both_clauses_comes_back_once():
    result, _ = retrieve([ecb_row(1)])
    assert len(result.records) == 1 and result.provenance.source_count == 1
    assert result.summary.matched_by_counts == {"lot+resolved_bin": 1}


def test_identical_copies_collapse_with_every_copy_in_lineage_and_fail_incomplete():
    store = InMemoryEvidenceStore()
    result, _ = retrieve([ecb_row(1), ecb_row(1)], evidence=store)
    (rec,) = result.records
    assert [s.row_index for s in rec.source_rows] == [0, 1]
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in rec.quality_flags
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION  # paging key repeated
    assert store.get(rec.original_digest) is not None


def test_same_extract_id_with_different_content_keeps_both_and_fails_closed():
    result, _ = retrieve([ecb_row(1), ecb_row(1, balance_due="500")])
    assert len(result.records) == 2
    for r in result.records:
        assert QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags
        assert r.attribution is UNATTR


def test_repeated_summons_number_under_two_extract_ids_is_never_collapsed():
    rows = [ecb_row(1), ecb_row(2, ecb_violation_number="39000001P")]
    result, _ = retrieve(rows)
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 2
    for r in result.records:
        assert QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags
        assert r.attribution is UNATTR


# ---- statuses: correction, adjudication, certification ------------------------------------


@pytest.mark.parametrize(
    "hearing,adjudication",
    [
        ("IN VIOLATION", Adjudication.UPHELD),
        ("CURED/IN-VIO", Adjudication.UPHELD),
        ("STIPULATION/IN-VIO", Adjudication.UPHELD),
        ("POP/IN-VIO", Adjudication.UPHELD),
        ("ADMIT/IN-VIO", Adjudication.UPHELD),
        ("DEFAULT", Adjudication.UPHELD_BY_DEFAULT),
        ("DISMISSED", Adjudication.DISMISSED),
        ("WRITTEN OFF", Adjudication.OTHER),
        ("PENDING", Adjudication.PENDING),
        (None, Adjudication.UNKNOWN),
    ],
)
def test_adjudication_follows_the_approved_mapping(hearing, adjudication):
    rec = one_of_two(ecb_row(1, hearing_status=hearing, ecb_violation_status="ACTIVE"))
    assert rec.adjudication is adjudication and rec.hearing_status_raw == hearing
    assert (QualityFlag.STATUS_UNKNOWN in rec.quality_flags) is (hearing is None)


def test_mapping_matches_the_approved_file_value_by_value():
    approved = json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "docs"
            / "task5"
            / "proposed_mappings.json"
        ).read_text()
    )["ecb_adjudication"]
    for row in approved:
        raw = None if row["hearing_status"] == "None" else row["hearing_status"]
        assert classify_hearing(raw)[0].value == row["adjudication"]


@pytest.mark.parametrize("hearing", ["DEFAULT", "POP/IN-VIO", "WRITTEN OFF"])
def test_undocumented_hearing_values_carry_their_uncertainty(hearing):
    rec = one(ecb_row(1, hearing_status=hearing))
    assert "not documented" in rec.adjudication_note
    assert rec.hearing_status_raw == hearing  # raw value always shown


def test_no_note_invents_a_tribunal_finding():
    for entry in reference()["adjudication"].values():
        for word in ("guilty", "found liable", "found the respondent", "proved"):
            assert word not in entry["note"].lower()


def test_unlisted_hearing_status_is_withheld_and_not_grouped():
    rec = one(ecb_row(1, hearing_status="MR. JOHN SENTINEL HEARD"))
    assert rec.adjudication is Adjudication.UNKNOWN and rec.hearing_status_raw == WITHHELD_VALUE
    assert {QualityFlag.STATUS_UNKNOWN, QualityFlag.UNCOMMON_VALUE_WITHHELD} <= set(
        rec.quality_flags
    )


@pytest.mark.parametrize(
    "raw,status",
    [
        ("ACTIVE", CorrectionStatus.OPEN_WITH_DOB),
        ("RESOLVE", CorrectionStatus.RESOLVED_WITH_DOB),
        ("Unknown", CorrectionStatus.UNKNOWN),
        (None, CorrectionStatus.UNKNOWN),
    ],
)
def test_correction_status_is_dobs_and_kept_apart(raw, status):
    rec = one_of_two(ecb_row(1, ecb_violation_status=raw))
    assert rec.correction_status is status and rec.correction_status_raw == raw
    assert (QualityFlag.STATUS_UNKNOWN in rec.quality_flags) is (status is CorrectionStatus.UNKNOWN)


@pytest.mark.parametrize(
    "status,hearing,disagree",
    [
        ("ACTIVE", "DISMISSED", True),
        ("RESOLVE", "PENDING", True),
        ("RESOLVE", "IN VIOLATION", False),
        ("ACTIVE", "PENDING", False),
        ("RESOLVE", "DISMISSED", False),
    ],
)
def test_status_disagreements_are_flagged_not_reconciled(status, hearing, disagree):
    rec = one(ecb_row(1, ecb_violation_status=status, hearing_status=hearing))
    assert (QualityFlag.STATUS_FIELDS_DISAGREE in rec.quality_flags) is disagree
    assert rec.correction_status_raw == status and rec.hearing_status_raw == hearing


def test_certification_is_kept_as_recorded_and_separate():
    rec = one(ecb_row(1, certification_status="NO COMPLIANCE RECORDED"))
    assert rec.certification_status == "NO COMPLIANCE RECORDED"
    assert rec.correction_status is CorrectionStatus.RESOLVED_WITH_DOB
    assert rec.adjudication is Adjudication.UPHELD


def test_summary_crosstab_keeps_statuses_separate():
    rows = [
        ecb_row(1),
        ecb_row(2, ecb_violation_status="ACTIVE", hearing_status="DEFAULT"),
        ecb_row(3, hearing_status="PENDING"),
    ]
    result, _ = retrieve(rows)
    b = result.summary.by_attribution[THIS]
    assert b.by_correction_and_adjudication == {
        "open_with_dob|upheld_by_default": 1,
        "resolved_with_dob|pending": 1,
        "resolved_with_dob|upheld": 1,
    }
    assert result.summary.status_fields_disagree == 1
    assert "DEFAULT" in result.summary.adjudication_notes
    assert "not by itself a finding" in result.summary.statement


@pytest.mark.parametrize(
    "raw,severity,undocumented",
    [
        ("Hazardous", Severity.HAZARDOUS, False),
        ("Non-Hazardous", Severity.NON_HAZARDOUS, False),
        ("Unknown", Severity.UNKNOWN, False),
        ("CLASS - 1", Severity.CLASS_1, True),
        ("CLASS - 2", Severity.CLASS_2, True),
        ("CLASS - 3", Severity.CLASS_3, True),
    ],
)
def test_severity_keeps_the_raw_value_and_flags_undocumented_classes(raw, severity, undocumented):
    rec = one(ecb_row(1, severity=raw))
    assert rec.severity is severity and rec.severity_raw == raw
    assert (QualityFlag.SEVERITY_UNDOCUMENTED_VALUE in rec.quality_flags) is undocumented


# ---- money ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,value,bad",
    [
        ("150", Decimal("150"), False),
        ("150.50", Decimal("150.50"), False),
        ("-350", Decimal("-350"), False),
        ("0", Decimal("0"), False),
        (None, None, False),
        ("1e3", None, True),
        ("$150", None, True),
        ("150.555", None, True),
    ],
)
def test_money_is_parsed_as_decimal_only(raw, value, bad):
    assert parse_money(raw) == (value, bad)


def test_financials_count_only_the_requested_building():
    hpd = hpd_result(bins=(R, OTHER))
    rows = [
        ecb_row(1, penality_imposed="100.25", amount_paid="0", balance_due="100.25"),
        ecb_row(2, penality_imposed="200", amount_paid="300", balance_due="-100"),
        ecb_row(3, balance_due="abc"),  # unparseable: counted, excluded from totals
        ecb_row(4, bin_=OTHER, balance_due="9999"),  # another building
        ecb_row(5, bin_=None, balance_due="7777"),  # unattributed
    ]
    result, _ = retrieve(rows, footprints=[fp_row(R), fp_row(OTHER)], hpd=hpd)
    fin = result.summary.requested_building_financials
    assert fin.summonses == 3 and fin.summonses_with_unparseable_amounts == 1
    assert fin.penalty_imposed_total == Decimal("300.25")
    assert fin.balance_due_total == Decimal("0.25")
    assert (fin.summonses_with_positive_balance, fin.summonses_with_negative_balance) == (1, 1)
    assert isinstance(fin.balance_due_total, Decimal)
    rec = next(r for r in result.records if r.isn_dob_bis_extract == "3")
    assert rec.balance_due is None and QualityFlag.AMOUNT_UNPARSEABLE in rec.quality_flags
    assert (
        json.loads(result.model_dump_json())["summary"]["requested_building_financials"][
            "balance_due_total"
        ]
        == "0.25"
    )  # serialised as an exact string, never a float


# ---- dates ------------------------------------------------------------------------------------


def test_dates_are_parsed_and_checked():
    assert parse_ecb_date("20260115", as_of=TODAY)[0].value == dt.date(2026, 1, 15)
    v, f = parse_ecb_date("2026-01-15 APT 4B", as_of=TODAY)
    assert (v.raw, v.value, f) == (None, None, QualityFlag.UNPARSEABLE_DATE)  # raw not shown
    assert parse_ecb_date("20261340", as_of=TODAY)[1] is QualityFlag.UNPARSEABLE_DATE
    assert parse_ecb_date("20270101", as_of=TODAY)[1] is QualityFlag.IMPLAUSIBLE_DATE


def test_future_hearing_is_allowed_and_issue_after_service_is_flagged():
    rec = one(ecb_row(1, hearing_date="20270115", issue_date="20260115", served_date="20260110"))
    assert rec.hearing_date.value == dt.date(2027, 1, 15)
    assert QualityFlag.IMPLAUSIBLE_DATE not in rec.quality_flags
    assert QualityFlag.DATE_ORDER in rec.quality_flags


# ---- privacy ----------------------------------------------------------------------------------

SENTINELS = (
    "JOHN SENTINEL",
    "APT 4B-SENTINEL",
    "7TH FLOOR SENTINEL",
    "sentinel@example.com",
    "212-555-0199",
    "INJURED SENTINEL",
)


def _sentinel_row(isn: int, text: str) -> dict:
    return ecb_row(
        isn,
        respondent_name=f"MR. {text}",
        respondent_house_number=text,
        respondent_street=text,
        respondent_city=text,
        respondent_zip=text,
        violation_description=f"{text} AT PREMISES",
        section_law_description1=f"28-105.11 {text}",  # code on the list, text not
        infraction_code2="SENTINEL",
        section_law_description2=text,
        violation_type=text,
        certification_status=text,
        hearing_status=text,
        severity=text,
        aggravated_level=text,
        hearing_time=text,
        dob_violation_number=text,
        ecb_violation_number=f"MR {text}",
    )


@pytest.mark.parametrize("text", SENTINELS)
def test_sentinels_in_every_text_field_stay_in_private_evidence_only(text, caplog):
    store = InMemoryEvidenceStore()
    with caplog.at_level(logging.DEBUG):
        result, fake = retrieve([_sentinel_row(1, text)], evidence=store)
    public = result.model_dump_json()
    for needle in (text, text.split()[0] if "@" not in text else text, "SENTINEL", "sentinel"):
        assert needle not in public
        assert needle not in caplog.text
        for c in fake.calls:
            assert needle not in str(c.url)
    (rec,) = result.records
    raw = store.get(rec.original_digest)
    assert raw.original_row["respondent_name"] == f"MR. {text}"
    assert verify(raw, KEY) and rec.digest_basis is DigestBasis.ORIGINAL_ROW
    assert QualityFlag.UNCOMMON_VALUE_WITHHELD in rec.quality_flags
    assert QualityFlag.IDENTIFIER_WITHHELD in rec.quality_flags
    assert {"respondent_name", "violation_description", "hearing_time"} <= set(raw.redacted_fields)


def test_allowlisted_infraction_text_is_shown_and_others_are_withheld():
    rec = one(
        ecb_row(
            1,
            section_law_description1=TEXT_220,
            infraction_code2="220",  # same code, a different text: withheld
            section_law_description2="28-105.11 SOMETHING ELSE",
            infraction_code3="ZZZZZZZ",  # not on the measured code list
            section_law_description3="ANY TEXT",
        )
    )
    first, second, third = rec.infractions
    assert (first.code, first.law_description, first.text_withheld) == ("220", TEXT_220, False)
    assert (second.code, second.law_description, second.text_withheld) == ("220", None, True)
    assert (third.code, third.law_description, third.text_withheld) == (None, None, True)


def test_privacy_pattern_pairs_are_withheld_fail_closed():
    ref = reference()
    for code in ref["infraction_texts_withheld"]["privacy_pattern"]:
        assert code not in ref["infraction_texts"] and code in ref["infraction_codes"]


def test_respondent_fields_and_description_never_appear_on_the_record():
    rec = one(ecb_row(1))
    fields = set(type(rec).model_fields)
    assert not {f for f in fields if f.startswith("respondent")}
    assert "violation_description" not in fields and "description" not in fields
    assert "hearing_time" not in fields
    dumped = rec.model_dump_json(exclude={"withheld_fields"})
    for value in ("SYNTHETIC OWNER LLC", "SYNTHETIC DESCRIPTION", "TEST STREET"):
        assert value not in dumped


def test_digest_is_keyed_and_redacted_hash_covers_only_public_values():
    from nyc_apartment_agent.records.redaction import canonical_sha256

    r = ecb_row(1)
    rec = one(r)
    assert rec.original_digest != canonical_sha256(r)
    withheld = set(rec.withheld_fields)
    expected = {k: ("[WITHHELD]" if k in withheld else v) for k, v in r.items()}
    assert rec.redacted_sha256 == canonical_sha256(expected)


# ---- retrieval status, outages, pagination, schema ----------------------------------------


def _fake(rows, fp=None):
    return EcbFake([], datasets={ECB: rows, FP: fp or [fp_row(R)]})


def test_records_found_and_no_records_found():
    result, _ = retrieve([ecb_row(1), ecb_row(2)])
    assert result.status is RetrievalStatus.RECORDS_FOUND and not result.partial
    result, _ = retrieve([])
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    assert result.provenance.source_count == 0 and result.summary.total_summonses == 0


@pytest.mark.parametrize("code", [500, 503, 429])
def test_source_down_before_count_is_unavailable_not_empty(code):
    fake = _fake([ecb_row(1)])
    fake.fail_at = {2: code, 3: code}  # calls 0-1 are Footprints; 2 is the ECB count
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.records == [] and result.summary is None
    assert result.provenance.where  # what would have been asked is kept


def test_footprints_down_makes_the_lot_family_and_the_result_incomplete():
    fake = _fake([ecb_row(1)])
    fake.fail_at = {0: 503, 1: 503}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and result.partial
    assert any("lot_family_incomplete" in r for r in result.provenance.incomplete_reasons)
    assert result.lot_family == [BBL] and not result.bin_evidence_complete
    assert result.summary.total_summonses == 1  # rows kept, marked partial


def test_tax_lot_footprints_down_is_incomplete():
    fake = _fake([ecb_row(1)])
    fake.fail_at = {0: 503, 1: 503}
    result, _ = retrieve([], fake=fake, **TAX)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION


def test_row_bin_lookup_failure_keeps_rows_unattributed_and_says_so():
    fake = _fake([ecb_row(1), ecb_row(2, bin_="2010717")])
    fake.fail_at = {4: 503, 5: 503}  # the by-BIN Footprints count, after the ECB fetch
    result, _ = retrieve([], fake=fake)
    rec = next(r for r in result.records if r.source_bin == "2010717")
    assert rec.attribution is UNATTR and QualityFlag.BIN_NOT_ON_CURRENT_LOT in rec.quality_flags
    assert result.row_bins_not_on_lot[0].classification == "not_checked"
    assert any(w.startswith("row_bin_lookup_failed") for w in result.provenance.warnings)
    assert result.status is RetrievalStatus.RECORDS_FOUND  # every row was fetched


def test_failed_page_keeps_partial_rows_marked():
    fake = _fake([ecb_row(i) for i in range(1, 16)])
    fake.fail_at = {4: 500, 5: 500}  # second ECB page
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.partial and len(result.records) == 10


def test_short_page_is_incomplete():
    fake = _fake([ecb_row(i) for i in range(1, 16)])
    fake.short_at = {3}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION


def test_dataset_changing_during_retrieval_is_retried_then_incomplete():
    fake = _fake([ecb_row(i) for i in range(1, 16)])
    fake.truth_at = {
        4: NEWER_TRUTH,
        6: "Thu, 22 Oct 2026 01:00:00 GMT",
        8: "Fri, 23 Oct 2026 01:00:00 GMT",
        10: "Sat, 24 Oct 2026 01:00:00 GMT",
    }
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.provenance.restarts == 2


def test_row_cap_is_incomplete():
    result, _ = retrieve([ecb_row(i) for i in range(1, 13)], row_cap=5)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and len(result.records) == 5


def test_missing_required_field_is_schema_changed():
    rows = [ecb_row(1), ecb_row(2)]
    for r in rows:
        del r["hearing_status"]
    result, _ = retrieve(rows)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.status_detail.startswith("schema_changed") and result.records == []


def test_provenance_records_every_lookup_and_the_coverage_limits():
    result, _ = retrieve([ecb_row(1)])
    assert [p.dataset_id for p in result.bin_evidence_provenance] == [FP, HPD]
    assert result.provenance.dataset_id == ECB and result.provenance.data_as_of
    assert result.lot_formats_queried == [f"{BBL}:0005", f"{BBL}:00005"]
    assert any("never added to the DOB complaint BIN search" in n for n in result.coverage_notes)
    rec = result.records[0]
    assert rec.query_url and rec.fetched_at and rec.data_as_of == result.provenance.data_as_of


def test_parse_lot_forms():
    assert parse_lot({"boro": "1", "block": "00835", "lot": "00041"}) == (
        "1008350041",
        LotFormat.FIVE_DIGIT,
    )
    assert parse_lot({"boro": "1", "block": "00835", "lot": "0041"}) == (
        "1008350041",
        LotFormat.FOUR_DIGIT,
    )
    assert parse_lot({"boro": "1", "block": "00835", "lot": "10041"}) == (
        None,
        LotFormat.MALFORMED,
    )


# ---- model invariants ---------------------------------------------------------------------


def test_model_refuses_a_bin_clause_at_tax_lot_scope():
    result, _ = retrieve([ecb_row(1)], **TAX)
    data = result.model_dump()
    data.update(bin_clause=R)
    with pytest.raises(ValidationError):
        type(result).model_validate(data)


def test_model_refuses_building_attribution_below_building_scope():
    result, _ = retrieve([ecb_row(1)])
    data = result.model_dump()
    data.update(join_scope=JoinScope.TAX_LOT, resolved_bin=None, bin_clause=None, summary=None)
    data["provenance"]["where"] = "(boro='2')"
    with pytest.raises(ValidationError):
        type(result).model_validate(data)


def test_model_refuses_financials_when_claims_are_denied():
    result, _ = retrieve([ecb_row(1)])
    data = result.model_dump()
    data["summary"]["building_specific_claims_allowed"] = False
    with pytest.raises(ValidationError):
        type(result).model_validate(data)


def test_model_refuses_this_building_with_a_conflict_flag():
    rec = one(ecb_row(1))
    data = rec.model_dump()
    data["quality_flags"] = [QualityFlag.BIN_RETIRED_OR_UNKNOWN]
    with pytest.raises(ValidationError):
        type(rec).model_validate(data)


def test_model_refuses_a_law_text_without_its_code():
    rec = one(ecb_row(1))
    data = rec.model_dump()
    data["infractions"][0]["code"] = None
    with pytest.raises(ValidationError):
        type(rec).model_validate(data)


@pytest.mark.parametrize(
    "raw,shown",
    [
        ("01272010LL26/04EMGPWR", True),  # recorded at 350 5th Ave
        ("060502BMD901175-01", True),  # recorded at 1845 ACP
        ("032297 E 1352 A 06", True),
        ("212-555-0199", False),  # phone pattern
        ("APT 4B 1234", False),  # unit pattern
        ("MR JOHN SENTINEL", False),  # no digits, honorific
    ],
)
def test_dob_violation_number_format_check(raw, shown):
    rec = one(ecb_row(1, dob_violation_number=raw))
    assert (rec.dob_violation_number == raw) is shown
    assert (QualityFlag.IDENTIFIER_WITHHELD in rec.quality_flags) is (not shown)
