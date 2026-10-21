"""Typed results for HPD complaints and problems (Phase 2, Task 2).

Three distinctions are built into the types so later code cannot blur them:

1. A complaint is a tenant's report (`evidence_kind = reported_allegation`). What HPD
   later wrote about it (`hpd_disposition`) is HPD's account of its follow-up, not a
   finding about a specific violation: no key links complaints to violation records.
2. Complaints and problems are counted separately. One complaint holds one or more
   problem rows; summaries never add problems up as complaints.
3. A complaint whose problems fall in different attribution buckets is `mixed`, and is
   never counted as belonging to the requested building.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from .models import (
    Attribution,
    DateValue,
    EvidenceKind,
    JoinScope,
    LocationScope,
    LotMembership,
    PrivacySummary,
    QualityFlag,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
    check_source_invariants,
)


class ComplaintOutcome(StrEnum):
    """HPD's disposition as stated in `status_description` (templates observed in data;
    the official data dictionary describes the field only as "Status description")."""

    VIOLATIONS_ISSUED = "violations_issued"
    VIOLATIONS_PREVIOUSLY_ISSUED = "violations_previously_issued"
    NO_VIOLATION_FOUND = "no_violation_found"
    NO_ACCESS = "no_access"
    INSPECTION_NOT_COMPLETED = "inspection_not_completed"
    CORRECTED_PER_CONTACT = "corrected_per_contact"  # occupant/tenant/phone contact said so
    RESTORED_PER_CONTACT = "restored_per_contact"  # heat/hot water restored, as told to HPD
    OPEN_OWNER_NOTIFIED = "open_owner_notified"  # still open; HPD notified the owner
    DUPLICATE_OF_BUILDING_WIDE = "duplicate_of_building_wide"
    OTHER = "other"  # text present but no rule matched; sanitized text is kept
    NO_TEXT = "no_text"  # no status_description at all


class ComplaintAttribution(StrEnum):
    THIS_BUILDING = "this_building"
    OTHER_BUILDING_ON_LOT = "other_building_on_lot"
    UNATTRIBUTED_ON_LOT = "unattributed_on_lot"
    LOT_LEVEL = "lot_level"
    MIXED = "mixed"  # problems in more than one bucket: never counted as this building


class HpdDisposition(BaseModel):
    """What HPD's record says happened. Not a finding about any specific violation."""

    kind: str = "hpd_reported_disposition"
    outcome: ComplaintOutcome
    initial_complaint_outcome: ComplaintOutcome | None = None  # for duplicate reports
    text: str | None  # sanitized status_description, kept verbatim otherwise
    identifies_specific_violation: bool = False
    note: str = (
        "HPD's description of its follow-up. It does not identify which violation "
        "record, if any, resulted; complaints and violations share no key."
    )


class NormalizedComplaintProblem(BaseModel):
    dataset_id: str
    evidence_kind: EvidenceKind = EvidenceKind.REPORTED_ALLEGATION
    problem_id: str
    complaint_id: str
    building_id: str | None

    source_bbl: str | None
    source_bin: str | None
    source_borough: str | None
    source_block: str | None
    source_lot: str | None
    resolved_bbl: str
    lot_membership: LotMembership
    attribution: Attribution

    type: str | None
    major_category: str | None
    minor_category: str | None
    problem_code: str | None
    unit_type: str | None
    space_type: str | None
    location_scope: LocationScope
    location_scope_basis: str

    complaint_status_raw: str | None
    problem_status_raw: str | None
    complaint_is_open: bool | None
    problem_is_open: bool | None
    is_duplicate_report: bool | None  # problem_duplicate_flag
    hpd_disposition: HpdDisposition
    has_311_reference: bool  # the 311 request number itself is withheld from public output

    dates: dict[str, DateValue]
    quality_flags: list[QualityFlag]
    redaction: RedactionInfo
    withheld_fields: list[str]
    original_digest: str
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None


class ComplaintGroup(BaseModel):
    complaint_id: str
    evidence_kind: EvidenceKind = EvidenceKind.REPORTED_ALLEGATION
    attribution: ComplaintAttribution
    problem_attributions: dict[str, int]  # bucket -> number of problems
    received_date: DateValue
    complaint_status_raw: str | None
    is_open: bool | None
    problem_ids: list[str]
    problem_count: int
    duplicate_problem_count: int
    types: list[str]
    major_categories: list[str]
    outcomes: dict[str, int]
    quality_flags: list[QualityFlag]


class ProblemBucket(BaseModel):
    problems: int = 0
    open: int = 0
    closed: int = 0
    status_unknown: int = 0
    duplicate_reports: int = 0  # problem_duplicate_flag = Y
    not_flagged_duplicate: int = 0  # NOT "distinct conditions": the data does not say that
    by_type: dict[str, int] = Field(default_factory=dict)
    by_major_category: dict[str, int] = Field(default_factory=dict)
    by_outcome: dict[str, int] = Field(default_factory=dict)
    received_last_12_months: int = 0
    received_last_36_months: int = 0


class ComplaintBucket(BaseModel):
    complaints: int = 0
    open: int = 0
    closed: int = 0
    status_unknown: int = 0
    with_duplicate_problems: int = 0
    hpd_reported_violations_issued: int = 0  # complaints with >=1 such problem disposition
    received_last_12_months: int = 0
    received_last_36_months: int = 0


class ClassificationCoverage(BaseModel):
    """How much of HPD's disposition text our rules recognise. Reported, not tuned."""

    problems_with_text: int
    classified: int
    other: int
    no_text: int
    coverage: float | None  # classified / problems_with_text
    other_texts: list[dict]  # sanitized text and count, most common first


class ComplaintSummary(BaseModel):
    data_as_of: str | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    total_problems: int
    total_complaints: int
    problems_by_attribution: dict[Attribution, ProblemBucket]
    complaints_by_attribution: dict[ComplaintAttribution, ComplaintBucket]
    classification: ClassificationCoverage
    conflict_counts: dict[str, int]
    quality_flag_counts: dict[str, int]
    location_scope_counts: dict[str, int]
    privacy: PrivacySummary
    withheld_field_counts: dict[str, int]


class ComplaintSourceResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[NormalizedComplaintProblem] = Field(default_factory=list)
    complaints: list[ComplaintGroup] = Field(default_factory=list)
    partial: bool = False
    summary: ComplaintSummary | None = None
    provenance: RetrievalProvenance

    @model_validator(mode="after")
    def _enforce_invariants(self) -> ComplaintSourceResult:
        building_only = {Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT}
        bad = [r.problem_id for r in self.records if r.attribution in building_only]
        bad += [
            f"complaint {c.complaint_id}"
            for c in self.complaints
            if c.attribution
            in (ComplaintAttribution.THIS_BUILDING, ComplaintAttribution.OTHER_BUILDING_ON_LOT)
        ]
        check_source_invariants(self, bad)
        # A mixed complaint must really span more than one bucket.
        for c in self.complaints:
            if (c.attribution is ComplaintAttribution.MIXED) != (len(c.problem_attributions) > 1):
                raise ValueError(f"complaint {c.complaint_id}: mixed flag inconsistent")
        return self
