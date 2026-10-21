"""HPD complaints/problems (Task 2) on SYNTHETIC rows and resolutions."""

import datetime as dt
import logging

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.complaint_models import (
    ComplaintAttribution,
    ComplaintOutcome,
    ComplaintSourceResult,
)
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_complaints import (
    classify_text,
    disposition,
    lot_where,
    normalize_fetch,
    retrieve_hpd_complaints,
)
from nyc_apartment_agent.records.join_policy import plan_lot_query
from nyc_apartment_agent.records.models import (
    Attribution,
    EvidenceKind,
    LocationScope,
    PageRecord,
    QualityFlag,
    RetrievalProvenance,
    RetrievalStatus,
)
from nyc_apartment_agent.records.redaction import WITHHELD, canonical_sha256
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from nyc_apartment_agent.sources.socrata_pager import PagedFetch
from tests.hpd_helpers import NEWER_TRUTH, FakeSocrata, complaint_row, resolution

TODAY = dt.date(2026, 10, 21)
KEY = b"synthetic-test-key"
row = complaint_row


def retrieve(rows, res=None, fake=None, **kw):
    fake = fake or FakeSocrata(rows)
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    with fake.client() as c:
        result = retrieve_hpd_complaints(res or resolution(), c, today=TODAY, **kw)
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


@pytest.mark.parametrize("code", [500, 503, 429])
def test_source_unavailable_is_not_no_records(code):
    fake = FakeSocrata([row(1)])
    fake.fail_at = {0: code, 1: code}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.records == [] and result.summary is None


def test_failed_page_gives_incomplete_pagination():
    fake = FakeSocrata([row(i, cid=i) for i in range(1, 16)])
    fake.fail_at = {2: 500, 3: 500}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.partial and len(result.records) == 10


def test_short_page_gives_incomplete_pagination():
    fake = FakeSocrata([row(i, cid=i) for i in range(1, 16)])
    fake.short_at = {1}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.partial


def test_dataset_refresh_on_every_attempt_gives_incomplete_pagination():
    fake = FakeSocrata([row(i, cid=i) for i in range(1, 16)])
    # The header changes after the count on the first pass and again after the restart.
    fake.truth_at = {2: NEWER_TRUTH, 4: "Thu, 22 Oct 2026 01:00:00 GMT"}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert any("changed" in r for r in result.provenance.incomplete_reasons)


def test_row_cap_gives_incomplete_pagination():
    result, _ = retrieve([row(i, cid=i) for i in range(1, 13)], row_cap=5)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert len(result.records) == 5


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unsupported_join_makes_zero_requests(outcome):
    result, fake = retrieve([row(1)], res=resolution(outcome=outcome))
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN
    assert fake.calls == [] and result.records == [] and result.complaints == []


# ---- query ---------------------------------------------------------------------------


def test_query_covers_bbl_and_borough_name_block_lot_never_bin():
    plan = plan_lot_query(resolution())
    assert lot_where(plan) == "bbl=2027540005 OR (borough='BRONX' AND block=2754 AND lot=5)"
    _, fake = retrieve([row(1)])
    assert all(r.url.params["$where"] == lot_where(plan) for r in fake.calls)
    assert all("bin" not in r.url.params["$where"] for r in fake.calls)


# ---- complaints vs problems ----------------------------------------------------------


def test_one_complaint_with_three_problems_is_counted_once():
    result, _ = retrieve([row(1, cid=7), row(2, cid=7), row(3, cid=7), row(4, cid=8)])
    s = result.summary
    assert (s.total_problems, s.total_complaints) == (4, 2)
    assert s.problems_by_attribution[Attribution.THIS_BUILDING].problems == 4
    assert s.complaints_by_attribution[ComplaintAttribution.THIS_BUILDING].complaints == 2
    group = next(c for c in result.complaints if c.complaint_id == "7")
    assert group.problem_count == 3 and group.problem_ids == ["1", "2", "3"]


def test_every_record_is_a_reported_allegation():
    result, _ = retrieve([row(1)])
    assert result.records[0].evidence_kind is EvidenceKind.REPORTED_ALLEGATION
    assert result.complaints[0].evidence_kind is EvidenceKind.REPORTED_ALLEGATION


def test_duplicate_flag_is_counted_not_called_distinct():
    rows = [row(1, problem_duplicate_flag="Y"), row(2), row(3, problem_duplicate_flag=None)]
    result, _ = retrieve(rows)
    b = result.summary.problems_by_attribution[Attribution.THIS_BUILDING]
    assert b.duplicate_reports == 1
    assert b.not_flagged_duplicate == 2
    assert [r.is_duplicate_report for r in result.records] == [True, False, None]
    assert "distinct" not in result.summary.model_dump_json().lower()


# ---- attribution -----------------------------------------------------------------


def test_building_level_buckets():
    rows = [
        row(1, cid=1),
        row(2, cid=2, bin="2006186"),
        row(3, cid=3, bin=None, bbl=None),  # matched by borough name + block + lot
        row(4, cid=4, bin="2000000"),
    ]
    result, _ = retrieve(rows)
    got = {r.problem_id: r.attribution for r in result.records}
    assert got == {
        "1": Attribution.THIS_BUILDING,
        "2": Attribution.OTHER_BUILDING_ON_LOT,
        "3": Attribution.UNATTRIBUTED_ON_LOT,
        "4": Attribution.UNATTRIBUTED_ON_LOT,
    }
    no_bbl = result.records[2]
    assert no_bbl.lot_membership.value == "confirmed"  # via borough name + block + lot
    assert QualityFlag.BBL_MISSING in no_bbl.quality_flags


def test_mixed_complaint_is_never_counted_as_this_building():
    rows = [row(1, cid=5), row(2, cid=5, bin=None), row(3, cid=6)]
    result, _ = retrieve(rows)
    groups = {c.complaint_id: c for c in result.complaints}
    assert groups["5"].attribution is ComplaintAttribution.MIXED
    assert groups["5"].problem_attributions == {"this_building": 1, "unattributed_on_lot": 1}
    by = result.summary.complaints_by_attribution
    assert by[ComplaintAttribution.THIS_BUILDING].complaints == 1
    assert by[ComplaintAttribution.MIXED].complaints == 1
    assert result.summary.conflict_counts["mixed_attribution_complaints"] == 1


def test_tax_lot_result_never_produces_building_claims():
    res = resolution(level=ResolutionLevel.TAX_LOT)
    rows = [row(1, cid=1), row(2, cid=1, bin="2006186"), row(3, cid=2, bin=None)]
    result, _ = retrieve(rows, res=res)
    assert {r.attribution for r in result.records} == {Attribution.LOT_LEVEL}
    assert {c.attribution for c in result.complaints} == {ComplaintAttribution.LOT_LEVEL}
    assert result.resolved_bin is None
    assert not result.summary.building_specific_claims_allowed


def test_model_rejects_building_attribution_at_tax_lot_scope():
    result, _ = retrieve([row(1)], res=resolution(level=ResolutionLevel.TAX_LOT))
    data = result.model_dump()
    data["complaints"][0]["attribution"] = ComplaintAttribution.THIS_BUILDING
    with pytest.raises(ValidationError, match="building-level attribution"):
        ComplaintSourceResult.model_validate(data)


def test_model_rejects_inconsistent_mixed_flag():
    result, _ = retrieve([row(1)])
    data = result.model_dump()
    data["complaints"][0]["attribution"] = ComplaintAttribution.MIXED
    with pytest.raises(ValidationError, match="mixed"):
        ComplaintSourceResult.model_validate(data)


@pytest.mark.parametrize("overrides", [{"lot": "6"}, {"bbl": "2027540006"}, {"borough": "QUEENS"}])
def test_bbl_vs_borough_block_lot_conflict_blocks_attribution(overrides):
    r = one(row(1, **overrides))
    assert QualityFlag.LOT_IDENTIFIER_CONFLICT in r.quality_flags
    assert r.attribution is Attribution.UNATTRIBUTED_ON_LOT


def test_borough_contradicting_bbl_blocks_attribution_even_when_block_lot_are_zero():
    # Seen live on the Empire State Building lot: Manhattan BBL, borough BROOKLYN, 0/0.
    r = one(row(1, borough="QUEENS", block="0", lot="0"))
    assert QualityFlag.LOT_PARTS_MISSING in r.quality_flags
    assert QualityFlag.LOT_IDENTIFIER_CONFLICT in r.quality_flags
    assert r.attribution is Attribution.UNATTRIBUTED_ON_LOT
    ok = one(row(2, block="0", lot="0"))  # same borough, unusable block/lot: BBL decides
    assert ok.attribution is Attribution.THIS_BUILDING


def test_numeric_bin_and_bbl_values_are_handled():
    r = one(row(1, bin=2006185, bbl=2027540005))
    assert r.attribution is Attribution.THIS_BUILDING


def test_differing_duplicate_problem_ids_are_flagged_and_downgraded():
    page = PageRecord(
        index=0,
        url="u",
        rows=3,
        fetched_at="2026-10-21T00:00:00+00:00",
        attempts=1,
        truth_last_modified="t",
        last_modified=None,
        request_id=None,
    )
    fetch = PagedFetch(
        rows=[row(1), row(1), row(1, problem_status="OPEN")],
        provenance=RetrievalProvenance(dataset_id="ygpa-z7cr", pages=[page]),
        row_page=[0, 0, 0],
    )
    out = normalize_fetch(fetch, plan_lot_query(resolution()), as_of=TODAY, digest_key=KEY)
    assert len(out) == 2  # the identical copy collapses
    assert QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in out[1].quality_flags
    assert out[1].attribution is Attribution.UNATTRIBUTED_ON_LOT


# ---- status and dates ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("cs", "ps", "flag"),
    [
        ("CLOSE", "CLOSE", None),
        ("OPEN", "OPEN", None),
        ("OPEN", "CLOSE", None),  # other problems of the complaint may still be open
        ("CLOSE", "OPEN", QualityFlag.STATUS_FIELDS_DISAGREE),  # contradicts the dictionary
        ("PENDING", "CLOSE", QualityFlag.STATUS_UNKNOWN),
    ],
)
def test_complaint_and_problem_status(cs, ps, flag):
    r = one(row(1, complaint_status=cs, problem_status=ps))
    assert r.complaint_status_raw == cs and r.problem_status_raw == ps
    if flag:
        assert flag in r.quality_flags
    else:
        assert QualityFlag.STATUS_FIELDS_DISAGREE not in r.quality_flags
        assert QualityFlag.STATUS_UNKNOWN not in r.quality_flags


def test_status_date_before_received_date_is_flagged_not_corrected():
    # Pattern seen live: received late December, closed early January of the same year.
    r = one(
        row(
            1,
            received_date="2013-12-30T17:23:00.000",
            problem_status_date="2013-01-03T16:06:00.000",
        )
    )
    assert QualityFlag.DATE_ORDER in r.quality_flags
    assert r.dates["problem_status_date"].value == dt.date(2013, 1, 3)  # raw date kept


def test_complaint_fields_disagreeing_across_problems_are_flagged():
    rows = [row(1, cid=9), row(2, cid=9, received_date="2026-02-01T10:00:00.000")]
    result, _ = retrieve(rows)
    assert QualityFlag.COMPLAINT_FIELDS_DISAGREE in result.complaints[0].quality_flags


def test_open_complaint_counts():
    rows = [row(1, cid=1, complaint_status="OPEN", problem_status="OPEN"), row(2, cid=2)]
    result, _ = retrieve(rows)
    b = result.summary.complaints_by_attribution[ComplaintAttribution.THIS_BUILDING]
    assert (b.open, b.closed) == (1, 1)


# ---- disposition classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        ("... inspected the following conditions. Violations were issued. ...", "issued"),
        ("... inspected the following conditions. A violation was issued. ...", "issued"),
        ("... Violations were previously issued for these conditions. ...", "previously"),
        ("... No violations were issued. The complaint has been closed.", "no_violation"),
        ("... the conditions did not violate the housing laws.", "no_violation"),
        ("... heat was not required at the time of the inspection ...", "no_violation"),
        ("... was not able to gain access to your apartment ...", "no_access"),
        ("... was unable to complete the inspection ...", "not_completed"),
        ("... verified that the following conditions were corrected ...", "corrected"),
        ("... indicated that the condition was corrected ...", "corrected"),
        ("... indicated that heat and hot water had been restored ...", "restored"),
        ("... complaint conditions are still open. HPD notified the owner ...", "open"),
    ],
)
def test_known_templates_are_classified(text, outcome):
    expected = {
        "issued": ComplaintOutcome.VIOLATIONS_ISSUED,
        "previously": ComplaintOutcome.VIOLATIONS_PREVIOUSLY_ISSUED,
        "no_violation": ComplaintOutcome.NO_VIOLATION_FOUND,
        "no_access": ComplaintOutcome.NO_ACCESS,
        "not_completed": ComplaintOutcome.INSPECTION_NOT_COMPLETED,
        "corrected": ComplaintOutcome.CORRECTED_PER_CONTACT,
        "restored": ComplaintOutcome.RESTORED_PER_CONTACT,
        "open": ComplaintOutcome.OPEN_OWNER_NOTIFIED,
    }[outcome]
    assert classify_text(text) is expected


def test_duplicate_template_keeps_the_initial_complaint_outcome():
    d = disposition(
        "More than one complaint was received for this building-wide condition. "
        "This complaint status is for the initial complaint. No violations were issued."
    )
    assert d.outcome is ComplaintOutcome.DUPLICATE_OF_BUILDING_WIDE
    assert d.initial_complaint_outcome is ComplaintOutcome.NO_VIOLATION_FOUND


def test_unknown_text_is_other_with_sanitized_text_kept():
    text = "HPD referred this complaint to another agency. Unit 4C, 3rd floor."
    r = one(row(1, status_description=text))
    assert r.hpd_disposition.outcome is ComplaintOutcome.OTHER
    assert r.hpd_disposition.text is not None and "3rd floor" not in r.hpd_disposition.text
    cov = disposition(None)
    assert cov.outcome is ComplaintOutcome.NO_TEXT


def test_coverage_is_reported_honestly():
    rows = [
        row(1),
        row(2, status_description="SOMETHING NEW"),
        row(3, status_description=None),
    ]
    result, _ = retrieve(rows)
    c = result.summary.classification
    assert (c.problems_with_text, c.classified, c.other, c.no_text) == (2, 1, 1, 1)
    assert c.coverage == 0.5
    assert c.other_texts == [{"text": "SOMETHING NEW", "count": 1}]


def test_disposition_never_identifies_a_specific_violation():
    r = one(row(1))
    assert r.hpd_disposition.outcome is ComplaintOutcome.VIOLATIONS_ISSUED
    assert r.hpd_disposition.identifies_specific_violation is False
    assert "violation_id" not in r.model_dump()


# ---- location scope ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit_type", "scope"),
    [
        ("APARTMENT", LocationScope.UNIT),
        ("BUILDING-WIDE", LocationScope.BUILDING_WIDE),
        ("BUILDING", LocationScope.BUILDING_WIDE),
        ("PUBLIC AREA", LocationScope.COMMON_AREA),
        ("PUBLIC PARTS", LocationScope.COMMON_AREA),
        ("SOMETHING ELSE", LocationScope.UNSPECIFIED),
        (None, LocationScope.UNSPECIFIED),
    ],
)
def test_location_scope_from_unit_type(unit_type, scope):
    assert one(row(1, unit_type=unit_type)).location_scope is scope


# ---- privacy ---------------------------------------------------------------------


def test_public_record_has_no_apartment_anonymous_flag_or_311_key():
    store = InMemoryEvidenceStore()
    raw = row(1, apartment="4C", complaint_anonymous_flag="Y", unique_key="61234567")
    r = one(raw, evidence=store)
    dumped = r.model_dump_json()
    for secret in ("4C", "61234567", 'complaint_anonymous_flag":"Y'):
        assert secret not in dumped  # field names may appear in withheld_fields; values never
    assert r.withheld_fields == ["complaint_anonymous_flag", "unique_key"]
    assert r.has_311_reference is True
    item = store.get(r.original_digest)
    assert item.original_row["unique_key"] == "61234567"  # kept for controlled reconciliation
    assert item.original_row["complaint_anonymous_flag"] == "Y"
    assert verify(item, KEY)
    assert "unique_key" in item.redacted_fields and "apartment" in item.redacted_fields


def test_withheld_markers_from_a_sanitized_fixture_are_treated_the_same():
    raw = row(1, unique_key="61234567")
    sanitized = dict(raw, unique_key=WITHHELD, complaint_anonymous_flag=WITHHELD, apartment="x")
    a, b = one(raw), one(sanitized)
    assert a.withheld_fields == b.withheld_fields
    assert a.has_311_reference == b.has_311_reference


def test_status_description_is_redacted():
    r = one(row(1, status_description="Inspector visited APT 3B on the 2nd floor."))
    assert "3B" not in r.hpd_disposition.text
    assert r.redaction.text_fields_redacted == ["status_description"]
    assert QualityFlag.UNIT_DETAIL_REDACTED in r.quality_flags


def test_summary_and_complaints_carry_no_withheld_values():
    rows = [row(1, unique_key="61234567", complaint_anonymous_flag="Y", apartment="7Q")]
    result, _ = retrieve(rows)
    dumped = result.model_dump_json()
    assert "61234567" not in dumped and "7Q" not in dumped
    assert result.summary.withheld_field_counts == {"complaint_anonymous_flag": 1, "unique_key": 1}


def test_public_record_does_not_carry_a_plain_hash_of_the_original():
    raw = row(1, apartment="4C")
    r = one(raw)
    assert canonical_sha256(raw) not in r.model_dump_json()
    assert one(raw, digest_key=b"other key").original_digest != r.original_digest


def test_logs_never_contain_row_contents(caplog):
    caplog.set_level(logging.DEBUG)
    retrieve([row(1, apartment="9Z", unique_key="61234567", status_description="SECRET 9Z")])
    fake = FakeSocrata([row(1)])
    fake.fail_at = {0: 503, 1: 503}
    retrieve([], fake=fake)
    for s in ("9Z", "61234567", "SECRET"):
        assert s not in caplog.text
