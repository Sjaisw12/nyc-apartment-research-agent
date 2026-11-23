"""Stable report schema (v1) shared by the deterministic baseline, the agent, the API,
the frontend and the Markdown/JSON exports.

Design rules
- Every factual finding cites evidence ids that exist in `evidence`.
- A finding with a `fact` is machine-checkable: re-running `fact.where` over `evidence`
  must give exactly `evidence_ids`, and `fact.value` must equal the count (or sum).
- `category` keeps official findings, allegations, enforcement actions, historical events,
  administrative records, uncertainty and unavailable data apart; the UI never merges them.
- `scope` says which building a record may be read as describing. Records on another
  building on the same lot are never shown as this building's.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

DISCLAIMER = (
    "This report summarizes official NYC open records about a building. It is not legal "
    "advice, not an inspection, and not a judgment that an apartment is safe or unsafe to "
    "rent. Records can be incomplete, delayed or wrong; check anything important with the "
    "agency and the landlord."
)


class Category(StrEnum):
    IDENTITY = "identity"
    VERIFIED_RECORD = "verified_record"  # an agency's own finding (e.g. HPD violation)
    COMPLAINT_ALLEGATION = "complaint_allegation"  # what someone reported; not a finding
    ENFORCEMENT_ACTION = "enforcement_action"  # a summons/charge (ECB); not a finding
    HISTORICAL_EVENT = "historical_event"  # an order event in the past; not proof it is current
    ADMINISTRATIVE_RECORD = "administrative_record"  # what an agency has on file
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


class Scope(StrEnum):
    THIS_BUILDING = "this_building"
    LOT_LEVEL = "lot_level"  # tax-lot resolution: the lot, not one building
    OTHER_BUILDING_ON_LOT = "other_building_on_lot"
    UNATTRIBUTED_ON_LOT = "unattributed_on_lot"
    MIXED = "mixed"
    NOT_APPLICABLE = "not_applicable"


class Importance(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class SectionId(StrEnum):
    SUMMARY = "summary"
    IDENTITY = "identity"
    HPD_VIOLATIONS = "hpd_violations"
    HPD_COMPLAINTS = "hpd_complaints"
    DOB_COMPLAINTS = "dob_complaints"
    ECB_VIOLATIONS = "ecb_violations"
    SR311 = "sr311"
    REGISTRATION = "registration"
    ORDERS = "orders"
    OTHER_BUILDINGS = "other_buildings"
    DATA_GAPS = "data_gaps"


SECTION_TITLES: dict[SectionId, str] = {
    SectionId.SUMMARY: "Executive summary",
    SectionId.IDENTITY: "Building identity",
    SectionId.HPD_VIOLATIONS: "HPD housing-code violations",
    SectionId.HPD_COMPLAINTS: "HPD tenant complaints",
    SectionId.DOB_COMPLAINTS: "DOB complaints",
    SectionId.ECB_VIOLATIONS: "DOB / ECB summonses",
    SectionId.SR311: "311 service requests",
    SectionId.REGISTRATION: "Building and registration records",
    SectionId.ORDERS: "Orders and historical events",
    SectionId.OTHER_BUILDINGS: "Records for other buildings on this lot",
    SectionId.DATA_GAPS: "Uncertain or unavailable information",
}


class EvidenceItem(BaseModel):
    """One official record, reduced to fields that are safe to publish."""

    id: str  # e.g. "hpd_violation:9666546"
    source: str  # hpd_violations | hpd_complaints | dob_complaints | ecb_violations | sr311 ...
    agency: str
    dataset_id: str
    dataset_name: str
    record_id: str  # the agency's public identifier (311: a privacy token, not the key)
    evidence_kind: str  # official_finding | reported_allegation | enforcement_action | ...
    attribution: str  # this_building | other_building_on_lot | unattributed_on_lot | lot_level
    title: str
    date: str | None = None  # ISO date of the main event
    date_label: str | None = None
    status: str | None = None
    is_open: bool | None = None
    tags: list[str] = Field(default_factory=list)  # machine-checkable facets (class:C, open...)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    amounts: dict[str, float] = Field(default_factory=dict)  # ECB money as DOB recorded it
    flags: list[str] = Field(default_factory=list)  # data-quality flags from Phase 2
    source_url: str | None = None  # the official API row for this record
    dataset_url: str
    data_as_of: str | None = None
    fetched_at: str | None = None


class FactCheck(BaseModel):
    """A machine-checkable claim: filter `evidence` with `where`, then count or sum."""

    metric: str  # "count" | "sum"
    value: float
    where: dict  # {"source": str, "attribution": [..], "tags_all": [..], "tags_any": [..]}
    field: str | None = None  # amounts key for "sum"


class Finding(BaseModel):
    id: str
    section: SectionId
    category: Category
    scope: Scope
    importance: Importance
    title: str
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    fact: FactCheck | None = None
    caveat: str | None = None


class Section(BaseModel):
    id: SectionId
    title: str
    intro: str
    finding_ids: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    date: str  # ISO date
    year: int
    source: str
    agency: str
    category: Category
    attribution: str
    label: str
    evidence_id: str
    notable: bool = False


class SourceStatus(BaseModel):
    source: str
    agency: str
    dataset_id: str
    dataset_name: str
    dataset_url: str
    status: str  # records_found | no_records_found | source_unavailable | ...
    status_label: str
    status_detail: str
    complete: bool
    records: int
    data_as_of: str | None = None  # dataset version (Socrata truth header) or update date
    retrieved_at: str | None = None  # when we fetched it (demo: when it was recorded)
    stale: bool | None = None
    note: str | None = None


class ResolutionOption(BaseModel):
    bbl: str
    bin: str | None
    label: str
    borough: str
    bin_evidence: str


class OfficialLink(BaseModel):
    label: str
    url: str
    note: str | None = None


class ResolutionView(BaseModel):
    outcome: str  # verified | ambiguous | no_reliable_match | source_unavailable
    explanation: str
    level: str | None = None  # building | tax_lot
    confidence: str  # high | medium | needs_choice | none
    confidence_label: str
    bbl: str | None = None
    bin: str | None = None
    label: str | None = None
    borough: str | None = None
    bin_evidence: str | None = None
    reasons: list[str] = Field(default_factory=list)
    permitted_join_keys: list[str] = Field(default_factory=list)
    footprint_bins_on_lot: list[str] = Field(default_factory=list)
    options: list[ResolutionOption] = Field(default_factory=list)
    user_selected: bool = False
    in_demo_dataset: bool = True
    links: list[OfficialLink] = Field(default_factory=list)


class BuildingOverview(BaseModel):
    hpd_building_id: str | None = None
    hpd_record_status: str | None = None
    legal_stories: int | None = None
    legal_class_a_units: int | None = None
    legal_class_b_units: int | None = None
    dob_building_class: str | None = None
    management_program: str | None = None
    registration_status: str | None = None
    registration_note: str | None = None
    registration_end_date: str | None = None
    buildings_on_lot: int | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Stat(BaseModel):
    label: str
    value: str
    tone: str = "neutral"  # neutral | attention | good
    finding_id: str | None = None
    hint: str | None = None


class AgentParagraph(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)


class RejectedClaim(BaseModel):
    text: str
    reasons: list[str]


class AgentAnalysis(BaseModel):
    status: str  # disabled | completed | fallback | not_applicable
    status_detail: str
    provider: str | None = None
    model: str | None = None
    llm_calls: int = 0
    tool_calls: list[dict] = Field(default_factory=list)
    paragraphs: list[AgentParagraph] = Field(default_factory=list)
    rejected: list[RejectedClaim] = Field(default_factory=list)
    focus_finding_ids: list[str] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    finding_id: str | None
    problem: str


class Verification(BaseModel):
    findings_checked: int = 0
    facts_checked: int = 0
    citations_checked: int = 0
    passed: bool = True
    issues: list[VerificationIssue] = Field(default_factory=list)


class RunStep(BaseModel):
    name: str
    status: str  # done | skipped | failed
    seconds: float = 0.0
    detail: str | None = None


class RunInfo(BaseModel):
    mode: str  # demo | live
    method: str  # deterministic_baseline | langgraph_agent
    elapsed_seconds: float = 0.0
    external_requests: int = 0
    llm_calls: int = 0
    steps: list[RunStep] = Field(default_factory=list)
    limits: dict = Field(default_factory=dict)


ATTENTION_RULES = [
    "Only records filed for this building count. Records for other buildings on the same "
    "tax lot are shown separately and never counted.",
    "Priority follow-up: at least one open HPD Class C violation (HPD's 'immediately "
    "hazardous' category), or a summons DOB still lists as unresolved that DOB marks "
    "hazardous or Class 1.",
    "Some items to check: open Class A or B HPD violations, HPD complaints in the last 12 "
    "months, DOB complaints in the last 3 years, unresolved summonses, or order events "
    "(always treated as historical).",
    "Insufficient information: only the tax lot was verified, or a key source (HPD "
    "violations, HPD complaints, DOB complaints, DOB/ECB summonses) could not be checked, "
    "or was only partly retrieved when nothing else was found.",
    "No priority items found in checked data: none of the above in sources that were "
    "checked completely. It is not a safety rating.",
    "Complaints are reports from residents or others, not proof of a problem. Record counts "
    "alone never raise the label, and no neighbourhood, demographic or personal data is used.",
]

ATTENTION_DISCLAIMER = (
    "Automated evidence summary built from fixed rules, not an AI judgment. It is not an "
    "inspection, a safety rating, legal advice or a prediction, and it does not tell you "
    "whether to rent."
)


class AttentionFact(BaseModel):
    name: str
    fact: FactCheck
    evidence_ids: list[str] = Field(default_factory=list)


class AttentionPoint(BaseModel):
    id: str
    kind: str
    level: str  # priority | check | info
    heading: str
    text: str
    ask: str | None = None
    section: str | None = None  # report section to jump to
    facts: list[AttentionFact] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)


class CoverageSource(BaseModel):
    source: str
    name: str
    state: str  # complete | partial | unavailable | not_applicable | not_checked
    note: str


class DataCoverage(BaseModel):
    level: str  # complete | partial | limited
    label: str
    detail: str
    sources: list[CoverageSource] = Field(default_factory=list)
    oldest_data_as_of: str | None = None
    newest_data_as_of: str | None = None


class AttentionSummary(BaseModel):
    label: str
    label_text: str
    label_meaning: str
    headline: str
    reasons: list[str] = Field(default_factory=list)
    points: list[AttentionPoint] = Field(default_factory=list)
    coverage: DataCoverage
    next_steps: list[str] = Field(default_factory=list)
    verify_links: list[OfficialLink] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=lambda: list(ATTENTION_RULES))
    generated_by: str = "automated_evidence_summary"
    disclaimer: str = ATTENTION_DISCLAIMER


class ResearchReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str
    generated_at: str
    input_address: str
    disclaimer: str = DISCLAIMER
    demo_notice: str | None = None
    resolution: ResolutionView
    building: BuildingOverview | None = None
    headline: str
    stats: list[Stat] = Field(default_factory=list)
    key_finding_ids: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    questions_to_ask: list[str] = Field(default_factory=list)
    attention: AttentionSummary | None = None
    limitations: list[str] = Field(default_factory=list)
    agent: AgentAnalysis | None = None
    verification: Verification = Field(default_factory=Verification)
    run: RunInfo

    def finding(self, fid: str) -> Finding | None:
        return next((f for f in self.findings if f.id == fid), None)

    def evidence_index(self) -> dict[str, EvidenceItem]:
        return {e.id: e for e in self.evidence}
