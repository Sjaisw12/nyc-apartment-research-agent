"""HPD building records (Task 3, step 1) on SYNTHETIC rows and resolutions."""

import datetime as dt
import logging

import pytest
from pydantic import ValidationError

from nyc_apartment_agent.records.admin_models import (
    BuildingSourceResult,
    RequestedBuildingStatus,
)
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verifies_original_row
from nyc_apartment_agent.records.hpd_buildings import lot_where, retrieve_hpd_buildings
from nyc_apartment_agent.records.join_policy import plan_lot_query
from nyc_apartment_agent.records.models import (
    Attribution,
    DigestBasis,
    EvidenceKind,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import NEWER_TRUTH, FakeSocrata, building_row, resolution

TODAY = dt.date(2026, 10, 22)
KEY = b"synthetic-test-key"
row = building_row


def retrieve(rows, res=None, fake=None, **kw):
    fake = fake or FakeSocrata(rows)
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 10)
    with fake.client() as c:
        result = retrieve_hpd_buildings(res or resolution(), c, today=TODAY, **kw)
    return result, fake


def one(r, **kw):
    result, _ = retrieve([r], **kw)
    assert len(result.records) == 1
    return result.records[0]


# ---- statuses --------------------------------------------------------------------


def test_records_found_and_evidence_kind():
    result, _ = retrieve([row(1)])
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.records[0].evidence_kind is EvidenceKind.ADMINISTRATIVE_RECORD


def test_no_records_found_needs_a_zero_count():
    result, _ = retrieve([])
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND


@pytest.mark.parametrize("code", [500, 503, 429])
def test_source_unavailable(code):
    fake = FakeSocrata([row(1)])
    fake.fail_at = {0: code, 1: code}
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE and result.records == []


def test_failed_page_short_page_refresh_and_row_cap_are_incomplete():
    rows = [row(i, bin=str(2006185 + i)) for i in range(1, 16)]
    fake = FakeSocrata(rows)
    fake.fail_at = {2: 500, 3: 500}
    assert retrieve([], fake=fake)[0].status is RetrievalStatus.INCOMPLETE_PAGINATION
    fake = FakeSocrata(rows)
    fake.short_at = {1}
    assert retrieve([], fake=fake)[0].status is RetrievalStatus.INCOMPLETE_PAGINATION
    fake = FakeSocrata(rows)
    fake.truth_at = {2: NEWER_TRUTH, 4: "Thu, 22 Oct 2026 01:00:00 GMT"}
    assert retrieve([], fake=fake)[0].status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert retrieve(rows, row_cap=5)[0].status is RetrievalStatus.INCOMPLETE_PAGINATION


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unsupported_join_makes_zero_requests(outcome):
    result, fake = retrieve([row(1)], res=resolution(outcome=outcome))
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


def test_query_is_by_boroid_block_lot_never_bin_and_ordered_by_unique_id():
    plan = plan_lot_query(resolution())
    assert lot_where(plan) == "boroid=2 AND block=2754 AND lot=5"
    _, fake = retrieve([row(1)])
    pages = [r for r in fake.calls if "$where" in r.url.params]
    assert all("bin" not in r.url.params["$where"] for r in pages)
    assert all(r.url.params.get("$order", "buildingid") == "buildingid" for r in pages)


# ---- freshness (D10) -----------------------------------------------------------------


def test_freshness_separates_data_update_from_metadata_update():
    result, _ = retrieve([row(1)])
    f = result.freshness
    assert f.data_updated_at.startswith("2026-08-12")
    assert f.metadata_updated_at.startswith("2026-10-02")  # recent edit, old data
    assert f.stated_update_frequency == "Monthly"
    assert f.days_since_data_update == 71 and f.stale is True
    assert result.summary.freshness == f


def test_freshness_failure_does_not_discard_verified_records():
    fake = FakeSocrata([row(1)])
    fake.fail_at = {2: 503, 3: 503}  # count, page, then the catalog call fails
    result, _ = retrieve([], fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 1
    assert result.freshness.error and result.freshness.stale is None


# ---- attribution -----------------------------------------------------------------


def test_building_level_buckets_and_requested_record():
    rows = [
        row(1),
        row(2, bin="2006186"),
        row(3, bin=None),
        row(4, bin="2000000"),
    ]
    result, _ = retrieve(rows)
    got = {r.building_id: r.attribution for r in result.records}
    assert got == {
        "1": Attribution.THIS_BUILDING,
        "2": Attribution.OTHER_BUILDING_ON_LOT,
        "3": Attribution.UNATTRIBUTED_ON_LOT,
        "4": Attribution.UNATTRIBUTED_ON_LOT,
    }
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD
    assert (rb.building_id, rb.legal_class_a_units, rb.registration_id) == ("1", 44, "203625")


def test_tax_lot_resolution_stays_lot_level_even_with_a_real_bin():
    # The 1217 West Farms pattern: HPD lists a building with a real BIN on a lot that
    # Phase 1 resolved only to tax-lot level. It must not become "the user's building".
    res = resolution(bbl="2027540005", bin_="2000000", level=ResolutionLevel.TAX_LOT)
    result, _ = retrieve([row(1, bin="2128717")], res=res)
    assert result.records[0].attribution is Attribution.LOT_LEVEL
    assert result.resolved_bin is None
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.NOT_APPLICABLE_TAX_LOT and rb.building_id is None
    assert not result.summary.building_specific_claims_allowed


def test_model_rejects_a_requested_building_at_tax_lot_scope():
    result, _ = retrieve([row(1)], res=resolution(level=ResolutionLevel.TAX_LOT))
    data = result.model_dump()
    data["summary"]["requested_building"]["status"] = "one_current_record"
    with pytest.raises(ValidationError, match="lot-level"):
        BuildingSourceResult.model_validate(data)


@pytest.mark.parametrize("overrides", [{"lot": "6"}, {"block": "0"}, {"boroid": "3"}])
def test_lot_conflict_blocks_attribution(overrides):
    r = one(row(1, **overrides))
    assert QualityFlag.LOT_IDENTIFIER_CONFLICT in r.quality_flags
    assert r.attribution is Attribution.UNATTRIBUTED_ON_LOT


# ---- inactive, demolished, duplicates ------------------------------------------------


@pytest.mark.parametrize(
    ("lifecycle", "status"),
    [
        ("Demolished", "Inactive"),
        ("Building", "Inactive"),
        ("Building", "Pending"),
        ("Land", "Active"),
    ],
)
def test_non_current_records_are_flagged_and_never_chosen(lifecycle, status):
    result, _ = retrieve([row(1, lifecycle=lifecycle, recordstatus=status)])
    r = result.records[0]
    assert not r.is_current and QualityFlag.NON_CURRENT_RECORD in r.quality_flags
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONLY_NON_CURRENT_RECORDS
    assert rb.building_id is None and rb.candidate_building_ids == ["1"]


def test_current_record_is_chosen_over_a_demolished_one_with_the_same_bin():
    rows = [row(1, lifecycle="Demolished", recordstatus="Inactive"), row(2)]
    rb = retrieve(rows)[0].summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD and rb.building_id == "2"


def test_two_current_records_for_one_bin_are_ambiguous():
    result, _ = retrieve([row(1), row(2)])
    assert all(QualityFlag.MULTIPLE_RECORDS_FOR_BIN in r.quality_flags for r in result.records)
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.MULTIPLE_CURRENT_RECORDS
    assert rb.building_id is None and rb.candidate_building_ids == ["1", "2"]


def test_no_record_for_the_requested_bin():
    rb = retrieve([row(1, bin="2006186")])[0].summary.requested_building
    assert rb.status is RequestedBuildingStatus.NO_RECORD_FOR_BIN


def test_registration_id_zero_means_none_on_the_building_record():
    r = one(row(1, registrationid="0", legalclassa="0", dobbuildingclass="NOT AVAILABLE"))
    assert r.registration_id is None and r.registration_id_raw == "0"


def test_original_values_are_kept_next_to_normalized_fields():
    r = one(row(1, bin=2006185, legalclassa="44", streetname="TEST STREET"))
    assert r.source_bin == "2006185" and r.legal_class_a_units == 44
    assert r.address == "1 TEST STREET"


# ---- evidence and logs ---------------------------------------------------------------


def test_live_style_row_is_original_evidence():
    store = InMemoryEvidenceStore()
    r = one(row(1), evidence=store)
    assert r.digest_basis is DigestBasis.ORIGINAL_ROW and r.redaction.applied == "none"
    assert verifies_original_row(store.get(r.original_digest), KEY)


def test_logs_carry_no_row_contents(caplog):
    caplog.set_level(logging.DEBUG)
    retrieve([row(1, streetname="SECRET STREET")])
    assert "SECRET STREET" not in caplog.text
