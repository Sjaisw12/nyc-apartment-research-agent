"""Typed results for records attached to a resolved lot or building.

Three ideas run through these models:

1. RetrievalStatus says whether we *could check*, separately from what we found.
   An empty list is never returned without a status that explains it.
2. Attribution says how far a record may be read as being about the user's building.
   A tax-lot resolution can never produce building-level attribution; the validator
   on SourceResult enforces that, so the rule does not depend on every caller.
3. Source values are kept as the source sent them (raw strings). Anything we derive
   (parsed dates, is_open, scopes, flags) sits in separate, clearly named fields.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalStatus(StrEnum):
    RECORDS_FOUND = "records_found"  # complete fetch, at least one record
    NO_RECORDS_FOUND = "no_records_found"  # complete fetch, the source count was 0
    SOURCE_UNAVAILABLE = "source_unavailable"  # the source failed before any count
    INCOMPLETE_PAGINATION = "incomplete_pagination"  # some rows, but not provably all
    UNSUPPORTED_JOIN = "unsupported_join"  # the resolver did not permit this join


class JoinScope(StrEnum):
    BUILDING = "building"  # resolver verified BBL + cross-verified BIN
    TAX_LOT = "tax_lot"  # resolver verified the BBL only


class Attribution(StrEnum):
    THIS_BUILDING = "this_building"  # building-level result; row BIN = resolved BIN
    OTHER_BUILDING_ON_LOT = "other_building_on_lot"  # building-level; row has another BIN
    UNATTRIBUTED_ON_LOT = "unattributed_on_lot"  # no usable BIN, or lot identifiers conflict
    LOT_LEVEL = "lot_level"  # tax-lot result: about the lot, not any one building


BUILDING_ONLY_ATTRIBUTIONS = frozenset(
    {Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT}
)


class LotMembership(StrEnum):
    CONFIRMED = "confirmed"  # source BBL and/or boro-block-lot agree with the resolved lot
    CONFLICTING = "conflicting"  # source BBL and boro-block-lot point at different lots


class LocationScope(StrEnum):
    UNIT = "unit"  # an apartment is named (field or description); unit id never copied
    COMMON_AREA = "common_area"  # description names a shared area (hall, cellar, roof...)
    BUILDING_WIDE = "building_wide"  # description says entire building / premises
    UNSPECIFIED = "unspecified"  # no evidence either way; NOT the same as building-wide


class OrderCategory(StrEnum):
    """What a Class I (information / order) record is about. See hpd_violations.py."""

    VACATE_ORDER = "vacate_order"
    VACANT_PREMISES_ORDER = "vacant_premises_order"
    RELOCATION_ORDER = "relocation_order"
    ALTERNATIVE_ENFORCEMENT_PROGRAM = "alternative_enforcement_program"
    UNDERLYING_CONDITIONS_ORDER = "underlying_conditions_order"
    REGISTRATION_NONCOMPLIANCE = "registration_noncompliance"
    OTHER = "other"


class QualityFlag(StrEnum):
    # Lot identity
    BBL_MISSING = "bbl_missing"  # source bbl empty; lot matched by boro-block-lot
    BBL_INVALID = "bbl_invalid"  # source bbl present but not a valid BBL (e.g. "0")
    LOT_PARTS_MISSING = "lot_parts_missing"  # boroid/block/lot absent or malformed
    LOT_IDENTIFIER_CONFLICT = "lot_identifier_conflict"  # bbl vs boro-block-lot disagree
    # Building identity
    BIN_MISSING = "bin_missing"
    BIN_PLACEHOLDER = "bin_placeholder"  # n000000: unassigned/unknown, never a join key
    BIN_INVALID = "bin_invalid"
    # Status
    STATUS_FIELDS_DISAGREE = "status_fields_disagree"
    STATUS_UNKNOWN = "status_unknown"  # violationstatus not Open/Close
    # Dates
    IMPLAUSIBLE_DATE = "implausible_date"  # parsed, but outside the plausible range
    UNPARSEABLE_DATE = "unparseable_date"
    # Duplicates
    DUPLICATE_ID_SAME_CONTENT = "duplicate_id_same_content"  # identical copies collapsed
    DUPLICATE_ID_DIFFERENT_CONTENT = "duplicate_id_different_content"  # kept, flagged
    # Other
    UNKNOWN_CLASS = "unknown_class"
    UNIT_DETAIL_REDACTED = "unit_detail_redacted"  # description had unit/floor detail


CONFLICT_FLAGS = frozenset(
    {
        QualityFlag.LOT_IDENTIFIER_CONFLICT,
        QualityFlag.STATUS_FIELDS_DISAGREE,
        QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT,
    }
)


class PageRecord(BaseModel):
    """One HTTP response in a paged retrieval (the count call is page -1)."""

    index: int
    url: str
    rows: int
    fetched_at: str
    attempts: int
    truth_last_modified: str | None
    last_modified: str | None
    request_id: str | None


class RetrievalProvenance(BaseModel):
    dataset_id: str
    where: str | None = None
    order_by: str | None = None
    page_size: int | None = None
    row_cap: int | None = None
    source_count: int | None = None  # count(*) for the same filter
    rows_fetched: int = 0
    pages: list[PageRecord] = Field(default_factory=list)
    restarts: int = 0  # whole-fetch restarts because the dataset changed mid-way
    data_as_of: str | None = None  # X-SODA2-Truth-Last-Modified, same on every page
    incomplete_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class DateValue(BaseModel):
    raw: str | None
    value: dt.date | None  # None when missing, unparseable or implausible


class NormalizedViolation(BaseModel):
    """One HPD violation, ready for the public report.

    Never contains apartment or story values, and the description is redacted.
    The unredacted source row lives only in the evidence store, keyed by original_sha256.
    """

    model_config = ConfigDict(populate_by_name=True)

    dataset_id: str
    violation_id: str
    nov_id: str | None
    order_number: str | None
    building_id: str | None
    registration_id: str | None

    # Identifiers exactly as the source sent them.
    source_bbl: str | None
    source_bin: str | None
    source_boroid: str | None
    source_block: str | None
    source_lot: str | None
    resolved_bbl: str  # the lot the resolver gave us
    lot_membership: LotMembership
    attribution: Attribution

    violation_class: str | None = Field(alias="class")
    is_informational: bool  # Class I: information / orders, kept separate from A/B/C
    order_category: OrderCategory | None  # only for Class I
    potentially_consequential: bool  # e.g. vacate, relocation, AEP, underlying conditions

    description: str | None  # redacted
    description_redacted: bool
    nov_type: str | None
    rent_impairing: bool | None

    violation_status_raw: str | None
    current_status_raw: str | None
    current_status_id: str | None
    is_open: bool | None  # from violationstatus; None if not Open/Close

    dates: dict[str, DateValue]
    location_scope: LocationScope
    location_scope_basis: str

    quality_flags: list[QualityFlag]
    original_sha256: str  # hash of the unredacted source row
    redacted_sha256: str  # hash of the public (redacted) source row
    query_url: str
    fetched_at: str
    data_as_of: str | None

    @property
    def conflicts(self) -> list[QualityFlag]:
        return [f for f in self.quality_flags if f in CONFLICT_FLAGS]


class BucketSummary(BaseModel):
    records: int = 0
    open: int = 0
    closed: int = 0
    status_unknown: int = 0
    hazard_class_counts: dict[str, int] = Field(default_factory=dict)  # A/B/C, all statuses
    open_hazard_class_counts: dict[str, int] = Field(default_factory=dict)
    information_order_counts: dict[str, int] = Field(default_factory=dict)  # Class I
    open_rent_impairing: int = 0
    inspected_last_12_months: int = 0
    inspected_last_36_months: int = 0
    consequential_orders: list[dict] = Field(default_factory=list)


class ViolationSummary(BaseModel):
    data_as_of: str | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    total_records: int
    by_attribution: dict[Attribution, BucketSummary]
    conflict_counts: dict[str, int]
    quality_flag_counts: dict[str, int]
    redacted_descriptions: int
    location_scope_counts: dict[str, int]


class SourceResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None  # only set at building scope
    records: list[NormalizedViolation] = Field(default_factory=list)
    partial: bool = False  # True when records are a known-incomplete subset
    summary: ViolationSummary | None = None
    provenance: RetrievalProvenance

    @model_validator(mode="after")
    def _enforce_invariants(self) -> SourceResult:
        if self.join_scope is not JoinScope.BUILDING:
            bad = [
                r.violation_id for r in self.records if r.attribution in BUILDING_ONLY_ATTRIBUTIONS
            ]
            if bad:
                raise ValueError(
                    f"building-level attribution without a building-level join: {bad[:5]}"
                )
            if self.resolved_bin is not None:
                raise ValueError("resolved_bin may only be set at building scope")
            if self.summary is not None and self.summary.building_specific_claims_allowed:
                raise ValueError("building-specific claims require a building-level join")
        if self.status is RetrievalStatus.UNSUPPORTED_JOIN and (
            self.records or self.provenance.pages
        ):
            raise ValueError("an unsupported join must not make requests or return records")
        if self.status is RetrievalStatus.NO_RECORDS_FOUND and (
            self.records or self.provenance.source_count != 0
        ):
            raise ValueError("no_records_found requires a complete fetch with count 0")
        if self.status is RetrievalStatus.RECORDS_FOUND and (
            not self.records or self.partial or self.provenance.incomplete_reasons
        ):
            raise ValueError("records_found requires a complete, non-empty fetch")
        if self.status is RetrievalStatus.INCOMPLETE_PAGINATION and not (
            self.partial and self.provenance.incomplete_reasons
        ):
            raise ValueError("incomplete_pagination must be marked partial, with reasons")
        return self


class RawEvidence(BaseModel):
    """The unredacted source row plus where it came from. Stored outside git."""

    dataset_id: str
    source_record_id: str
    original_sha256: str
    redacted_sha256: str
    redacted_fields: list[str]
    original_row: dict
    query_url: str
    fetched_at: str
    data_as_of: str | None
