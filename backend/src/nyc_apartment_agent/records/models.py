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


class AttributionBasis(StrEnum):
    """Which identifiers decided a row's attribution (D9)."""

    ROW_IDENTIFIERS = "row_identifiers"  # the row's own BBL / boro-block-lot / BIN
    # The row's HPD BuildingID maps to exactly one current HPD building record on the
    # same lot, with an assigned BIN. Only ever applied to rows that were unattributed.
    HPD_BUILDING_ID = "hpd_building_id"
    # DOB complaints (Task 5): the row has only a BIN; the BIN's place on the lot comes
    # from Footprints and/or HPD building records, not from the row itself.
    BIN_LOT_MAPPING = "bin_lot_mapping"


class LotMembership(StrEnum):
    CONFIRMED = "confirmed"  # source BBL and/or boro-block-lot agree with the resolved lot
    CONFLICTING = "conflicting"  # source BBL and boro-block-lot point at different lots
    # Task 5: the row has no lot identifiers; membership is inferred from its BIN through
    # the resolver, Footprints or HPD building records.
    VIA_BIN_MAPPING = "via_bin_mapping"


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


class EvidenceKind(StrEnum):
    OFFICIAL_FINDING = "official_finding"  # e.g. an HPD violation issued after inspection
    REPORTED_ALLEGATION = "reported_allegation"  # e.g. a tenant complaint to HPD
    # e.g. an HPD building or registration record: what HPD has on file, not a finding
    # about conditions, and not independent proof of title or actual management.
    ADMINISTRATIVE_RECORD = "administrative_record"


class DigestBasis(StrEnum):
    """What `original_digest` was computed over. Live retrieval sees the unredacted row;
    offline replay of a committed fixture only ever sees the sanitized row, so its digest
    is NOT an original-row digest and cannot be checked against original evidence."""

    ORIGINAL_ROW = "original_row"  # input row carried no redaction or withheld markers
    SANITIZED_ROW = "sanitized_row"  # input row was already sanitized (e.g. fixture replay)


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
    # HPD administrative records (Task 3)
    NON_CURRENT_RECORD = "non_current_record"  # lifecycle/record status not Building/Active
    MULTIPLE_RECORDS_FOR_BIN = "multiple_records_for_bin"  # >1 current HPD record, same BIN
    MULTIPLE_REGISTRATION_ROWS = "multiple_registration_rows"  # >1 row for one building
    SHARED_REGISTRATION = "shared_registration"  # one registration id, several buildings
    BUILDING_RECORD_MISMATCH = "building_record_mismatch"  # BIN/lot differs from HPD record
    # Other
    UNKNOWN_CLASS = "unknown_class"
    # Complaints (Task 2)
    DATE_ORDER = "date_order"  # a status date earlier than the received date
    COMPLAINT_FIELDS_DISAGREE = "complaint_fields_disagree"  # differs between problems
    UNIT_DETAIL_REDACTED = "unit_detail_redacted"  # description had unit/floor detail
    # DOB complaints (Task 5, design 18.3 and 18.6)
    BIN_SINGLE_SOURCE = "bin_single_source"  # one source places the BIN on the lot; other silent
    BIN_LOT_CONFLICT = "bin_lot_conflict"  # two sources place the BIN on different lots
    BIN_NOT_ON_CURRENT_LOT = "bin_not_on_current_lot"  # row BIN in no current lot list
    DISPOSITION_CODE_UNKNOWN = "disposition_code_unknown"  # no official definition found
    COMPLAINT_CATEGORY_UNKNOWN = "complaint_category_unknown"  # not in DOB's category list


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


class DataFreshness(BaseModel):
    """How current a Socrata dataset is, from its catalog entry (/api/views/<id>.json).

    Two timestamps that must not be confused:
    - data_updated_at (`rowsUpdatedAt`): when the rows last changed. This is what decides
      whether a record could reflect recent events (e.g. a registration renewal).
    - metadata_updated_at (`viewLastModified`): when the catalog page changed (description,
      columns, tags). It can be recent while the data is months old.
    """

    dataset_id: str
    data_updated_at: str | None = None
    metadata_updated_at: str | None = None
    stated_update_frequency: str | None = None
    checked_on: str | None = None  # date the staleness was judged against
    days_since_data_update: int | None = None
    stale_threshold_days: int | None = None  # from the stated frequency, plus a margin
    stale: bool | None = None  # None = could not be judged
    error: str | None = None


class RedactionInfo(BaseModel):
    """What was removed from this record for privacy. Kinds and counts only, never values.

    `applied` says where it happened: "this_run" (we redacted a live row now),
    "upstream" (the row came from a sanitized fixture; counts rebuilt from its markers),
    or "none". Either way the same metrics come out, so privacy numbers can be
    reproduced from sanitized fixtures.
    """

    applied: str
    fields_masked: list[str] = Field(default_factory=list)  # apartment, story
    text_fields_redacted: list[str] = Field(default_factory=list)  # novdescription
    text_replacements: dict[str, int] = Field(default_factory=dict)  # unit_id/floor/position


class PrivacySummary(BaseModel):
    rows: int = 0
    rows_with_unit_fields_masked: int = 0
    rows_with_text_redacted: int = 0
    rows_with_any_redaction: int = 0
    text_replacements_by_kind: dict[str, int] = Field(default_factory=dict)
    rows_redacted_this_run: int = 0
    rows_redacted_upstream: int = 0


class DateValue(BaseModel):
    raw: str | None
    value: dt.date | None  # None when missing, unparseable or implausible


class NormalizedViolation(BaseModel):
    """One HPD violation, ready for the public report.

    Never contains apartment or story values, and the description is redacted.
    The unredacted source row lives only in the evidence store, keyed by original_digest.
    """

    model_config = ConfigDict(populate_by_name=True)

    dataset_id: str
    evidence_kind: EvidenceKind = EvidenceKind.OFFICIAL_FINDING
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
    # D9: how the attribution was decided. original_attribution and mapped_bin are set
    # only when the HPD BuildingID enhancement changed an unattributed row.
    attribution_basis: AttributionBasis = AttributionBasis.ROW_IDENTIFIERS
    original_attribution: Attribution | None = None
    mapped_bin: str | None = None

    violation_class: str | None = Field(alias="class")
    is_informational: bool  # Class I: information / orders, kept separate from A/B/C
    order_category: OrderCategory | None  # only for Class I
    potentially_consequential: bool  # e.g. vacate, relocation, AEP, underlying conditions
    # What an order record can and cannot show. An order record is evidence that HPD
    # issued the order; it does not by itself confirm the program or condition is current.
    order_interpretation: str | None = None

    description: str | None  # redacted
    description_redacted: bool
    redaction: RedactionInfo
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
    # Keyed HMAC-SHA256 of the unredacted row (server-side key). Not a plain hash: a plain
    # hash of the original could be brute-forced back to the apartment and floor.
    original_digest: str
    # original_row: live input, verifiable against the evidence store's original row.
    # sanitized_row: replayed fixture; the digest covers only the sanitized row.
    digest_basis: DigestBasis
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
    privacy: PrivacySummary
    location_scope_counts: dict[str, int]


class BuildingIdAttributionReport(BaseModel):
    """Before/after record of the optional HPD BuildingID attribution step (D9)."""

    applied: bool
    not_applied_reason: str | None = None
    buildings_dataset_id: str | None = None
    buildings_status: RetrievalStatus | None = None
    buildings_data_as_of: str | None = None
    before: dict[str, int] = Field(default_factory=dict)  # row bucket -> count
    after: dict[str, int] = Field(default_factory=dict)
    upgraded: dict[str, int] = Field(default_factory=dict)  # new bucket -> rows moved
    upgraded_by_building_id: dict[str, dict] = Field(default_factory=dict)
    kept_unattributed: dict[str, int] = Field(default_factory=dict)  # reason -> rows
    # Rows whose own BIN gave the attribution but whose BuildingID maps to a different
    # BIN. Reported only; the original attribution is never changed by this step.
    bin_building_id_disagreements: int = 0
    complaints_before: dict[str, int] | None = None  # complaints only (grouped level)
    complaints_after: dict[str, int] | None = None
    note: str = (
        "BuildingID is a secondary key: used only for rows the row's own identifiers left "
        "unattributed, on a confirmed lot, when it maps to exactly one current HPD building "
        "record on that lot with an assigned BIN. Never applied at tax-lot scope."
    )


def check_basis_invariants(result, records) -> None:
    for r in records:
        if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID:
            if result.join_scope is not JoinScope.BUILDING:
                raise ValueError("BuildingID attribution requires a building-level join")
            if r.original_attribution is not Attribution.UNATTRIBUTED_ON_LOT:
                raise ValueError("BuildingID attribution may only upgrade unattributed rows")
            if r.lot_membership is not LotMembership.CONFIRMED or not r.mapped_bin:
                raise ValueError("BuildingID attribution needs a confirmed lot and mapped BIN")
        elif r.original_attribution is not None or r.mapped_bin is not None:
            raise ValueError("original_attribution/mapped_bin set without a basis change")


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
    building_id_attribution: BuildingIdAttributionReport | None = None

    @model_validator(mode="after")
    def _enforce_invariants(self) -> SourceResult:
        check_source_invariants(
            self,
            [r.violation_id for r in self.records if r.attribution in BUILDING_ONLY_ATTRIBUTIONS],
        )
        check_basis_invariants(self, self.records)
        return self


def check_source_invariants(result, building_attributed_ids: list[str]) -> None:
    """Rules every source result must satisfy (shared by violations and complaints)."""
    if result.join_scope is not JoinScope.BUILDING:
        if building_attributed_ids:
            raise ValueError(
                "building-level attribution without a building-level join: "
                f"{building_attributed_ids[:5]}"
            )
        if result.resolved_bin is not None:
            raise ValueError("resolved_bin may only be set at building scope")
        if result.summary is not None and result.summary.building_specific_claims_allowed:
            raise ValueError("building-specific claims require a building-level join")
    if result.status is RetrievalStatus.UNSUPPORTED_JOIN and (
        result.records or result.provenance.pages
    ):
        raise ValueError("an unsupported join must not make requests or return records")
    if result.status is RetrievalStatus.NO_RECORDS_FOUND and (
        result.records or result.provenance.source_count != 0
    ):
        raise ValueError("no_records_found requires a complete fetch with count 0")
    if result.status is RetrievalStatus.RECORDS_FOUND and (
        not result.records or result.partial or result.provenance.incomplete_reasons
    ):
        raise ValueError("records_found requires a complete, non-empty fetch")
    if result.status is RetrievalStatus.INCOMPLETE_PAGINATION and not (
        result.partial and result.provenance.incomplete_reasons
    ):
        raise ValueError("incomplete_pagination must be marked partial, with reasons")


class RawEvidence(BaseModel):
    """The unredacted source row plus where it came from. Stored outside git."""

    dataset_id: str
    source_record_id: str
    original_sha256: str  # plain hash; private, stays in controlled storage
    original_digest: str  # keyed hash; what public records carry
    redacted_sha256: str
    redacted_fields: list[str]
    # The row as received. With basis sanitized_row it is a sanitized fixture row, not
    # original evidence: verify() then proves only that the sanitized copy is intact.
    basis: DigestBasis
    original_row: dict
    query_url: str
    fetched_at: str
    data_as_of: str | None
