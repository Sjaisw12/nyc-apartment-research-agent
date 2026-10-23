"""Typed results for DOB Complaints Received (eabe-havv), Phase 2 Task 5 step 2.

Distinctions built into the types (design sections 4, 5, 18.3 and 18.6):

1. A DOB complaint is a report to DOB (`evidence_kind = reported_allegation`). What DOB
   recorded afterwards (the disposition) is kept as its own fields: DOB's code, DOB's
   own text verbatim, and our documented outcome grouping.
2. The complaint `status` (ACTIVE / CLOSED) and the disposition outcome are separate
   fields. Neither is derived from the other; when they disagree both are kept and the
   record is flagged.
3. An order event is a historical record that DOB wrote a stop-work, vacate or padlock
   disposition. It never says that an order is in effect (`still_in_effect` can only be
   "not_shown_by_source"), and its action comes from DOB's own verb.
4. A code with no official definition stays `unknown_code` with no description.
5. DOB complaints carry only a BIN. Building attribution therefore rests on the BIN's
   place on the lot (resolver, Footprints, HPD), recorded per BIN with its sources.
"""

from __future__ import annotations

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


class DobComplaintStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class DobComplaintOutcome(StrEnum):
    """Our grouping of DOB disposition codes (design 18.6, approved 2026-10-23)."""

    VIOLATION_SERVED = "violation_served"
    VIOLATION_ISSUED = "violation_issued"
    VIOLATION_PREPARED_NOT_SERVED = "violation_prepared_not_served"
    CORRECTIVE_ACTION_REQUESTED = "corrective_action_requested"
    CORRECTIVE_ACTION_CORRECTED = "corrective_action_corrected"
    CORRECTIVE_ACTION_NOT_CORRECTED = "corrective_action_not_corrected"
    ORDER_EVENT = "order_event"
    NO_VIOLATION = "no_violation"
    INSPECTION_COMPLETED = "inspection_completed"
    INSPECTION_PASSED = "inspection_passed"
    INSPECTION_FAILED = "inspection_failed"
    INSPECTION_FINDINGS_UNDER_REVIEW = "inspection_findings_under_review"
    UNSUBSTANTIATED = "unsubstantiated"
    NO_ACCESS = "no_access"
    REFERRED = "referred"
    ASSIGNED_OR_PENDING = "assigned_or_pending"
    OTHER_RECORDED = "other_recorded"
    UNKNOWN_CODE = "unknown_code"  # no official definition found; never guessed
    NONE = "none"  # the row has no disposition code


class OrderEventKind(StrEnum):
    """What kind of order DOB's disposition is about. A label contains "issued" only when
    DOB's text says served, issued or previously issued."""

    STOP_WORK_FULL_ISSUED = "stop_work_full_issued"
    STOP_WORK_PARTIAL_ISSUED = "stop_work_partial_issued"
    STOP_WORK_PARTIAL_RECORDED = "stop_work_partial_recorded"
    STOP_WORK_CRANE_NO_ADDRESS_RECORDED = "stop_work_crane_no_address_recorded"
    STOP_WORK_RESCINDED_FULL = "stop_work_rescinded_full"
    STOP_WORK_RESCINDED_PARTIAL = "stop_work_rescinded_partial"
    STOP_WORK_NONCOMPLIANCE = "stop_work_noncompliance"
    STOP_ALL_WORK_NO_TCAO = "stop_all_work_no_tcao"
    VACATE_FULL_ISSUED = "vacate_full_issued"
    VACATE_PARTIAL_ISSUED = "vacate_partial_issued"
    VACATE_RESCINDED_FULL = "vacate_rescinded_full"
    VACATE_RESCINDED_PARTIAL = "vacate_rescinded_partial"
    VACATE_PREVIOUSLY_ISSUED = "vacate_previously_issued"
    VACATE_NONCOMPLIANCE = "vacate_noncompliance"
    PADLOCK_ISSUED = "padlock_issued"
    PADLOCK_RESCINDED = "padlock_rescinded"


class OrderAction(StrEnum):
    """The verb in DOB's own text for this code."""

    SERVED = "served"
    ISSUED = "issued"
    RESCINDED = "rescinded"
    PREVIOUSLY_ISSUED = "previously_issued"
    NONCOMPLIANCE_VIOLATION = "noncompliance_violation"  # a violation for disobeying it
    NOT_STATED = "not_stated"  # DOB's text has no verb (e.g. L1, K4, H5)


class BinSource(StrEnum):
    RESOLVER = "resolver"  # Phase 1 verified this BIN for the address
    FOOTPRINTS = "footprints"  # Footprints base_bbl or mappluto_bbl is the resolved lot
    HPD = "hpd"  # a current HPD building record on the resolved lot has this BIN


class OrderEvent(BaseModel):
    kind: OrderEventKind
    action: OrderAction
    disposition_code: str
    dob_text: str  # DOB's definition of the code, verbatim
    disposition_date: DateValue | None
    complaint_number: str
    still_in_effect: Literal["not_shown_by_source"] = "not_shown_by_source"
    statement: str  # fixed wording; never claims the order is in effect


class BinEvidence(BaseModel):
    """Where one BIN's place on the resolved lot comes from (design 18.3)."""

    bin: str
    sources: list[BinSource]
    is_resolved_bin: bool
    searched: bool
    # Lots that Footprints gives for this BIN when looked up by BIN (only for BINs that
    # Footprints does not place on the lot family); empty when not looked up.
    footprints_lots_for_bin: list[str] = Field(default_factory=list)
    flags: list[QualityFlag] = Field(default_factory=list)
    note: str | None = None


class NormalizedDobComplaint(BaseModel):
    """One DOB complaint, ready for the public report. `dobrundate` is not public."""

    dataset_id: str
    evidence_kind: Literal[EvidenceKind.REPORTED_ALLEGATION] = EvidenceKind.REPORTED_ALLEGATION
    complaint_number: str  # DOB's own public identifier
    source_row_ids: list[str]  # Socrata :id of every source row behind this record
    source_bin: str | None
    bin_sources: list[BinSource]  # where this BIN's place on the lot comes from
    resolved_bbl: str
    lot_membership: LotMembership
    attribution: Attribution
    attribution_basis: AttributionBasis

    complaint_category: str | None
    complaint_category_description: str | None  # DOB's list (Rev. 12/18); None if absent
    complaint_category_priority: str | None
    complaint_status: DobComplaintStatus
    status_raw: str | None
    disposition_code: str | None
    disposition_description: str | None  # official text verbatim; None if unknown
    disposition_definition_source: str | None  # dob_pdf_2021 / ref_6v9u_ndjg_2017
    outcome: DobComplaintOutcome
    order_event: OrderEvent | None

    received_date: DateValue
    inspection_date: DateValue
    disposition_date: DateValue
    dob_unit: str | None  # the DOB unit that handled it, not an apartment
    community_board: str | None
    recorded_address: str | None  # DOB's house number and street, after unit redaction
    recorded_zip_code: str | None
    redaction: RedactionInfo

    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None

    @model_validator(mode="after")
    def _consistent(self) -> NormalizedDobComplaint:
        unknown = self.outcome is DobComplaintOutcome.UNKNOWN_CODE
        if unknown and self.disposition_description is not None:
            raise ValueError("an unknown disposition code must not carry a description")
        if unknown != (QualityFlag.DISPOSITION_CODE_UNKNOWN in self.quality_flags):
            raise ValueError("unknown_code and disposition_code_unknown must go together")
        if (self.outcome is DobComplaintOutcome.ORDER_EVENT) and self.order_event is None:
            raise ValueError("an order_event outcome needs an order event")
        if self.attribution in (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT):
            if self.lot_membership is not LotMembership.VIA_BIN_MAPPING:
                raise ValueError("building attribution needs the BIN's lot membership")
            if QualityFlag.BIN_LOT_CONFLICT in self.quality_flags:
                raise ValueError("a BIN-lot conflict never yields building attribution")
            if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in self.quality_flags:
                raise ValueError("conflicting duplicate copies never keep building attribution")
        if self.attribution is Attribution.THIS_BUILDING and (
            self.attribution_basis is not AttributionBasis.ROW_IDENTIFIERS
        ):
            raise ValueError("this_building comes from the row's own BIN")
        if self.attribution is Attribution.OTHER_BUILDING_ON_LOT and (
            self.attribution_basis is not AttributionBasis.BIN_LOT_MAPPING
        ):
            raise ValueError("other_building_on_lot rests on the BIN-lot mapping")
        return self


class AttributionBucket(BaseModel):
    complaints: int = 0
    active: int = 0
    closed: int = 0
    status_unknown: int = 0
    by_outcome: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_bin: dict[str, int] = Field(default_factory=dict)
    received_last_12_months: int = 0
    received_last_36_months: int = 0


class DobComplaintSummary(BaseModel):
    data_as_of: str | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    total_complaints: int
    source_rows: int
    by_attribution: dict[Attribution, AttributionBucket]
    order_events: list[OrderEvent]  # in date order; issues and rescissions never netted
    status_fields_disagree: int
    active_with_final_disposition: int
    closed_with_no_disposition: int
    unknown_disposition_codes: dict[str, int]
    unknown_category_codes: dict[str, int]
    quality_flag_counts: dict[str, int]
    conflict_counts: dict[str, int]


DOB_CONFLICT_FLAGS = frozenset(
    {
        QualityFlag.STATUS_FIELDS_DISAGREE,
        QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT,
        QualityFlag.BIN_LOT_CONFLICT,
    }
)


class DobComplaintResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[NormalizedDobComplaint] = Field(default_factory=list)
    partial: bool = False
    summary: DobComplaintSummary | None = None
    provenance: RetrievalProvenance  # the DOB complaints fetch
    search_bins: list[str] = Field(default_factory=list)
    bin_evidence: list[BinEvidence] = Field(default_factory=list)
    lot_family: list[str] = Field(default_factory=list)
    bin_lot_conflicts: list[str] = Field(default_factory=list)
    # Footprints (by lot, and by BIN when needed) and HPD building lookups.
    bin_evidence_provenance: list[RetrievalProvenance] = Field(default_factory=list)
    bin_evidence_complete: bool = False
    coverage_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> DobComplaintResult:
        building_only = (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT)
        check_source_invariants(
            self, [r.complaint_number for r in self.records if r.attribution in building_only]
        )
        if self.join_scope is not JoinScope.BUILDING and (self.search_bins or self.records):
            raise ValueError("DOB complaints are searched by BIN only at building scope")
        searched = set(self.search_bins)
        if set(self.bin_lot_conflicts) & searched - {self.resolved_bin}:
            raise ValueError("a BIN with conflicting lot evidence must not be searched")
        for r in self.records:
            if r.attribution is Attribution.THIS_BUILDING and r.source_bin != self.resolved_bin:
                raise ValueError(f"{r.complaint_number}: this_building needs the resolved BIN")
            if (
                r.attribution is Attribution.OTHER_BUILDING_ON_LOT
                and r.source_bin == self.resolved_bin
            ):
                raise ValueError(f"{r.complaint_number}: resolved BIN counted as another building")
        return self
