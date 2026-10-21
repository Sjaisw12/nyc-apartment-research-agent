"""Typed result of an address resolution."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class Outcome(StrEnum):
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    NO_RELIABLE_MATCH = "no_reliable_match"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ResolutionLevel(StrEnum):
    BUILDING = "building"  # BBL verified and BIN cross-verified in two sources
    TAX_LOT = "tax_lot"  # BBL verified; BIN unresolved or single-source


class BinEvidence(StrEnum):
    CROSS_VERIFIED = "cross_verified"  # PAD BIN, and Footprints puts it on the same lot
    PAD_ONLY = "pad_only"  # PAD BIN, Footprints has no record: single source
    PLACEHOLDER = "placeholder"  # PAD BIN is n000000 (unassigned/unknown)
    MISSING = "missing"  # PAD gave no BIN
    LOT_CONFLICT = "lot_conflict"  # Footprints puts the PAD BIN on a different lot
    NOT_CHECKED = "not_checked"  # Footprints request failed


class NumberMatch(StrEnum):
    EXACT = "exact"
    HYPHEN_ONLY = "hyphen_only"
    MISMATCH = "mismatch"


class StreetMatch(StrEnum):
    EXACT = "exact"
    NEAR = "near"  # small typo; never auto-accepted
    MISMATCH = "mismatch"


class BoroughMatch(StrEnum):
    STATED_MATCH = "stated_match"
    INFERRED = "inferred"
    STATED_CONFLICT = "stated_conflict"
    UNKNOWN = "unknown"


class Reason(StrEnum):
    INPUT_EMPTY = "INPUT_EMPTY"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    MISSING_HOUSE_NUMBER = "MISSING_HOUSE_NUMBER"
    NOT_NYC = "NOT_NYC"
    NO_CANDIDATES = "NO_CANDIDATES"
    HOUSE_NUMBER_MISMATCH = "HOUSE_NUMBER_MISMATCH"
    HOUSE_NUMBER_HYPHEN = "HOUSE_NUMBER_HYPHEN"
    STREET_TYPO = "STREET_TYPO"
    BOROUGH_STATED = "BOROUGH_STATED"
    BOROUGH_INFERRED = "BOROUGH_INFERRED"
    BOROUGH_CONFLICT = "BOROUGH_CONFLICT"
    BOROUGH_MULTIPLE = "BOROUGH_MULTIPLE"
    BOROUGH_CHECK_INCOMPLETE = "BOROUGH_CHECK_INCOMPLETE"
    ZIP_CONFLICT = "ZIP_CONFLICT"
    MULTIPLE_BUILDINGS = "MULTIPLE_BUILDINGS"
    BIN_CROSS_VERIFIED = "BIN_CROSS_VERIFIED"
    BIN_PAD_ONLY = "BIN_PAD_ONLY"
    BIN_PLACEHOLDER = "BIN_PLACEHOLDER"
    BIN_MISSING = "BIN_MISSING"
    BIN_LOT_CONFLICT = "BIN_LOT_CONFLICT"
    FOOTPRINTS_UNAVAILABLE = "FOOTPRINTS_UNAVAILABLE"
    GEOSEARCH_UNAVAILABLE = "GEOSEARCH_UNAVAILABLE"


class CandidateCheck(BaseModel):
    """One PAD address record from GeoSearch and how it fared against the input."""

    label: str
    aliases: list[str] = Field(default_factory=list)
    house_number: str | None
    street: str | None
    borough: str | None
    postalcode: str | None
    bbl: str
    pad_bin: str | None  # exactly what PAD returned (may be a placeholder)
    pad_bin_status: str  # assigned | unassigned_placeholder | missing
    number: NumberMatch
    street_match: StreetMatch
    borough_match: BoroughMatch
    zip_conflict: bool = False
    strict: bool = False
    weak: bool = False
    found_by: list[str] = Field(default_factory=list)  # which queries returned it


class ResolvedIdentity(BaseModel):
    bbl: str
    bin: str | None  # None unless PAD gave an assigned BIN that is not in conflict
    bin_evidence: BinEvidence
    label: str
    aliases: list[str]
    borough: str
    borough_source: str  # stated | inferred
    footprint_bins_on_lot: list[str] | None = None  # context only; never used as the BIN
    footprints_lots_for_bin: list[str] | None = None


class ParsedInput(BaseModel):
    raw: str
    house_number: str | None
    street: str | None
    street_normalized: str | None
    borough: str | None
    zip_code: str | None
    state: str | None
    notes: list[str]


class ResolutionResult(BaseModel):
    outcome: Outcome
    explanation: str
    reasons: list[Reason]
    input: ParsedInput
    resolution_level: ResolutionLevel | None = None  # only for verified
    building: ResolvedIdentity | None = None  # only for verified
    options: list[ResolvedIdentity] = Field(default_factory=list)  # only for ambiguous
    candidates: list[CandidateCheck] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)

    @computed_field
    @property
    def permitted_join_keys(self) -> list[str]:
        """Which keys later phases may use to attach records to this result."""
        if self.outcome is not Outcome.VERIFIED or self.building is None:
            return []
        if self.resolution_level is ResolutionLevel.BUILDING:
            return ["bbl", "bin"]
        return ["bbl"]

    @computed_field
    @property
    def building_specific_findings_allowed(self) -> bool:
        return self.resolution_level is ResolutionLevel.BUILDING
