"""Typed results for DOB ECB Violations (6bgk-3dad), Phase 2 Task 5 step 3.

Distinctions built into the types (design sections 4, 7, 18.2 and 18.3):

1. An ECB summons is an enforcement action: a charge DOB issued
   (`evidence_kind = enforcement_action`). It is not a finding that a violation occurred.
2. Three statuses stay separate, each with its raw value: the correction status with DOB
   (`ecb_violation_status`), the OATH hearing record (`hearing_status`, grouped by the
   project's approved mapping, with a note saying what is not documented) and the
   certification status. None is derived from another; disagreements are flagged.
3. Money is Decimal, as recorded. A requested-building financial summary counts only
   records attributed to the requested building, and only when building claims are
   allowed.
4. Never public: respondent name and address, `violation_description` (withheld in V1,
   D31), `hearing_time`. A picklist value or a section/law text is public only when it is
   on the measured list; otherwise it is withheld and flagged.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .admin_models import SourceRowRef
from .dob_models import BinEvidence
from .models import (
    Attribution,
    AttributionBasis,
    DateValue,
    DigestBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalProvenance,
    RetrievalStatus,
    check_source_invariants,
)

WITHHELD_VALUE = "[UNCOMMON VALUE WITHHELD]"


class CorrectionStatus(StrEnum):
    """`ecb_violation_status`, as the ECB dictionary documents it."""

    OPEN_WITH_DOB = "open_with_dob"  # ACTIVE: "still needs to be addressed"
    RESOLVED_WITH_DOB = "resolved_with_dob"  # RESOLVE: "fixed with DOB or dismissed by OATH"
    UNKNOWN = "unknown"


class Adjudication(StrEnum):
    """The project's grouping of `hearing_status` (approved mapping, not OATH's)."""

    UPHELD = "upheld"
    UPHELD_BY_DEFAULT = "upheld_by_default"  # DEFAULT; meaning not documented
    DISMISSED = "dismissed"
    PENDING = "pending"
    OTHER = "other"  # WRITTEN OFF; meaning not documented
    UNKNOWN = "unknown"  # no hearing status, or a value not on the measured list


class Severity(StrEnum):
    HAZARDOUS = "hazardous"
    NON_HAZARDOUS = "non_hazardous"
    CLASS_1 = "class_1"  # in use, not in the dictionary
    CLASS_2 = "class_2"
    CLASS_3 = "class_3"
    UNKNOWN = "unknown"


UNDOCUMENTED_SEVERITIES = frozenset({Severity.CLASS_1, Severity.CLASS_2, Severity.CLASS_3})


class LotFormat(StrEnum):
    FOUR_DIGIT = "four_digit"  # e.g. 0041
    FIVE_DIGIT = "five_digit"  # e.g. 00041
    MISSING = "missing"
    MALFORMED = "malformed"  # boro, block or lot not in a recognised form


class MatchedBy(StrEnum):
    """Which clause of the ECB query the row satisfies (both, when they overlap)."""

    LOT = "lot"  # its boro/block/lot is in the lot family
    RESOLVED_BIN = "resolved_bin"  # its BIN is the resolved BIN (building scope only)


class EcbSourceRow(SourceRowRef):
    """One source row behind a summons. `source_row_id` is `isn_dob_bis_extract` (the
    query has no `$select`, so no Socrata :id); `row_index` is the row's position in the
    fetch, so two identical copies stay two lineage entries."""

    row_index: int
    query_url: str
    fetched_at: str


class Infraction(BaseModel):
    position: int  # N in infraction_codeN
    code: str | None  # None when the code is not on the measured code list
    law_description: str | None  # DOB's text, only for an allowlisted (code, text) pair
    text_withheld: bool  # a text was recorded but is not shown


class NormalizedEcbViolation(BaseModel):
    """One ECB summons, ready for the public report."""

    dataset_id: str
    evidence_kind: Literal[EvidenceKind.ENFORCEMENT_ACTION] = EvidenceKind.ENFORCEMENT_ACTION
    isn_dob_bis_extract: str
    ecb_violation_number: str | None  # public, OATH-searchable; None if malformed
    dob_violation_number: str | None
    source_rows: list[EcbSourceRow]
    matched_by: list[MatchedBy]

    source_bin: str | None
    source_boro: str | None
    source_block: str | None
    source_lot: str | None  # as recorded (4 or 5 digits)
    lot_format: LotFormat
    row_bbl: str | None  # boro + block + 4-digit lot, when all three are well formed
    resolved_bbl: str
    lot_membership: LotMembership
    attribution: Attribution
    attribution_basis: AttributionBasis

    correction_status: CorrectionStatus
    correction_status_raw: str | None
    adjudication: Adjudication
    hearing_status_raw: str | None  # withheld marker if not on the measured list
    adjudication_note: str  # what the sources do and do not document for this value
    certification_status: str | None  # as recorded, if on the measured list
    severity: Severity
    severity_raw: str | None
    violation_type: str | None
    aggravated_level: str | None
    infractions: list[Infraction]

    issue_date: DateValue
    served_date: DateValue
    hearing_date: DateValue

    penalty_imposed: Decimal | None  # source field `penality_imposed` (sic)
    amount_paid: Decimal | None
    balance_due: Decimal | None

    withheld_fields: list[str]  # source fields never public, or withheld for this row
    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None

    @model_validator(mode="after")
    def _consistent(self) -> NormalizedEcbViolation:
        flags = set(self.quality_flags)
        if self.attribution in (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT):
            blocking = flags & {
                QualityFlag.BIN_LOT_CONFLICT,
                QualityFlag.LOT_IDENTIFIER_CONFLICT,
                QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT,
                QualityFlag.BIN_RETIRED_OR_UNKNOWN,
                QualityFlag.BIN_ON_OTHER_LOT,
                QualityFlag.BIN_NOT_ON_CURRENT_LOT,
            }
            if blocking:
                raise ValueError(f"building attribution with {sorted(blocking)}")
            if self.lot_membership is LotMembership.CONFLICTING:
                raise ValueError("building attribution with conflicting lot identifiers")
            if self.lot_membership is LotMembership.VIA_BIN_MAPPING and not (
                self.attribution is Attribution.THIS_BUILDING
                and QualityFlag.LOT_PARTS_MISSING in flags
            ):
                raise ValueError("via_bin_mapping only for a resolved-BIN row with no lot")
            if self.attribution_basis is not AttributionBasis.ROW_IDENTIFIERS:
                raise ValueError("ECB attribution comes from the row's own identifiers")
        if self.adjudication is Adjudication.UNKNOWN and QualityFlag.STATUS_UNKNOWN not in flags:
            raise ValueError("an unknown adjudication must be flagged status_unknown")
        if (self.severity in UNDOCUMENTED_SEVERITIES) != (
            QualityFlag.SEVERITY_UNDOCUMENTED_VALUE in flags
        ):
            raise ValueError("CLASS - n severities and severity_undocumented_value go together")
        for inf in self.infractions:
            if inf.law_description is not None and inf.code is None:
                raise ValueError("a law text is shown only with its allowlisted code")
        if not self.source_rows or self.source_rows[0].original_digest != self.original_digest:
            raise ValueError("source_rows must start with the record's own source row")
        return self


class EcbBucket(BaseModel):
    summonses: int = 0
    by_correction_status: dict[str, int] = Field(default_factory=dict)
    by_adjudication: dict[str, int] = Field(default_factory=dict)
    # "correction_status|adjudication" -> count; never netted or reconciled
    by_correction_and_adjudication: dict[str, int] = Field(default_factory=dict)
    by_certification_status: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_violation_type: dict[str, int] = Field(default_factory=dict)
    by_bin: dict[str, int] = Field(default_factory=dict)
    by_lot_format: dict[str, int] = Field(default_factory=dict)
    issued_last_12_months: int = 0
    issued_last_36_months: int = 0


class EcbFinancials(BaseModel):
    """Money as DOB recorded it, for the requested building only. Not reconciled."""

    attribution: Literal[Attribution.THIS_BUILDING] = Attribution.THIS_BUILDING
    summonses: int
    penalty_imposed_total: Decimal
    amount_paid_total: Decimal
    balance_due_total: Decimal
    summonses_with_positive_balance: int
    summonses_with_negative_balance: int
    summonses_with_unparseable_amounts: int  # excluded from the totals
    note: str


class EcbRowBin(BaseModel):
    """A BIN seen on ECB rows that is on no current list for the lot (design 18.3)."""

    bin: str
    rows: int
    classification: Literal["retired_or_unknown", "on_other_lot", "on_lot_family", "not_checked"]
    footprints_lots_for_bin: list[str] = Field(default_factory=list)
    note: str


class EcbSummary(BaseModel):
    data_as_of: str | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    building_claims_denied_reason: str | None = None
    total_summonses: int
    source_rows: int
    by_attribution: dict[Attribution, EcbBucket]
    lot_format_counts: dict[str, int]
    matched_by_counts: dict[str, int]  # "lot", "resolved_bin", "lot+resolved_bin"
    status_fields_disagree: int
    adjudication_notes: dict[str, str]  # raw hearing status -> note, for values present
    requested_building_financials: EcbFinancials | None
    quality_flag_counts: dict[str, int]
    conflict_counts: dict[str, int]
    statement: str


ECB_CONFLICT_FLAGS = frozenset(
    {
        QualityFlag.LOT_IDENTIFIER_CONFLICT,
        QualityFlag.STATUS_FIELDS_DISAGREE,
        QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT,
        QualityFlag.BIN_LOT_CONFLICT,
        QualityFlag.BIN_ON_OTHER_LOT,
    }
)


class EcbViolationResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[NormalizedEcbViolation] = Field(default_factory=list)
    partial: bool = False
    summary: EcbSummary | None = None
    provenance: RetrievalProvenance  # the ECB fetch
    lot_family: list[str] = Field(default_factory=list)
    # "<family BBL>:<lot as queried>", in query order, e.g. 1008350041:0041
    lot_formats_queried: list[str] = Field(default_factory=list)
    bin_clause: str | None = None  # building scope only: the resolved BIN
    bin_evidence: list[BinEvidence] = Field(default_factory=list)
    bin_lot_conflicts: list[str] = Field(default_factory=list)
    row_bins_not_on_lot: list[EcbRowBin] = Field(default_factory=list)
    # Footprints (by lot, and by BIN when needed) and HPD building lookups.
    bin_evidence_provenance: list[RetrievalProvenance] = Field(default_factory=list)
    bin_evidence_complete: bool = False
    coverage_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> EcbViolationResult:
        building_only = (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT)
        check_source_invariants(
            self, [r.isn_dob_bis_extract for r in self.records if r.attribution in building_only]
        )
        if self.join_scope is not JoinScope.BUILDING:
            if self.bin_clause is not None or "bin=" in (self.provenance.where or ""):
                raise ValueError("a tax-lot resolution never authorizes a BIN clause")
            if any(MatchedBy.RESOLVED_BIN in r.matched_by for r in self.records):
                raise ValueError("no row is matched by BIN at tax-lot scope")
        elif self.bin_clause is not None and self.bin_clause != self.resolved_bin:
            raise ValueError("the BIN clause may only use the resolved BIN")
        if self.summary is not None:
            denied = self.resolved_bin is None or self.resolved_bin in self.bin_lot_conflicts
            if self.summary.building_specific_claims_allowed and denied:
                raise ValueError("no requested-building claims when the resolved BIN conflicts")
            fin = self.summary.requested_building_financials
            if fin is not None:
                if not self.summary.building_specific_claims_allowed:
                    raise ValueError("financials for the requested building need building claims")
                mine = [r for r in self.records if r.attribution is Attribution.THIS_BUILDING]
                if fin.summonses != len(mine):
                    raise ValueError("financials must count only requested-building records")
        if self.status is RetrievalStatus.RECORDS_FOUND:
            idx = [ref.row_index for r in self.records for ref in r.source_rows]
            if len(idx) != len(set(idx)) or len(idx) != self.provenance.source_count:
                raise ValueError("every fetched source row must be listed exactly once")
        for r in self.records:
            if r.attribution is Attribution.THIS_BUILDING and (
                r.source_bin != self.resolved_bin or self.resolved_bin in self.bin_lot_conflicts
            ):
                raise ValueError(f"{r.isn_dob_bis_extract}: this_building needs the resolved BIN")
            if (
                r.attribution is Attribution.OTHER_BUILDING_ON_LOT
                and r.source_bin == self.resolved_bin
            ):
                raise ValueError(f"{r.isn_dob_bis_extract}: resolved BIN counted as another")
        return self
