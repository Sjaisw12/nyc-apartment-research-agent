"""HPD registrations (Task 3, step 2) on SYNTHETIC rows and resolutions."""

import datetime as dt

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.admin_models import (
    RegistrationSourceResult,
    RegistrationStatus,
)
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_registrations import (
    registration_status,
    retrieve_hpd_registrations,
)
from nyc_apartment_agent.records.models import (
    Attribution,
    DateValue,
    EvidenceKind,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import FakeSocrata, building_row, registration_row, resolution

TODAY = dt.date(2026, 10, 22)
DATA_UPDATED = dt.date(2026, 8, 12)  # the synthetic catalog entry in FakeSocrata
KEY = b"synthetic-test-key"
row = registration_row


def retrieve(rows, res=None, fake=None, **kw):
    fake = fake or FakeSocrata(rows)
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    with fake.client() as c:
        result = retrieve_hpd_registrations(res or resolution(), c, today=TODAY, **kw)
    return result, fake


def buildings(rows, res=None):
    fake = FakeSocrata(rows)
    with fake.client() as c:
        return retrieve_hpd_buildings(res or resolution(), c, today=TODAY, digest_key=KEY)


# ---- statuses and paging ---------------------------------------------------------


def test_records_found_and_evidence_kind():
    result, _ = retrieve([row("row-1")])
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.records[0].evidence_kind is EvidenceKind.ADMINISTRATIVE_RECORD
    assert result.records[0].source_row_id == "row-1"


def test_pages_are_ordered_by_unique_row_id_with_it_selected():
    rows = [row(f"row-{i:02d}", reg=i, bid=i, bin=str(2006185 + i)) for i in range(1, 13)]
    result, fake = retrieve(rows)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    pages = [r for r in fake.calls if "$offset" in r.url.params]
    assert len(pages) == 2
    assert all(r.url.params["$order"] == ":id" for r in pages)
    assert all(r.url.params["$select"] == "*, :id" for r in pages)


def test_duplicate_row_ids_across_pages_make_the_fetch_incomplete():
    rows = [row("row-1", reg=1, bid=1), row("row-1", reg=2, bid=2)]
    result, _ = retrieve(rows)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert "duplicate_:id_across_pages" in result.provenance.incomplete_reasons
    assert result.summary.requested_building is None  # partial: no status claimed


def test_missing_row_id_makes_the_fetch_incomplete():
    r = row("x")
    del r[":id"]
    result, _ = retrieve([r])
    assert "missing_:id" in result.provenance.incomplete_reasons


def test_same_business_key_same_content_different_row_id_collapses_and_is_flagged():
    store = InMemoryEvidenceStore()
    result, _ = retrieve([row("row-1"), row("row-2"), row("row-3", reg=2, bid=2)], evidence=store)
    assert len(result.records) == 2
    first = result.records[0]
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in first.quality_flags
    # Both source rows stay traceable, each with its own digest and evidence entry.
    assert [ref.source_row_id for ref in first.source_rows] == ["row-1", "row-2"]
    assert all(store.get(ref.original_digest) for ref in first.source_rows)
    s = result.summary
    assert (s.source_rows, s.total_rows, s.identical_rows_collapsed) == (3, 2, 1)
    assert result.provenance.source_count == 3
    assert "3 source rows match the source count; 2 normalized" in result.status_detail


def test_model_rejects_a_record_set_that_drops_a_source_row():
    result, _ = retrieve([row("row-1"), row("row-2")])
    data = result.model_dump()
    data["records"][0]["source_rows"] = data["records"][0]["source_rows"][:1]
    with pytest.raises(ValidationError, match="source count"):
        RegistrationSourceResult.model_validate(data)


def test_same_business_key_different_content_is_kept_as_history():
    # Seen live: registration 15905 / building 707130 has a 2025 row and a 2026 row.
    rows = [
        row(
            "row-1",
            lastregistrationdate="2025-04-22T00:00:00.000",
            registrationenddate="2025-09-01T00:00:00.000",
        ),
        row("row-2"),
    ]
    result, _ = retrieve(rows)
    assert len(result.records) == 2
    assert all(QualityFlag.MULTIPLE_REGISTRATION_ROWS in r.quality_flags for r in result.records)
    assert all(r.attribution is Attribution.THIS_BUILDING for r in result.records)
    rb = result.summary.requested_building
    assert rb.rows_on_file == 2 and rb.latest_end_date.value == dt.date(2026, 9, 1)


def test_rows_for_one_building_with_different_bins_lose_attribution():
    result, _ = retrieve([row("row-1"), row("row-2", reg=999, bin="2006186")])
    assert all(
        QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags for r in result.records
    )
    assert all(r.attribution is Attribution.UNATTRIBUTED_ON_LOT for r in result.records)


def test_shared_registration_covers_several_buildings():
    rows = [row("row-1", bid=1), row("row-2", bid=2, bin="2006186")]
    result, _ = retrieve(rows)
    assert all(r.buildings_on_lot_with_this_registration == 2 for r in result.records)
    assert all(QualityFlag.SHARED_REGISTRATION in r.quality_flags for r in result.records)
    assert result.summary.registration_ids == 1 and len(result.summary.buildings) == 2


@pytest.mark.parametrize("code", [500, 503, 429])
def test_source_unavailable(code):
    fake = FakeSocrata([row("row-1")])
    fake.fail_at = {0: code, 1: code}
    assert retrieve([], fake=fake)[0].status is RetrievalStatus.SOURCE_UNAVAILABLE


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unsupported_join_makes_zero_requests(outcome):
    result, fake = retrieve([row("row-1")], res=resolution(outcome=outcome))
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


def test_row_cap():
    rows = [row(f"row-{i:02d}", reg=i, bid=i) for i in range(1, 9)]
    assert retrieve(rows, row_cap=3)[0].status is RetrievalStatus.INCOMPLETE_PAGINATION


# ---- registration status (D10, D12) --------------------------------------------------


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ("2027-09-01", RegistrationStatus.ON_FILE_END_DATE_NOT_PASSED),
        ("2026-09-01", RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE),
        ("2026-08-12", RegistrationStatus.ON_FILE_END_DATE_PASSED_BEFORE_DATA_UPDATE),
        ("2025-09-01", RegistrationStatus.ON_FILE_END_DATE_PASSED_BEFORE_DATA_UPDATE),
    ],
)
def test_status_depends_on_the_data_update_date(end, expected):
    end_value = DateValue(raw=end, value=dt.date.fromisoformat(end))
    assert registration_status(end_value, TODAY, DATA_UPDATED, True) is expected


def test_status_without_freshness_or_end_date_or_rows():
    end = DateValue(raw="2026-09-01", value=dt.date(2026, 9, 1))
    assert registration_status(end, TODAY, None, True) is (
        RegistrationStatus.ON_FILE_END_DATE_PASSED_FRESHNESS_UNKNOWN
    )
    assert registration_status(DateValue(raw="x", value=None), TODAY, DATA_UPDATED, True) is (
        RegistrationStatus.ON_FILE_END_DATE_UNKNOWN
    )
    assert registration_status(None, TODAY, DATA_UPDATED, False) is RegistrationStatus.NONE_ON_FILE


def test_stale_snapshot_never_reads_as_expired():
    result, _ = retrieve([row("row-1")])
    rb = result.summary.requested_building
    assert rb.status is RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE
    assert rb.status_basis == {
        "judged_on": "2026-10-22",
        "data_updated_on": "2026-08-12",
        "data_updated_source": "catalog rowsUpdatedAt",
    }
    text = result.model_dump_json().lower()
    for word in ("expired", "unregistered", '"noncompliant'):
        assert word not in text


def test_none_on_file_names_the_building_record_but_claims_nothing_more():
    b = buildings([building_row(2780, registrationid="0", legalclassa="0")])
    result, _ = retrieve([], buildings=b)
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    rb = result.summary.requested_building
    assert (rb.status, rb.building_id) == (RegistrationStatus.NONE_ON_FILE, "2780")
    assert "does not show that one was required" in rb.note


def test_freshness_failure_falls_back_to_the_truth_header_and_says_so():
    fake = FakeSocrata([row("row-1")])
    fake.fail_at = {2: 503, 3: 503}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    basis = result.summary.requested_building.status_basis
    assert basis["data_updated_source"] == "X-SODA2-Truth-Last-Modified"


# ---- attribution and cross-check ---------------------------------------------------


def test_tax_lot_is_lot_level_with_no_requested_building():
    res = resolution(level=ResolutionLevel.TAX_LOT)
    result, _ = retrieve([row("row-1")], res=res)
    assert result.records[0].attribution is Attribution.LOT_LEVEL
    assert result.summary.requested_building is None
    assert {b.attribution for b in result.summary.buildings} == {Attribution.LOT_LEVEL}


def test_model_rejects_building_claims_at_tax_lot_scope():
    result, _ = retrieve([row("row-1")], res=resolution(level=ResolutionLevel.TAX_LOT))
    data = result.model_dump()
    data["summary"]["buildings"][0]["attribution"] = "this_building"
    with pytest.raises(ValidationError, match="building-level attribution"):
        RegistrationSourceResult.model_validate(data)


def test_cross_check_with_building_records_by_building_id():
    b = buildings([building_row(122027), building_row(5, bin="2006186")])
    rows = [
        row("row-1"),  # matches building 122027 and its BIN
        row("row-2", reg=2, bid=5, bin="2006999"),  # building 5 has another BIN
        row("row-3", reg=3, bid=77, bin="2006187"),  # no such building record on the lot
    ]
    result, _ = retrieve(rows, buildings=b)
    got = {r.building_id: (r.attribution, r.quality_flags) for r in result.records}
    assert got["122027"][0] is Attribution.THIS_BUILDING
    for bid in ("5", "77"):
        assert got[bid][0] is Attribution.UNATTRIBUTED_ON_LOT
        assert QualityFlag.BUILDING_RECORD_MISMATCH in got[bid][1]


def test_no_cross_check_against_an_incomplete_building_fetch():
    fake = FakeSocrata([building_row(1)])
    fake.fail_at = {1: 500, 2: 500}
    with fake.client() as c:
        b = retrieve_hpd_buildings(resolution(), c, today=TODAY, digest_key=KEY)
    assert b.status is RetrievalStatus.INCOMPLETE_PAGINATION
    result, _ = retrieve([row("row-1", bid=99)], buildings=b)
    assert QualityFlag.BUILDING_RECORD_MISMATCH not in result.records[0].quality_flags


def test_address_is_never_a_join_key():
    # Same address, different building ids: two separate buildings.
    rows = [row("row-1", bid=1), row("row-2", reg=2, bid=2)]
    result, _ = retrieve(rows)
    assert len(result.summary.buildings) == 2
