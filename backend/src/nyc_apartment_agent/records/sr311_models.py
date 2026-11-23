"""Typed results for 311 Service Requests from 2020 to Present (erm2-nwe9), Task 5 step 4.

Distinctions built into the types (design sections 4, 6.4, 18.1, 18.2 and 18.4):

1. A 311 request is a report to the city (`evidence_kind = reported_allegation`), never
   a confirmed violation. A closed request does not show that a condition was fixed.
2. 311 rows carry a BBL and no BIN, and the BBL is geocoded from what the submitter
   typed. A request is therefore never attributed to a building: `unattributed_on_lot` at
   building scope, `lot_level` at tax-lot scope. The types refuse anything else.
3. The source `unique_key` never leaves private evidence. The public reference is the
   keyed `k311_` token, which is also the record's `source_record_id`. There is no
   row-level lineage and no Socrata `:id`.
4. HPD- and DOB-routed requests are not retrieved. Their numbers come from a separate
   count query and are "established" only when that count and the main fetch describe
   the same complete snapshot; otherwise they are shown as not established, with why.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .models import (
    Attribution,
    AttributionBasis,
    DateValue,
    DigestBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
    check_source_invariants,
)
from .redaction import K311_PATTERN

WITHHELD_VALUE = "[UNCOMMON VALUE WITHHELD]"
NULL_AGENCY_REASON = "null_agency_rows_not_retrieved"


class RequestStatus(StrEnum):
    OPEN = "open"  # Open, In Progress, Assigned, Pending, Started
    CLOSED = "closed"  # the agency closed the request; not proof of a repair
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class NormalizedServiceRequest(BaseModel):
    """One 311 request, ready for the public report. Dates only, no location detail."""

    dataset_id: str
    evidence_kind: Literal[EvidenceKind.REPORTED_ALLEGATION] = EvidenceKind.REPORTED_ALLEGATION
    request_ref: str  # k311_ token, never the unique_key (D20)
    source_record_id: str  # the same token (single-record lineage, 18.1)
    source_row_id: None = None  # no row-level lineage and no Socrata :id for 311
    collapsed_copies: int = 1  # identical source copies behind this record

    source_bbl: str | None
    resolved_bbl: str
    join_scope: JoinScope
    lot_membership: LotMembership
    attribution: Literal[Attribution.UNATTRIBUTED_ON_LOT, Attribution.LOT_LEVEL]
    attribution_basis: Literal[AttributionBasis.ROW_IDENTIFIERS] = AttributionBasis.ROW_IDENTIFIERS

    agency: str | None  # allowlisted code, or the withheld marker
    complaint_type: str | None
    descriptor: str | None
    descriptor_2: str | None
    location_type: str | None
    resolution_description: str | None  # allowlisted template, unit/floor redacted
    redaction: RedactionInfo

    request_status: RequestStatus
    status_raw: str | None  # the measured value, or the withheld marker
    received_date: DateValue  # created_date, date part only
    closed_date: DateValue
    agency_updated_date: DateValue  # resolution_action_updated_date, date part only

    withheld_fields: list[str]  # names of source fields not shown for this row
    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None

    @model_validator(mode="after")
    def _consistent(self) -> NormalizedServiceRequest:
        if not K311_PATTERN.match(self.request_ref):
            raise ValueError("request_ref must be a k311_ token")
        if self.source_record_id != self.request_ref:
            raise ValueError("source_record_id must be the request token")
        if self.join_scope is JoinScope.BUILDING and self.attribution is Attribution.LOT_LEVEL:
            raise ValueError("at building scope a 311 request stays unattributed_on_lot")
        if (
            self.join_scope is JoinScope.TAX_LOT
            and self.attribution is Attribution.LOT_LEVEL
            and self.lot_membership is not LotMembership.CONFIRMED
        ):
            raise ValueError("lot_level needs the row's BBL on the lot")
        for d in (self.received_date, self.closed_date, self.agency_updated_date):
            if d.raw is not None and len(d.raw) != 10:
                raise ValueError("public 311 dates carry the date only")
        if self.request_status is RequestStatus.UNKNOWN and (
            QualityFlag.STATUS_UNKNOWN not in self.quality_flags
        ):
            raise ValueError("an unknown status must be flagged status_unknown")
        return self


class CountCheck(BaseModel):
    """A separate count query, with what it does and does not establish."""

    query_where: str
    truth_header: str | None = None
    fetched_at: str | None = None
    ok: bool = False
    error: str | None = None
    counts_observed: dict[str, int] | None = None  # as returned; may not match the fetch
    established: bool = False
    counts: dict[str, int] | None = None  # only when established
    not_established_reason: str | None = None


class ExcludedCounts(BaseModel):
    """HPD- and DOB-routed requests on the lot: source counts, never retrieved rows."""

    agencies: list[str] = Field(default_factory=lambda: ["DOB", "HPD"])
    by_agency: CountCheck
    null_agency: CountCheck  # rows the main filter cannot return (agency IS NULL)
    note: str = (
        "Source counts from separate queries, not retrieved rows. HPD- and DOB-routed "
        "requests are covered by those agencies' own datasets and are not added to the 311 "
        "records. A count is established only when its query succeeded, the main fetch is "
        "complete, and both responses carry the same data version."
    )

    @model_validator(mode="after")
    def _consistent(self) -> ExcludedCounts:
        for c in (self.by_agency, self.null_agency):
            if c.established != (c.counts is not None):
                raise ValueError("counts are given only when established")
            if not c.established and not c.not_established_reason:
                raise ValueError("a count that is not established must say why")
        return self


class Attempt(BaseModel):
    """One count-then-data attempt, accepted or rejected (D27, 18.4)."""

    attempt: int
    count_header: str | None
    page_headers: list[str | None]
    source_count: int | None
    rows: int
    reasons: list[str]
    accepted: bool


class Sr311Bucket(BaseModel):
    requests: int = 0
    by_agency: dict[str, int] = Field(default_factory=dict)
    by_complaint_type: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    # "agency|complaint_type|status" -> count
    by_agency_type_status: dict[str, int] = Field(default_factory=dict)
    # None when the window is not supportable: an incomplete fetch, or a window that
    # starts before the 2020 floor of the dataset.
    received_last_12_months: int | None = None
    received_last_36_months: int | None = None


class Sr311Summary(BaseModel):
    data_as_of: str | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: Literal[False] = False  # never, for 311 (D29)
    total_requests: int
    source_rows: int
    complete: bool  # windows and totals cover the whole source result only when true
    by_attribution: dict[Attribution, Sr311Bucket]
    status_fields_disagree: int
    open_with_closed_date: int
    closed_without_closed_date: int
    withheld_value_counts: dict[str, int]  # field -> rows with a withheld value
    quality_flag_counts: dict[str, int]
    statement: str


class ServiceRequestResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: None = None  # 311 has no BIN; never a BIN join
    records: list[NormalizedServiceRequest] = Field(default_factory=list)
    partial: bool = False
    summary: Sr311Summary | None = None
    provenance: RetrievalProvenance  # the accepted (or last) attempt
    attempts: list[Attempt] = Field(default_factory=list)  # every attempt, incl. rejected
    data_versions_seen: list[str] = Field(default_factory=list)  # every truth header seen
    accepted_data_version: str | None = None  # header of the accepted pair, if any
    rows_rejected: dict[str, int] = Field(default_factory=dict)  # reason -> rows not shown
    excluded_counts: ExcludedCounts | None = None
    lot_family: list[str] = Field(default_factory=list)
    lot_family_provenance: list[RetrievalProvenance] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> ServiceRequestResult:
        check_source_invariants(self, [])
        for r in self.records:
            if r.attribution not in (Attribution.UNATTRIBUTED_ON_LOT, Attribution.LOT_LEVEL):
                raise ValueError("a 311 request is never attributed to a building")
            if r.join_scope is not self.join_scope:
                raise ValueError("record scope differs from the result scope")
        where = self.provenance.where or ""
        if re.search(r"unique_key|:id|\bbin\b", where):
            raise ValueError("311 is queried by BBL only; no key, row id or BIN in a query")
        if self.status is RetrievalStatus.RECORDS_FOUND:
            copies = sum(r.collapsed_copies for r in self.records)
            if copies != self.provenance.source_count or self.rows_rejected:
                raise ValueError("every fetched source row must be accounted for")
        if self.excluded_counts is not None and self.excluded_counts.by_agency.established:
            if self.provenance.incomplete_reasons or self.status not in (
                RetrievalStatus.RECORDS_FOUND,
                RetrievalStatus.NO_RECORDS_FOUND,
            ):
                raise ValueError("excluded counts are established only for a complete result")
            if self.accepted_data_version is None or (
                self.excluded_counts.by_agency.truth_header != self.accepted_data_version
            ):
                raise ValueError("excluded counts need the same data version as the fetch")
        reasons = self.provenance.incomplete_reasons
        if self.accepted_data_version is not None and not all(
            r.startswith(NULL_AGENCY_REASON) for r in reasons
        ):
            raise ValueError("an incomplete fetch has no accepted data version")
        return self
