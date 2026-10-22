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


class SourceRowRef(BaseModel):
    """One original source row behind a normalized record (Socrata :id + keyed digest).
    When identical copies under different :ids are collapsed, every copy stays listed."""

    source_row_id: str
    original_digest: str


def check_source_rows(result, records) -> None:
    """Every fetched source row is accounted for by exactly one normalized record."""
    refs = [ref.source_row_id for r in records for ref in r.source_rows]
    for r in records:
        if not r.source_rows or r.source_rows[0].source_row_id != r.source_row_id:
            raise ValueError("source_rows must start with the record's own source row")
    # A complete fetch: unique row ids (the pager marks repeats incomplete), all listed.
    if result.status is RetrievalStatus.RECORDS_FOUND:
        if len(refs) != len(set(refs)):
            raise ValueError("a source row is listed under more than one record")
        if len(refs) != result.provenance.source_count:
            raise ValueError("source rows listed do not match the source count")


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


# ---- Registrations (Task 3, step 2) --------------------------------------------------


class RegistrationStatus(StrEnum):
    """What the checked snapshot shows. Never "expired", "unregistered" or "non-compliant"
    (approved decisions D10, D12): the data cannot establish legal status."""

    ON_FILE_END_DATE_NOT_PASSED = "on_file_end_date_not_passed"
    # End date passed, but after the dataset's last data update: a renewal made since
    # then would not be in this snapshot.
    ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE = "on_file_end_date_passed_after_data_update"
    # End date passed before the last data update, and no later row is on file.
    ON_FILE_END_DATE_PASSED_BEFORE_DATA_UPDATE = "on_file_end_date_passed_before_data_update"
    ON_FILE_END_DATE_PASSED_FRESHNESS_UNKNOWN = "on_file_end_date_passed_freshness_unknown"
    ON_FILE_END_DATE_UNKNOWN = "on_file_end_date_unknown"
    NONE_ON_FILE = "none_on_file"  # only: none found in the checked snapshot


REGISTRATION_STATUS_NOTE = {
    RegistrationStatus.ON_FILE_END_DATE_NOT_PASSED: (
        "A registration is on file with an end date that has not passed."
    ),
    RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE: (
        "The registration on file has an end date that has passed, but the dataset was last "
        "updated before that date, so a renewal would not appear in it. Status unknown."
    ),
    RegistrationStatus.ON_FILE_END_DATE_PASSED_BEFORE_DATA_UPDATE: (
        "The registration on file ended before the dataset's last update, and the snapshot "
        "shows no later registration. This is not a finding of legal non-compliance."
    ),
    RegistrationStatus.ON_FILE_END_DATE_PASSED_FRESHNESS_UNKNOWN: (
        "The registration on file has an end date that has passed; the dataset's update date "
        "could not be checked, so whether a renewal would be visible is unknown."
    ),
    RegistrationStatus.ON_FILE_END_DATE_UNKNOWN: (
        "A registration is on file but its end date is missing or unreadable."
    ),
    RegistrationStatus.NONE_ON_FILE: (
        "No registration was found in the checked snapshot. This does not show that one was "
        "required, or that any requirement was violated."
    ),
}


class HpdRegistrationRecord(BaseModel):
    dataset_id: str
    evidence_kind: EvidenceKind = EvidenceKind.ADMINISTRATIVE_RECORD
    source_row_id: str  # Socrata :id; the dataset has no unique business key
    # Every source row this record stands for: itself first, then identical copies
    # (same business fields, different :id) that were collapsed into it.
    source_rows: list[SourceRowRef]
    registration_id: str
    building_id: str | None

    source_boroid: str | None
    source_block: str | None
    source_lot: str | None
    source_bin: str | None
    resolved_bbl: str
    lot_membership: LotMembership
    attribution: Attribution

    address: str | None  # display only, never a join key
    last_registration_date: DateValue
    registration_end_date: DateValue
    buildings_on_lot_with_this_registration: int  # other lots are not checked

    quality_flags: list[QualityFlag]
    redaction: RedactionInfo
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None


class BuildingRegistration(BaseModel):
    """The registration picture for one HPD building ID, from every row on file for it."""

    building_id: str | None
    attribution: Attribution
    status: RegistrationStatus
    note: str
    registration_ids: list[str]
    latest_registration_id: str | None
    latest_last_registration_date: DateValue | None
    latest_end_date: DateValue | None
    rows_on_file: int
    status_basis: dict  # dates the status was judged with


class RegistrationSummary(BaseModel):
    data_as_of: str | None
    freshness: DataFreshness | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    total_rows: int  # normalized records (after collapsing identical copies)
    source_rows: int  # rows the source returned; equals the source count when complete
    identical_rows_collapsed: int  # source_rows - total_rows
    registration_ids: int
    buildings: list[BuildingRegistration]
    requested_building: BuildingRegistration | None  # None at tax-lot scope
    quality_flag_counts: dict[str, int]


class RegistrationSourceResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[HpdRegistrationRecord] = Field(default_factory=list)
    partial: bool = False
    summary: RegistrationSummary | None = None
    freshness: DataFreshness | None = None
    provenance: RetrievalProvenance

    @model_validator(mode="after")
    def _enforce_invariants(self) -> RegistrationSourceResult:
        building_only = {Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT}
        bad = [r.source_row_id for r in self.records if r.attribution in building_only]
        if self.summary is not None:
            bad += [
                f"building {b.building_id}"
                for b in self.summary.buildings
                if b.attribution in building_only
            ]
        check_source_invariants(self, bad)
        check_source_rows(self, self.records)
        if (
            self.summary is not None
            and self.join_scope is not JoinScope.BUILDING
            and self.summary.requested_building is not None
        ):
            raise ValueError("a lot-level result cannot name a requested building")
        return self


# ---- Registration contacts (Task 3, step 3) ------------------------------------------


class ContactRole(StrEnum):
    """HPD's registration contact type, mapped one to one. Unknown types stay `other`
    with the raw value kept. A role is what was registered with HPD, not independent
    proof of legal title or of who actually manages the building."""

    CORPORATE_OWNER = "corporate_owner"
    INDIVIDUAL_OWNER = "individual_owner"
    JOINT_OWNER = "joint_owner"
    HEAD_OFFICER = "head_officer"
    OFFICER = "officer"
    SHAREHOLDER = "shareholder"
    AGENT = "agent"
    SITE_MANAGER = "site_manager"
    LESSEE = "lessee"
    OTHER = "other"


class ContactAssociation(StrEnum):
    """Which buildings a contact's registration covers. Contacts belong to a registration,
    so they are associated with every building it covers, never one exclusively."""

    REQUESTED_BUILDING_ONLY = "requested_building_only"  # coverage checked: only this one
    SHARED_INCLUDING_REQUESTED_BUILDING = "shared_including_requested_building"
    REQUESTED_BUILDING_SHARING_UNKNOWN = "requested_building_sharing_unknown"  # unchecked
    OTHER_BUILDINGS_ONLY = "other_buildings_only"
    LOT_LEVEL = "lot_level"  # tax-lot scope
    UNATTRIBUTED = "unattributed"


ASSOCIATION_NOTE = {
    ContactAssociation.REQUESTED_BUILDING_ONLY: (
        "The registration covers only the requested building (checked across all HPD "
        "registration rows for this registration ID)."
    ),
    ContactAssociation.SHARED_INCLUDING_REQUESTED_BUILDING: (
        "The registration covers the requested building and other buildings. Its contacts "
        "are registered for all of them, not specifically for the requested building."
    ),
    ContactAssociation.REQUESTED_BUILDING_SHARING_UNKNOWN: (
        "The registration covers the requested building; whether it also covers buildings "
        "on other lots could not be checked."
    ),
    ContactAssociation.OTHER_BUILDINGS_ONLY: (
        "The registration covers other buildings on this lot, not the requested building."
    ),
    ContactAssociation.LOT_LEVEL: "About the tax lot, not any one building.",
    ContactAssociation.UNATTRIBUTED: "The registration could not be tied to a building.",
}


ROLE_NOTE = (
    "Registered with HPD under this role. A registration is the owner's filing with HPD; "
    "it is not independent proof of legal title or of who actually manages the building."
)


class HpdRegistrationContact(BaseModel):
    dataset_id: str
    evidence_kind: EvidenceKind = EvidenceKind.ADMINISTRATIVE_RECORD
    source_row_id: str  # Socrata :id; registrationcontactid is not unique citywide
    source_rows: list[SourceRowRef]  # itself first, then collapsed identical copies
    registration_contact_id: str | None
    registration_id: str
    association: ContactAssociation  # via the registration's covered buildings
    registration_shared: bool | None  # None: off-lot coverage could not be checked
    role: ContactRole
    role_raw: str | None
    role_note: str = ROLE_NOTE
    registration_description: str | None  # HPD's contactdescription, e.g. CORP, CONDO
    corporation_name: str | None  # public
    names_a_person: bool  # a person's name is on file (withheld)
    public_city: str | None  # only for contacts that name no person
    public_state: str | None
    public_zip: str | None
    attribution: Attribution  # from the registration's buildings
    withheld_fields: list[str]  # names only, never values
    redaction: RedactionInfo
    original_digest: str
    digest_basis: DigestBasis
    redacted_sha256: str
    query_url: str
    fetched_at: str
    data_as_of: str | None


class RegistrationContacts(BaseModel):
    registration_id: str
    attribution: Attribution  # THIS_BUILDING: covers the requested building (maybe others)
    association: ContactAssociation
    association_note: str
    shared_registration: bool | None  # None: only one building known, coverage unchecked
    building_ids_on_lot: list[str]  # HPD building IDs on this lot with this registration
    # Every HPD building ID with this registration ID in the registrations dataset,
    # citywide. None when that coverage lookup did not complete.
    covered_building_ids: list[str] | None
    covered_buildings_off_lot: int | None
    contacts: int
    roles: dict[str, int]
    corporate_names_by_role: dict[str, list[str]]
    persons_named_withheld: int


class ContactSummary(BaseModel):
    data_as_of: str | None
    freshness: DataFreshness | None
    join_scope: JoinScope | None
    building_specific_claims_allowed: bool
    registrations_requested: int
    registrations_checked: int  # batches completed for these ids
    total_contacts: int  # normalized records (after collapsing identical copies)
    source_rows: int  # rows the source returned
    identical_rows_collapsed: int
    coverage_status: str  # complete / incomplete / unavailable / not_needed
    by_registration: list[RegistrationContacts]
    requested_building: list[RegistrationContacts] | None  # None at tax-lot scope
    withheld_field_counts: dict[str, int]
    role_counts: dict[str, int]
    duplicate_contact_ids: int


class ContactSourceResult(BaseModel):
    source: str
    dataset_id: str
    status: RetrievalStatus
    status_detail: str
    join_scope: JoinScope | None
    resolved_bbl: str | None
    resolved_bin: str | None
    records: list[HpdRegistrationContact] = Field(default_factory=list)
    partial: bool = False
    summary: ContactSummary | None = None
    freshness: DataFreshness | None = None
    provenance: RetrievalProvenance  # aggregate over batches
    batch_provenance: list[RetrievalProvenance] = Field(default_factory=list)
    coverage_provenance: list[RetrievalProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> ContactSourceResult:
        building_only = {Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT}
        bad = [r.source_row_id for r in self.records if r.attribution in building_only]
        check_source_invariants(self, bad)
        check_source_rows(self, self.records)
        if self.summary is not None:
            for c in self.summary.by_registration:
                only = ContactAssociation.REQUESTED_BUILDING_ONLY
                if c.association is only and (
                    c.covered_building_ids is None or len(c.covered_building_ids) != 1
                ):
                    raise ValueError("'requested building only' needs complete coverage")
                shared = ContactAssociation.SHARED_INCLUDING_REQUESTED_BUILDING
                if (c.association is shared) != (
                    c.attribution is Attribution.THIS_BUILDING and c.shared_registration is True
                ):
                    raise ValueError("shared association inconsistent with coverage")
        if self.join_scope is not JoinScope.BUILDING and self.summary is not None:
            if self.summary.requested_building is not None:
                raise ValueError("a lot-level result cannot name a requested building")
        for r in self.records:  # defence in depth: nothing personal in public records
            if r.names_a_person and (r.public_city or r.public_zip):
                raise ValueError("address detail published for a contact naming a person")
        return self
