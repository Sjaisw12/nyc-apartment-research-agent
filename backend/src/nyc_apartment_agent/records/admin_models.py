"""Typed results for HPD administrative records (Phase 2, Task 3).

Administrative records say what HPD has on file: building records, registrations and
registration contacts. They are `evidence_kind = administrative_record`, separate from
violations (official findings) and complaints (reported allegations). A registered
owner or agent is HPD's registration record, not independent proof of legal title or
of who actually manages the building.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from .models import (
    Attribution,
    DataFreshness,
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


class HpdBuildingRecord(BaseModel):
    dataset_id: str
    evidence_kind: EvidenceKind = EvidenceKind.ADMINISTRATIVE_RECORD
    building_id: str

    # Original identifiers as HPD sent them (the dataset has no bbl field).
    source_boroid: str | None
    source_block: str | None
    source_lot: str | None
    source_bin: str | None
    resolved_bbl: str
    lot_membership: LotMembership
    attribution: Attribution

    address: str | None  # display only, never a join key
    low_house_number: str | None
    high_house_number: str | None
    zip: str | None
    management_program: str | None
    dob_building_class: str | None
    legal_stories: int | None
    legal_class_a_units: int | None
    legal_class_b_units: int | None
    registration_id: str | None  # None when HPD's record has 0 or nothing
    registration_id_raw: str | None
    lifecycle: str | None  # Building, Demolished, Land, Planned, UnderConstruction, ...
    record_status: str | None  # Active, Inactive, Pending
    is_current: bool  # lifecycle Building and record status Active

    quality_flags: list[QualityFlag]
    redaction: RedactionInfo  # nothing personal in this dataset; recorded for uniformity
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None


class RequestedBuildingStatus(StrEnum):
    ONE_CURRENT_RECORD = "one_current_record"
    MULTIPLE_CURRENT_RECORDS = "multiple_current_records"  # ambiguous: none is chosen
    ONLY_NON_CURRENT_RECORDS = "only_non_current_records"
    NO_RECORD_FOR_BIN = "no_record_for_bin"
    NOT_APPLICABLE_TAX_LOT = "not_applicable_tax_lot"  # lot-level resolution: no building


class RequestedBuilding(BaseModel):
    status: RequestedBuildingStatus
    building_id: str | None = None
    legal_class_a_units: int | None = None
    legal_class_b_units: int | None = None
    legal_stories: int | None = None
    dob_building_class: str | None = None
    management_program: str | None = None
    registration_id: str | None = None
    candidate_building_ids: list[str] = Field(default_factory=list)


class BuildingBucket(BaseModel):
    records: int = 0
    current: int = 0
    non_current: int = 0
    current_legal_class_a_units: int = 0
    building_ids: list[str] = Field(default_factory=list)


class BuildingSummary(BaseModel):
    data_as_of: str | None
    freshness: DataFreshness | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    total_records: int
    current_records: int
    by_attribution: dict[Attribution, BuildingBucket]
    requested_building: RequestedBuilding
    quality_flag_counts: dict[str, int]
    lifecycle_counts: dict[str, int]


class BuildingSourceResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[HpdBuildingRecord] = Field(default_factory=list)
    partial: bool = False
    summary: BuildingSummary | None = None
    freshness: DataFreshness | None = None
    provenance: RetrievalProvenance

    @model_validator(mode="after")
    def _enforce_invariants(self) -> BuildingSourceResult:
        building_only = {Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT}
        bad = [r.building_id for r in self.records if r.attribution in building_only]
        check_source_invariants(self, bad)
        if self.summary is not None and self.join_scope is not JoinScope.BUILDING:
            if self.summary.requested_building.status is not (
                RequestedBuildingStatus.NOT_APPLICABLE_TAX_LOT
            ):
                raise ValueError("a lot-level result cannot name a requested building record")
        return self
