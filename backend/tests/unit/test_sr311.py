"""311 Service Requests (Task 5, step 4) on SYNTHETIC rows, lots and resolutions.

Covers the query and lot family, D19 exclusion counts, D27 unstable snapshots (matching
and differing headers and counts, alternating versions, exhausted retries, multi-page
changes, row caps, interrupted pages), duplicates, missing BBL and agency, schema changes
and outages, the k311_ token in records and evidence, allowlists, statuses and dates,
summaries, privacy sentinels in every public field, and model invariants. Every key,
row and header here is synthetic. The five recorded addresses are in
test_sr311_cassette.py.
"""

import datetime as dt
import json
import logging
import re

import httpx
import pytest
from pydantic import ValidationError

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records import sr311
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.models import (
    Attribution,
    DateValue,
    DigestBasis,
    JoinScope,
    LotMembership,
    QualityFlag,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
)
from nyc_apartment_agent.records.redaction import K311_PATTERN, k311_token
from nyc_apartment_agent.records.sr311 import (
    build_where,
    classify_status,
    excluded_where,
    null_agency_where,
    parse_date,
    public_value,
    retrieve_service_requests,
)
from nyc_apartment_agent.records.sr311_models import (
    WITHHELD_VALUE,
    CountCheck,
    ExcludedCounts,
    NormalizedServiceRequest,
    RequestStatus,
    ServiceRequestResult,
)
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from nyc_apartment_agent.sources.clients import socrata_client
from tests.hpd_helpers import NEWER_TRUTH, TRUTH, resolution

TODAY = dt.date(2026, 10, 23)
KEY = b"synthetic-test-key"
BBL = "2027540005"
BASE = "2027540001"  # synthetic condo base lot in the same block
SR, FP = "erm2-nwe9", "5zhs-2jue"
NYPD_TEMPLATE = (
    "The New York City Police Department responded to the complaint and a report was "
    "prepared as part of their investigation. Thank you for attention to this matter. We "
    "count on New Yorkers like yourself to maintain a safe City, so please let us know if "
    "you see other conditions that require our attention."
)


def sr_row(n: int, **overrides) -> dict:
    """A synthetic 311 row shaped like erm2-nwe9 (field names from X-SODA2-Fields)."""
    base = {
        "unique_key": str(60_000_000 + n),
        "created_date": "2026-03-05T14:22:31.000",
        "closed_date": "2026-03-07T09:10:00.000",
        "agency": "NYPD",
        "agency_name": "New York City Police Department",
        "complaint_type": "Noise - Residential",
        "descriptor": "Loud Music/Party",
        "location_type": "Residential Building/House",
        "incident_zip": "10460",
        "incident_address": "1 SYNTHETIC ROAD",
        "street_name": "SYNTHETIC ROAD",
        "cross_street_1": "FIRST AVENUE",
        "cross_street_2": "SECOND AVENUE",
        "landmark": "SYNTHETIC PARK",
        "status": "Closed",
        "resolution_description": NYPD_TEMPLATE,
        "resolution_action_updated_date": "2026-03-07T09:10:00.000",
        "bbl": BBL,
        "latitude": "40.8371",
        "longitude": "-73.8810",
        "location": {"latitude": "40.8371", "longitude": "-73.8810"},
        "open_data_channel_type": "PHONE",
        "due_date": "2026-03-12T14:22:31.000",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}  # Socrata omits nulls


def fp_row(bin_: str, base: str = BBL, mappluto: str | None = None) -> dict:
    return {"bin": bin_, "base_bbl": base, "mappluto_bbl": mappluto or base, ":id": f"fp-{bin_}"}


class Fake311:
    """A synthetic Socrata server for Footprints and 311, scripted per call kind.

    Kinds: fp (any Footprints call), count (main 311 count), data (311 page), excluded
    (grouped HPD/DOB count), null (NULL-agency count). Lists are consumed per call; the
    last element repeats.
    """

    def __init__(self, rows, fp=None):
        self.rows = rows
        self.fp = fp if fp is not None else [fp_row("2006185")]
        self.calls: list[httpx.Request] = []
        self.n = {k: 0 for k in ("fp", "count", "data", "excluded", "null")}
        self.truth = {k: [TRUTH] for k in self.n}
        self.count_values: list[int | None] = [None]  # None = len(rows)
        self.data_rows: list[list[dict] | None] = [None]  # None = self.rows
        self.excluded = [{"agency": "DOB", "n": "1"}, {"agency": "HPD", "n": "3"}]
        self.null = [{"n": "0"}]
        self.fail: dict[str, set[int]] = {k: set() for k in self.n}  # kind -> call numbers

    @staticmethod
    def _at(seq, i):
        return seq[min(i, len(seq) - 1)]

    def kind(self, request) -> str:
        p = request.url.params
        if FP in request.url.path:
            return "fp"
        if "$group" in p:
            return "excluded"
        if "count(" in p.get("$select", ""):
            return "null" if "IS NULL" in p["$where"] else "count"
        return "data"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        kind = self.kind(request)
        i = self.n[kind]
        self.n[kind] += 1
        if i in self.fail[kind]:
            return httpx.Response(500, text="synthetic failure")
        truth = self._at(self.truth[kind], i)
        headers = {"x-soda2-truth-last-modified": truth} if truth else {}
        p = request.url.params
        if kind == "fp":
            lot = re.search(r"base_bbl='(\d+)'", p["$where"]).group(1)
            fp = [r for r in self.fp if lot in (r["base_bbl"], r["mappluto_bbl"])]
            if "count(" in p.get("$select", ""):
                return httpx.Response(200, json=[{"n": str(len(fp))}], headers=headers)
            off, lim = int(p["$offset"]), int(p["$limit"])
            return httpx.Response(200, json=fp[off : off + lim], headers=headers)
        if kind == "excluded":
            return httpx.Response(200, json=self.excluded, headers=headers)
        if kind == "null":
            return httpx.Response(200, json=self.null, headers=headers)
        if kind == "count":
            n = self._at(self.count_values, i)
            return httpx.Response(
                200, json=[{"n": str(len(self.rows) if n is None else n)}], headers=headers
            )
        rows = self._at(self.data_rows, i)
        rows = self.rows if rows is None else rows
        off, lim = int(p["$offset"]), int(p["$limit"])
        return httpx.Response(200, content=json.dumps(rows[off : off + lim]), headers=headers)

    def client(self):
        settings = Settings(_env_file=None, http_max_attempts=1)
        c = socrata_client(settings, transport=httpx.MockTransport(self.handler))
        c._sleep = lambda _s: None
        return c

    def urls(self, kind=None) -> list[str]:
        return [str(c.url) for c in self.calls if kind is None or self.kind(c) == kind]


TAX_LOT = dict(bbl="2029930031", bin_=None, level=ResolutionLevel.TAX_LOT)


def retrieve(rows=None, *, fake=None, res=None, **kw):
    fake = fake or Fake311(rows if rows is not None else [sr_row(1)])
    kw.setdefault("digest_key", KEY)
    kw.setdefault("page_size", 1000)
    with fake.client() as c:
        result = retrieve_service_requests(res or resolution(), c, today=TODAY, **kw)
    return result, fake


def one(row, **kw):
    result, _ = retrieve([row], **kw)
    assert len(result.records) == 1, result.status_detail
    return result.records[0]


def second(row, **kw):
    """`row` beside a normal row (a field missing from every row is a schema change)."""
    result, _ = retrieve([sr_row(9), row], **kw)
    assert len(result.records) == 2, result.status_detail
    return result.records[1]


# ---- query and lot family ---------------------------------------------------------------------


def test_query_is_by_bbl_window_and_agency_only():
    result, fake = retrieve()
    where = (
        f"bbl in ('{BBL}') AND created_date >= '2020-01-01T00:00:00' "
        "AND agency not in ('HPD', 'DOB')"
    )
    assert result.provenance.where == where == build_where([BBL])
    data = [c for c in fake.calls if fake.kind(c) == "data"]
    assert [dict(c.url.params) for c in data] == [
        {"$where": where, "$order": "unique_key", "$limit": "1", "$offset": "0"}
    ]  # no $select, so no :id; one response for a one-page lot
    for url in fake.urls():
        assert "60000001" not in url
    for url in fake.urls("count") + fake.urls("data") + fake.urls("excluded") + fake.urls("null"):
        assert "%3Aid" not in url and ":id" not in url
        assert "bin" not in url.split("?", 1)[1].replace("bbl", "")


def test_condo_lot_family_from_footprints_lot_lookup_only():
    fake = Fake311(
        [sr_row(1), sr_row(2, bbl=BASE)], fp=[fp_row("2006185", base=BASE, mappluto=BBL)]
    )
    result, fake = retrieve(fake=fake)
    assert result.lot_family == [BASE, BBL]
    assert result.provenance.where.startswith(f"bbl in ('{BASE}', '{BBL}')")
    fp_wheres = {c.url.params["$where"] for c in fake.calls if fake.kind(c) == "fp"}
    assert fp_wheres == {f"base_bbl='{BBL}' OR mappluto_bbl='{BBL}'"}  # never by BIN
    base = next(r for r in result.records if r.source_bbl == BASE)
    assert QualityFlag.CONDO_LOT_FAMILY in base.quality_flags
    assert base.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert {c.url.path for c in fake.calls} == {f"/resource/{FP}.json", f"/resource/{SR}.json"}


def test_footprints_values_that_are_not_bbls_never_reach_a_query():
    fake = Fake311([sr_row(1)], fp=[fp_row("2006185", base="1' OR '1'='1", mappluto=BBL)])
    result, _ = retrieve(fake=fake)
    assert result.lot_family == [BBL]
    assert any("lot_family_value_ignored" in w for w in result.provenance.warnings)
    with pytest.raises(ValueError):
        build_where(["1' OR '1'='1"])


def test_footprints_outage_makes_the_result_incomplete():
    fake = Fake311([sr_row(1)])
    fake.fail["fp"] = {0}
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert "lot_family_incomplete: footprints unavailable" in result.provenance.incomplete_reasons
    assert len(result.records) == 1


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unresolved_addresses_send_no_request(outcome):
    result, fake = retrieve(res=resolution(outcome=outcome))
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN and fake.calls == []


# ---- complete retrievals and exclusion counts (D19) -------------------------------------------


def test_complete_one_response_retrieval_establishes_the_excluded_counts():
    result, _ = retrieve([sr_row(1), sr_row(2)])
    assert result.status is RetrievalStatus.RECORDS_FOUND and not result.partial
    assert result.accepted_data_version == TRUTH
    assert [(a.count_header, a.page_headers, a.accepted) for a in result.attempts] == [
        (TRUTH, [TRUTH], True)
    ]
    ex = result.excluded_counts
    assert ex.by_agency.established and ex.by_agency.counts == {"DOB": 1, "HPD": 3}
    assert ex.by_agency.query_where == excluded_where([BBL])
    assert ex.by_agency.truth_header == TRUTH and ex.by_agency.fetched_at
    assert ex.null_agency.established and ex.null_agency.counts == {"null_agency": 0}
    assert ex.null_agency.query_where == null_agency_where([BBL])
    # Excluded rows are never records and never added to the totals.
    assert result.summary.total_requests == 2
    assert all(r.agency not in ("HPD", "DOB") for r in result.records)


def test_zero_rows_complete_is_no_records_found_with_counts_established():
    result, _ = retrieve([])
    assert result.status is RetrievalStatus.NO_RECORDS_FOUND
    assert result.excluded_counts.by_agency.established


def test_grouped_count_without_a_row_for_an_agency_means_zero_for_it():
    fake = Fake311([sr_row(1)])
    fake.excluded = [{"agency": "HPD", "n": "7"}]
    result, _ = retrieve(fake=fake)
    assert result.excluded_counts.by_agency.counts == {"HPD": 7, "DOB": 0}


@pytest.mark.parametrize(
    "setup, reason",
    [
        (lambda f: f.fail["excluded"].add(0), "count query failed: SourceServerError"),
        (lambda f: setattr(f, "excluded", [{"agency": "NYPD", "n": "1"}]), "bad_count_response"),
        (lambda f: setattr(f, "excluded", [{"agency": "HPD", "n": "-1"}]), "bad_count_response"),
        (lambda f: setattr(f, "excluded", {"n": 1}), "bad_count_response"),
        (lambda f: f.truth.__setitem__("excluded", [None]), "no data version header"),
        (lambda f: f.truth.__setitem__("excluded", [NEWER_TRUTH]), "data version differs"),
    ],
)
def test_excluded_counts_not_established_say_why(setup, reason):
    fake = Fake311([sr_row(1)])
    setup(fake)
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND  # the main fetch is unaffected
    by = result.excluded_counts.by_agency
    assert not by.established and by.counts is None and reason in by.not_established_reason


def test_excluded_counts_are_not_established_for_an_incomplete_fetch():
    fake = Fake311([sr_row(1)])
    fake.truth["data"] = [NEWER_TRUTH]
    result, _ = retrieve(fake=fake)
    by = result.excluded_counts.by_agency
    assert by.ok and by.counts_observed == {"DOB": 1, "HPD": 3}
    assert not by.established and by.not_established_reason == "main fetch incomplete"


def test_null_agency_rows_found_make_the_result_incomplete():
    fake = Fake311([sr_row(1)])
    fake.null = [{"n": "2"}]
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.provenance.incomplete_reasons == ["null_agency_rows_not_retrieved: 2"]
    assert result.excluded_counts.null_agency.counts == {"null_agency": 2}
    assert result.excluded_counts.by_agency.established  # the main fetch itself was complete


def test_null_agency_count_failure_is_a_warning_not_silence():
    fake = Fake311([sr_row(1)])
    fake.fail["null"] = {0}
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert not result.excluded_counts.null_agency.established
    assert any("null_agency_count_not_established" in w for w in result.provenance.warnings)


# ---- unstable snapshots (D27) ------------------------------------------------------------------


def _unstable(result):
    return any(
        r.startswith("source_snapshot_unstable") for r in result.provenance.incomplete_reasons
    )


def test_matching_headers_but_conflicting_counts_are_retried_then_unstable():
    fake = Fake311([sr_row(1), sr_row(2)])
    fake.count_values = [3]  # the count says 3; the data has 2
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and _unstable(result)
    assert result.provenance.restarts == 2 and len(result.attempts) == 3
    assert all(a.source_count == 3 and a.rows == 2 for a in result.attempts)
    assert len(result.records) == 2  # rows kept and marked
    assert result.accepted_data_version is None


def test_a_count_mismatch_that_clears_on_retry_is_complete():
    fake = Fake311([sr_row(1), sr_row(2)])
    fake.count_values = [3, None]
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert [a.accepted for a in result.attempts] == [False, True]
    assert result.attempts[0].reasons == ["short_page at offset 0: 2 < 3"]


def test_differing_headers_with_matching_counts_are_not_a_complete_pair():
    fake = Fake311([sr_row(1), sr_row(2)])
    fake.truth["data"] = [NEWER_TRUTH]
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and _unstable(result)
    assert all(a.source_count == a.rows == 2 for a in result.attempts)
    assert [(a.count_header, a.page_headers) for a in result.attempts] == [
        (TRUTH, [NEWER_TRUTH])
    ] * 3
    assert result.data_versions_seen == sorted([TRUTH, NEWER_TRUTH])
    assert result.provenance.data_as_of is None
    assert any(w == "data_versions_seen: 2" for w in result.provenance.warnings)


def test_alternating_replica_versions_accept_only_a_matched_pair():
    fake = Fake311([sr_row(1)])
    fake.truth["count"] = [TRUTH, NEWER_TRUTH]
    fake.truth["data"] = [NEWER_TRUTH, NEWER_TRUTH]
    result, _ = retrieve(fake=fake)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert [(a.count_header, a.page_headers, a.accepted) for a in result.attempts] == [
        (TRUTH, [NEWER_TRUTH], False),
        (NEWER_TRUTH, [NEWER_TRUTH], True),
    ]
    assert result.accepted_data_version == NEWER_TRUTH
    assert result.data_versions_seen == sorted([TRUTH, NEWER_TRUTH])
    # The grouped count came from the other version: shown, but not established.
    by = result.excluded_counts.by_agency
    assert not by.established and "data version differs" in by.not_established_reason


def test_two_unsuccessful_retries_record_all_three_attempts():
    fake = Fake311([sr_row(1)])
    fake.truth["count"] = [TRUTH, NEWER_TRUTH, TRUTH]
    fake.truth["data"] = [NEWER_TRUTH, TRUTH, NEWER_TRUTH]
    result, fake = retrieve(fake=fake)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and _unstable(result)
    assert fake.n["count"] == 3 and fake.n["data"] == 3
    assert [a.count_header for a in result.attempts] == [TRUTH, NEWER_TRUTH, TRUTH]
    assert not any(a.accepted for a in result.attempts)


def test_multi_page_version_change_is_retried_and_never_merged():
    rows = [sr_row(i) for i in range(1, 6)]
    fake = Fake311(rows)
    fake.truth["data"] = [TRUTH, NEWER_TRUTH]  # second page of every attempt changes
    fake.truth["count"] = [TRUTH]
    result, fake = retrieve(fake=fake, page_size=2)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and _unstable(result)
    assert len(result.records) <= 5
    assert len({r.request_ref for r in result.records}) == len(result.records)


def test_multi_page_consistent_retry_is_complete():
    rows = [sr_row(i) for i in range(1, 6)]
    fake = Fake311(rows)
    fake.truth["data"] = [TRUTH, NEWER_TRUTH, NEWER_TRUTH, NEWER_TRUTH, NEWER_TRUTH]
    fake.truth["count"] = [TRUTH, NEWER_TRUTH]
    result, _ = retrieve(fake=fake, page_size=2)
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 5
    assert result.attempts[-1].page_headers == [NEWER_TRUTH] * 3


def test_row_cap_is_incomplete_without_retry():
    result, fake = retrieve([sr_row(i) for i in range(1, 4)], row_cap=2)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION and not _unstable(result)
    assert result.provenance.restarts == 0 and len(result.records) == 2
    assert result.provenance.incomplete_reasons[0].startswith("row_cap_exceeded")
    assert result.summary.complete is False


def test_interrupted_page_keeps_partial_rows():
    fake = Fake311([sr_row(i) for i in range(1, 6)])
    fake.fail["data"] = {1}
    result, _ = retrieve(fake=fake, page_size=2)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert result.provenance.incomplete_reasons[0].startswith("page_failed at offset 2")
    assert len(result.records) == 2 and result.partial


# ---- duplicates, missing values, schema and outages --------------------------------------------


def test_identical_duplicate_rows_collapse_and_block_completeness():
    store = InMemoryEvidenceStore()
    result, _ = retrieve([sr_row(1), sr_row(1)], evidence=store)
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert "duplicate_unique_key_across_pages" in result.provenance.incomplete_reasons
    assert not _unstable(result)  # the more specific reason is kept
    (rec,) = result.records
    assert rec.collapsed_copies == 2
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in rec.quality_flags
    assert verify(store.get(rec.original_digest), KEY)


def test_conflicting_duplicates_are_both_kept_and_flagged():
    result, _ = retrieve([sr_row(1), sr_row(1, status="Open", closed_date=None)])
    assert len(result.records) == 2
    assert result.records[0].request_ref == result.records[1].request_ref
    assert result.records[0].original_digest != result.records[1].original_digest
    for r in result.records:
        assert QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags


@pytest.mark.parametrize("key", [None, "", "ABC", "[WITHHELD]", "12 34"])
def test_rows_with_a_missing_or_malformed_key_are_rejected_not_shown(key):
    store = InMemoryEvidenceStore()
    result, _ = retrieve([sr_row(1), sr_row(2, unique_key=key)], evidence=store)
    assert len(result.records) == 1
    assert result.rows_rejected == {"unique_key_missing_or_malformed": 1}
    assert result.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert "rows_rejected: 1" in result.provenance.incomplete_reasons
    assert len(store.items) == 1  # no evidence without a token


def test_missing_bbl_is_never_attributed_and_never_falls_back_to_the_address():
    rec = second(sr_row(1, bbl=None), res=resolution(**TAX_LOT))
    assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert rec.lot_membership is LotMembership.CONFLICTING
    assert QualityFlag.BBL_MISSING in rec.quality_flags and rec.source_bbl is None


def test_a_bbl_off_the_lot_or_malformed_is_flagged():
    rec = one(sr_row(1, bbl="2099990001"), res=resolution(**TAX_LOT))
    assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert QualityFlag.LOT_IDENTIFIER_CONFLICT in rec.quality_flags
    rec = one(sr_row(1, bbl="0"))
    assert QualityFlag.BBL_INVALID in rec.quality_flags and rec.source_bbl is None


def test_missing_agency_is_kept_as_none():
    rec = second(sr_row(1, agency=None))
    assert rec.agency is None


def test_schema_change_is_source_unavailable():
    rows = [{k: v for k, v in sr_row(1).items() if k != "complaint_type"}]
    result, _ = retrieve(rows)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE
    assert result.status_detail.startswith("schema_changed")
    assert result.records == []


def test_outage_is_source_unavailable_and_sends_no_count_queries():
    fake = Fake311([sr_row(1)])
    fake.fail["count"] = {0}
    result, fake = retrieve(fake=fake)
    assert result.status is RetrievalStatus.SOURCE_UNAVAILABLE and result.records == []
    assert result.excluded_counts is None
    assert fake.n["excluded"] == fake.n["null"] == 0


# ---- attribution ------------------------------------------------------------------------------


def test_building_scope_stays_unattributed_even_on_a_one_building_lot():
    rec = one(sr_row(1, location_type="Residential Building/House"))
    assert rec.attribution is Attribution.UNATTRIBUTED_ON_LOT
    assert rec.lot_membership is LotMembership.CONFIRMED and rec.join_scope is JoinScope.BUILDING


def test_tax_lot_scope_is_lot_level():
    result, _ = retrieve([sr_row(1, bbl="2029930031")], res=resolution(**TAX_LOT))
    (rec,) = result.records
    assert result.lot_family == ["2029930031"]
    assert rec.attribution is Attribution.LOT_LEVEL and result.resolved_bin is None
    assert not result.summary.building_specific_claims_allowed


# ---- token and evidence ------------------------------------------------------------------------


def test_token_is_the_public_reference_and_the_key_stays_private():
    store = InMemoryEvidenceStore()
    result, _ = retrieve([sr_row(1)], evidence=store)
    (rec,) = result.records
    assert rec.request_ref == k311_token("60000001", KEY) == rec.source_record_id
    assert K311_PATTERN.match(rec.request_ref) and rec.source_row_id is None
    raw = store.get(rec.original_digest)
    assert raw.source_record_id == rec.request_ref
    assert raw.original_row["unique_key"] == "60000001"
    assert raw.basis is DigestBasis.ORIGINAL_ROW and verify(raw, KEY)
    assert "60000001" not in result.model_dump_json()


def test_same_request_same_token_across_runs_and_new_token_after_rotation():
    a = one(sr_row(1)).request_ref
    assert one(sr_row(1)).request_ref == a
    assert one(sr_row(1), digest_key=b"rotated").request_ref != a


# ---- allowlists --------------------------------------------------------------------------------


def test_values_off_the_allowlist_are_withheld_and_flagged():
    rec = one(sr_row(1, complaint_type="Rare Synthetic Type", agency="ZZZ", descriptor_2="x"))
    assert rec.complaint_type == WITHHELD_VALUE and rec.agency == WITHHELD_VALUE
    assert rec.descriptor_2 == WITHHELD_VALUE
    assert {"complaint_type", "agency", "descriptor_2"} <= set(rec.withheld_fields)
    assert QualityFlag.UNCOMMON_VALUE_WITHHELD in rec.quality_flags


def test_allowlisted_values_are_shown_verbatim():
    rec = one(sr_row(1, descriptor_2="Tenant Refused Repair Request"))
    assert rec.complaint_type == "Noise - Residential" and rec.descriptor == "Loud Music/Party"
    assert rec.descriptor_2 == "Tenant Refused Repair Request"
    assert rec.resolution_description == NYPD_TEMPLATE
    assert QualityFlag.UNCOMMON_VALUE_WITHHELD not in rec.quality_flags


def test_a_private_value_is_withheld_even_if_it_reached_the_allowlist(monkeypatch):
    sr311._allow.cache_clear()
    ref = sr311.reference()
    patched = {**ref, "allowlists": {**ref["allowlists"], "descriptor": ["APT 4B LEAK"]}}
    monkeypatch.setattr(sr311, "reference", lambda: patched)
    try:
        assert public_value("descriptor", "APT 4B LEAK") == (WITHHELD_VALUE, True)
    finally:
        sr311._allow.cache_clear()


def test_resolution_text_off_the_template_list_is_withheld():
    rec = one(sr_row(1, resolution_description=NYPD_TEMPLATE + " Call MR SMITH."))
    assert rec.resolution_description == WITHHELD_VALUE
    assert "resolution_description" in rec.withheld_fields


# ---- statuses and dates ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, status, shown",
    [
        ("Open", RequestStatus.OPEN, "Open"),
        ("In Progress", RequestStatus.OPEN, "In Progress"),
        ("Assigned", RequestStatus.OPEN, "Assigned"),
        ("Pending", RequestStatus.OPEN, "Pending"),
        ("Started", RequestStatus.OPEN, "Started"),
        ("Closed", RequestStatus.CLOSED, "Closed"),
        ("Canceled", RequestStatus.CANCELED, "Canceled"),
        ("Unspecified", RequestStatus.UNKNOWN, "Unspecified"),  # measured, unmapped
        ("Resolved SENTINEL", RequestStatus.UNKNOWN, WITHHELD_VALUE),  # never as text
        (None, RequestStatus.UNKNOWN, None),
    ],
)
def test_status_mapping(raw, status, shown):
    got, raw_shown, _ = classify_status(raw)
    assert (got, raw_shown) == (status, shown)


def test_unknown_status_is_flagged_and_withheld():
    rec = one(sr_row(1, status="Resolved SENTINEL"))
    assert rec.request_status is RequestStatus.UNKNOWN and rec.status_raw == WITHHELD_VALUE
    assert QualityFlag.STATUS_UNKNOWN in rec.quality_flags and "status" in rec.withheld_fields


def test_open_with_a_closed_date_disagrees():
    rec = one(sr_row(1, status="In Progress"))
    assert QualityFlag.STATUS_FIELDS_DISAGREE in rec.quality_flags


def test_closed_without_a_closed_date_disagrees_and_is_not_a_repair():
    result, _ = retrieve([sr_row(1, closed_date=None)])
    (rec,) = result.records
    assert rec.request_status is RequestStatus.CLOSED
    assert QualityFlag.STATUS_FIELDS_DISAGREE in rec.quality_flags
    assert result.summary.closed_without_closed_date == 1
    assert "does not show that the condition was corrected" in result.summary.statement
    assert "repaired" not in rec.model_dump_json()


def test_status_and_the_two_dates_stay_separate():
    rec = one(
        sr_row(
            1,
            status="Pending",
            closed_date=None,
            resolution_action_updated_date="2026-04-01T08:00:00.000",
        )
    )
    assert rec.request_status is RequestStatus.OPEN and rec.closed_date.raw is None
    assert rec.agency_updated_date.raw == "2026-04-01"
    assert QualityFlag.STATUS_FIELDS_DISAGREE not in rec.quality_flags


@pytest.mark.parametrize(
    "raw, out, flag",
    [
        ("2026-03-05T14:22:31.000", "2026-03-05", None),
        ("2026-03-05T14:22:31", "2026-03-05", None),
        ("2026-03-05", "2026-03-05", None),
        (None, None, None),
        ("", None, None),
        ("05/03/2026 SENTINEL", None, QualityFlag.UNPARSEABLE_DATE),
        ("2026-02-30T00:00:00.000", None, QualityFlag.UNPARSEABLE_DATE),
        ("2027-01-01T00:00:00.000", "2027-01-01", QualityFlag.IMPLAUSIBLE_DATE),
        ("1899-12-31T00:00:00.000", "1899-12-31", QualityFlag.IMPLAUSIBLE_DATE),
    ],
)
def test_dates_are_date_only(raw, out, flag):
    value, got = parse_date(raw, as_of=TODAY)
    assert value.raw == out and got == flag


def test_closed_before_created_is_date_order():
    rec = one(sr_row(1, closed_date="2026-03-01T00:00:00.000"))
    assert QualityFlag.DATE_ORDER in rec.quality_flags


def test_no_timestamp_reaches_a_record():
    result, _ = retrieve([sr_row(1)])
    blob = result.model_dump_json()
    assert "14:22" not in blob and "09:10" not in blob and 'due_date":' not in blob


# ---- summary ------------------------------------------------------------------------------------


def test_summary_by_agency_type_status_and_windows():
    rows = [
        sr_row(1),
        sr_row(2, created_date="2025-01-10T00:00:00.000", closed_date="2025-01-11T00:00:00.000"),
        sr_row(
            3,
            agency="DSNY",
            complaint_type="Request Large Bulky Item Collection",
            descriptor="Request Large Bulky Item Collection",
            status="Open",
            closed_date=None,
            created_date="2021-06-01T00:00:00.000",
        ),
    ]
    result, _ = retrieve(rows)
    s = result.summary
    assert s.complete and s.building_specific_claims_allowed is False
    b = s.by_attribution[Attribution.UNATTRIBUTED_ON_LOT]
    assert b.requests == 3
    assert b.by_agency == {"NYPD": 2, "DSNY": 1}
    assert b.by_status == {"closed": 2, "open": 1}
    assert b.by_agency_type_status["NYPD|Noise - Residential|closed"] == 2
    assert b.received_last_12_months == 1 and b.received_last_36_months == 2


def test_windows_are_not_given_for_an_incomplete_fetch():
    result, _ = retrieve([sr_row(i) for i in range(1, 4)], row_cap=2)
    b = result.summary.by_attribution[Attribution.UNATTRIBUTED_ON_LOT]
    assert b.received_last_12_months is None and b.received_last_36_months is None


# ---- privacy ------------------------------------------------------------------------------------

SENTINELS = (
    "MR. JOHN SENTINEL",
    "APT 4B-SENTINEL",
    "7TH FLOOR SENTINEL",
    "sentinel@example.com",
    "212-555-0199",
    "40.83719 -73.88101",
    "INJURED SENTINEL CHILD",
)


def _sentinel_row(n: int, text: str) -> dict:
    return sr_row(
        n,
        agency=text,
        agency_name=text,
        complaint_type=text,
        descriptor=text,
        descriptor_2=text,
        location_type=text,
        resolution_description=f"{NYPD_TEMPLATE} {text}",
        status=text,
        created_date=f"2026-03-05T14:22:31.000 {text}",
        closed_date=text,
        resolution_action_updated_date=text,
        incident_address=text,
        street_name=text,
        cross_street_1=text,
        cross_street_2=text,
        intersection_street_1=text,
        intersection_street_2=text,
        landmark=text,
        incident_zip=text,
        latitude=text,
        longitude=text,
        location={"human_address": text},
        open_data_channel_type=text,
        due_date=text,
        bbl=f"{BBL}",
    )


@pytest.mark.parametrize("text", SENTINELS)
def test_sentinels_in_every_field_stay_in_private_evidence_only(text, caplog):
    store = InMemoryEvidenceStore()
    with caplog.at_level(logging.DEBUG):
        result, fake = retrieve([_sentinel_row(1, text)], evidence=store)
    public = result.model_dump_json()
    needles = {text, "SENTINEL", "sentinel", "555-0199", "40.83719"}
    for needle in needles:
        assert needle not in public
        assert needle not in caplog.text
        for c in fake.calls:
            assert needle not in str(c.url)
        for p in [result.provenance, *result.lot_family_provenance]:
            assert needle not in p.model_dump_json()
    (rec,) = result.records
    raw = store.get(rec.original_digest)
    assert raw.original_row["incident_address"] == text
    assert verify(raw, KEY) and rec.digest_basis is DigestBasis.ORIGINAL_ROW
    assert QualityFlag.UNCOMMON_VALUE_WITHHELD in rec.quality_flags
    assert {"incident_address", "latitude", "location", "due_date"} <= set(raw.redacted_fields)
    assert {"incident_address", "landmark", "open_data_channel_type"} <= set(rec.withheld_fields)


def test_log_lines_carry_no_key_or_row_values(caplog):
    with caplog.at_level(logging.DEBUG):
        retrieve([sr_row(1)])
    for needle in ("60000001", "SYNTHETIC ROAD", "40.8371", "Loud Music"):
        assert needle not in caplog.text


# ---- model invariants ---------------------------------------------------------------------------


def _record(**overrides) -> dict:
    base = dict(
        dataset_id=SR,
        request_ref=k311_token("60000001", KEY),
        source_record_id=k311_token("60000001", KEY),
        source_bbl=BBL,
        resolved_bbl=BBL,
        join_scope=JoinScope.BUILDING,
        lot_membership=LotMembership.CONFIRMED,
        attribution=Attribution.UNATTRIBUTED_ON_LOT,
        agency="NYPD",
        complaint_type=None,
        descriptor=None,
        descriptor_2=None,
        location_type=None,
        resolution_description=None,
        redaction=RedactionInfo(applied="none"),
        request_status=RequestStatus.CLOSED,
        status_raw="Closed",
        received_date=DateValue(raw="2026-03-05", value=dt.date(2026, 3, 5)),
        closed_date=DateValue(raw=None, value=None),
        agency_updated_date=DateValue(raw=None, value=None),
        withheld_fields=[],
        quality_flags=[],
        original_digest="x",
        digest_basis=DigestBasis.ORIGINAL_ROW,
        redacted_sha256="y",
        query_url="u",
        fetched_at="t",
        data_as_of=None,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"attribution": Attribution.THIS_BUILDING},
        {"attribution": Attribution.OTHER_BUILDING_ON_LOT},
        {"attribution": Attribution.LOT_LEVEL},  # at building scope
        {"request_ref": "60000001", "source_record_id": "60000001"},
        {"source_record_id": k311_token("60000002", KEY)},
        {"received_date": DateValue(raw="2026-03-05T14:22:31.000", value=None)},
        {"request_status": RequestStatus.UNKNOWN},  # without status_unknown
        {"source_row_id": "row-1"},
        {
            "join_scope": JoinScope.TAX_LOT,
            "attribution": Attribution.LOT_LEVEL,
            "lot_membership": LotMembership.CONFLICTING,
        },
    ],
)
def test_record_model_refuses_contract_violations(overrides):
    with pytest.raises(ValidationError):
        NormalizedServiceRequest(**_record(**overrides))


def test_count_check_needs_a_reason_when_not_established():
    ok = CountCheck(query_where="w", ok=True, established=True, counts={"HPD": 1})
    with pytest.raises(ValidationError):
        ExcludedCounts(by_agency=CountCheck(query_where="w"), null_agency=ok)
    with pytest.raises(ValidationError):
        ExcludedCounts(by_agency=CountCheck(query_where="w", established=True), null_agency=ok)


def _result(**overrides):
    base = dict(
        source="nyc_open_data",
        dataset_id=SR,
        status=RetrievalStatus.NO_RECORDS_FOUND,
        status_detail="d",
        join_scope=JoinScope.BUILDING,
        resolved_bbl=BBL,
        provenance=RetrievalProvenance(dataset_id=SR, where=build_where([BBL]), source_count=0),
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "where",
    [
        "unique_key = '60000001'",
        "bbl in ('2027540005') AND :id = 'row-1'",
        "bin = '2006185'",
    ],
)
def test_result_refuses_a_query_by_key_row_id_or_bin(where):
    prov = RetrievalProvenance(dataset_id=SR, where=where, source_count=0)
    with pytest.raises(ValidationError):
        ServiceRequestResult(**_result(provenance=prov))


def test_result_refuses_established_counts_for_an_incomplete_fetch():
    est = CountCheck(query_where="w", ok=True, truth_header=TRUTH, established=True, counts={})
    ex = ExcludedCounts(by_agency=est, null_agency=est)
    prov = RetrievalProvenance(
        dataset_id=SR, where=build_where([BBL]), incomplete_reasons=["short_page at offset 0"]
    )
    with pytest.raises(ValidationError):
        ServiceRequestResult(
            **_result(
                status=RetrievalStatus.INCOMPLETE_PAGINATION,
                partial=True,
                provenance=prov,
                excluded_counts=ex,
            )
        )
    with pytest.raises(ValidationError):  # complete, but no accepted version to match
        ServiceRequestResult(**_result(excluded_counts=ex))
    ServiceRequestResult(**_result(excluded_counts=ex, accepted_data_version=TRUTH))


def test_result_refuses_unaccounted_rows():
    rec = NormalizedServiceRequest(**_record())
    prov = RetrievalProvenance(dataset_id=SR, where=build_where([BBL]), source_count=2)
    with pytest.raises(ValidationError):
        ServiceRequestResult(
            **_result(status=RetrievalStatus.RECORDS_FOUND, records=[rec], provenance=prov)
        )


def test_reference_mapping_text_never_calls_closed_resolved():
    assert re.search(
        r"does not show that a condition was corrected", sr311.reference()["status_note"]
    )
