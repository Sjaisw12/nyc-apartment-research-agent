"""HPD Housing Maintenance Code Violations (wvxf-dwi5): retrieve, normalize, summarize.

Entry point: `retrieve_hpd_violations(resolution, client)`.

Measured facts this module is built around (live queries, 2026-10-21; see
docs/phase2_task1_hpd_violations_design.md):
- 10,665 rows have no bbl and no bin but do have boroid/block/lot, and 1,143 rows have
  bbl "0". So the lot is queried by bbl OR boroid+block+lot (approved decision D1).
- violationid is unique across the dataset.
- violationstatus (Open/Close) and currentstatus (23 values) occasionally contradict.
- Dates are floating timestamps (no time zone); some are impossible (year 0217, 9999).
- Class I rows are information/orders, including vacate orders and the Alternative
  Enforcement Program; they are kept apart from hazard classes A/B/C.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import secrets
from collections import Counter
from typing import Any

from ..config import Settings, get_settings
from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..resolver.models import ResolutionResult
from ..sources.socrata_pager import PagedFetch, fetch_all
from .attribution import attribute, bin_flags, check_lot_identity
from .evidence import EvidenceStore
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    CONFLICT_FLAGS,
    Attribution,
    BucketSummary,
    DateValue,
    DigestBasis,
    JoinScope,
    LocationScope,
    NormalizedViolation,
    OrderCategory,
    PrivacySummary,
    QualityFlag,
    RawEvidence,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
    SourceResult,
    ViolationSummary,
)
from .redaction import (
    canonical_sha256,
    event_from_sanitized,
    event_is_empty,
    is_sanitized,
    keyed_digest,
    redact_row_event,
)

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "wvxf-dwi5"
ORDER_BY = "violationid"

REQUIRED_FIELDS = (
    "violationid",
    "boroid",
    "block",
    "lot",
    "class",
    "inspectiondate",
    "novdescription",
    "currentstatus",
    "violationstatus",
)

# ---- Dates ---------------------------------------------------------------------------
# Events that cannot be in the future relative to the data snapshot.
EVENT_DATES = (
    "inspectiondate",
    "approveddate",
    "novissueddate",
    "certifieddate",
    "currentstatusdate",
)
# Deadlines may legitimately be in the future.
DEADLINE_DATES = (
    "originalcertifybydate",
    "originalcorrectbydate",
    "newcertifybydate",
    "newcorrectbydate",
)
EARLIEST_PLAUSIBLE = dt.date(1900, 1, 1)
LATEST_DEADLINE = dt.date(2100, 1, 1)
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T[\d:.]+)?$")

# ---- Status --------------------------------------------------------------------------
# Only clear contradictions are flagged. Other mixed combinations exist in the data
# (e.g. 57,143 rows "Close" + "VIOLATION WILL BE REINSPECTED") but their meaning is not
# documented, so they are kept as-is and not judged.
CURRENT_STATUS_TERMINAL = frozenset({"VIOLATION CLOSED", "VIOLATION DISMISSED"})
CURRENT_STATUS_CLEARLY_OPEN = frozenset({"VIOLATION OPEN", "VIOLATION REOPEN", "NOV SENT OUT"})

# ---- Class I orders --------------------------------------------------------------------
# Order numbers seen on Class I rows (live group-by, 2026-10-21), with their descriptions.
ORDER_NUMBER_CATEGORY = {
    "765": OrderCategory.VACATE_ORDER,  # § 27-2142 apartments vacated by HPD
    "1067": OrderCategory.VACATE_ORDER,  # § 27-2142 premises or apartments vacated
    "767": OrderCategory.VACATE_ORDER,  # § 27-2089 premises vacated by HPD order
    "766": OrderCategory.VACANT_PREMISES_ORDER,  # § 27-2089 vacant 60+ days, no reoccupancy
    "759": OrderCategory.RELOCATION_ORDER,  # § 27-2091 commissioner's order, relocation
    "760": OrderCategory.RELOCATION_ORDER,
    "729": OrderCategory.ALTERNATIVE_ENFORCEMENT_PROGRAM,  # § 27-2153 AEP selection
    "730": OrderCategory.UNDERLYING_CONDITIONS_ORDER,  # § 27-2091(c) underlying conditions
    "780": OrderCategory.REGISTRATION_NONCOMPLIANCE,  # § 27-2107 failed to register
    "771": OrderCategory.REGISTRATION_NONCOMPLIANCE,  # § 27-2096 false/incomplete reg.
}
# Fallback when the order number is unknown: keywords from the same descriptions.
ORDER_KEYWORDS = (
    (re.compile(r"\bVACATED\b"), OrderCategory.VACATE_ORDER),
    (re.compile(r"VACANT AND UNTENANTED"), OrderCategory.VACANT_PREMISES_ORDER),
    (re.compile(r"\bRELOCATION\b"), OrderCategory.RELOCATION_ORDER),
    (re.compile(r"ALTERNATIVE ENFORCEMENT"), OrderCategory.ALTERNATIVE_ENFORCEMENT_PROGRAM),
    (re.compile(r"UNDERLYING CONDITIONS"), OrderCategory.UNDERLYING_CONDITIONS_ORDER),
    (re.compile(r"\bREGISTRATION\b"), OrderCategory.REGISTRATION_NONCOMPLIANCE),
)
CONSEQUENTIAL_ORDERS = frozenset(
    {
        OrderCategory.VACATE_ORDER,
        OrderCategory.VACANT_PREMISES_ORDER,
        OrderCategory.RELOCATION_ORDER,
        OrderCategory.ALTERNATIVE_ENFORCEMENT_PROGRAM,
        OrderCategory.UNDERLYING_CONDITIONS_ORDER,
    }
)
# What each order record does and does not establish (approved adjustment 3: an open AEP
# order record is not confirmation of current AEP enrollment).
ORDER_INTERPRETATION = {
    OrderCategory.ALTERNATIVE_ENFORCEMENT_PROGRAM: (
        "HPD order record stating the building was selected for the Alternative Enforcement "
        "Program. It is not confirmation of current enrollment; HPD publishes enrollment "
        "status separately (Buildings Selected for AEP, dataset hcir-3275), which this "
        "task does not query."
    ),
    OrderCategory.VACATE_ORDER: (
        "HPD order record stating units or premises were vacated. It does not confirm the "
        "units are vacant or the order is still in force today."
    ),
    OrderCategory.VACANT_PREMISES_ORDER: (
        "HPD order record about vacant, untenanted premises. It does not confirm the "
        "building is vacant today."
    ),
    OrderCategory.RELOCATION_ORDER: (
        "HPD commissioner's order record about relocation. It does not confirm anyone is "
        "displaced today."
    ),
    OrderCategory.UNDERLYING_CONDITIONS_ORDER: (
        "HPD order record under the underlying-conditions provision. It does not confirm "
        "the order is still active."
    ),
    OrderCategory.REGISTRATION_NONCOMPLIANCE: (
        "HPD record of a registration violation at the time it was issued. It does not "
        "confirm the current registration status."
    ),
}
KNOWN_CLASSES = frozenset({"A", "B", "C", "I"})

# ---- Location scope --------------------------------------------------------------------
_UNIT_TEXT = re.compile(r"\b(?:APTS?|APARTMENTS?|UNITS?)\s+(?:\[REDACTED\]|#?\d)", re.I)
_BUILDING_WIDE_TEXT = re.compile(
    r"ENTIRE BUILDING|THROUGHOUT THE BUILDING|BUILDING-WIDE|ENTIRE PREMISES|THE PREMISES", re.I
)
_COMMON_AREA_TEXT = re.compile(
    r"PUBLIC HALL|PUB HALL|HALLWAY|CELLAR|BASEMENT|BOILER ROOM|ROOF|STAIR|LOBBY|"
    r"COURTYARD|\bYARD\b|ELEVATOR|BULKHEAD|FIRE ESCAPE|VESTIBULE|LAUNDRY ROOM|"
    r"ENTRANCE DOOR TO BUILDING|FRONT ENTRANCE",
    re.I,
)


def parse_date(
    raw: object, *, deadline: bool, as_of: dt.date
) -> tuple[DateValue, QualityFlag | None]:
    if raw is None or str(raw).strip() == "":
        return DateValue(raw=None, value=None), None
    text = str(raw).strip()
    m = _DATE_RE.match(text)
    if not m:
        return DateValue(raw=text, value=None), QualityFlag.UNPARSEABLE_DATE
    try:
        value = dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return DateValue(raw=text, value=None), QualityFlag.UNPARSEABLE_DATE
    latest = LATEST_DEADLINE if deadline else as_of + dt.timedelta(days=1)
    if value < EARLIEST_PLAUSIBLE or value > latest:
        return DateValue(raw=text, value=None), QualityFlag.IMPLAUSIBLE_DATE
    return DateValue(raw=text, value=value), None


def status_of(
    violation_status: object, current_status: object
) -> tuple[bool | None, list[QualityFlag]]:
    vs = str(violation_status).strip().upper() if violation_status else ""
    cs = str(current_status).strip().upper() if current_status else ""
    if vs == "OPEN":
        is_open: bool | None = True
    elif vs == "CLOSE":
        is_open = False
    else:
        return None, [QualityFlag.STATUS_UNKNOWN]
    disagree = (is_open and cs in CURRENT_STATUS_TERMINAL) or (
        not is_open and cs in CURRENT_STATUS_CLEARLY_OPEN
    )
    return is_open, [QualityFlag.STATUS_FIELDS_DISAGREE] if disagree else []


def order_category(violation_class: str | None, order_number: object, description: str | None):
    if violation_class != "I":
        return None
    by_number = ORDER_NUMBER_CATEGORY.get(str(order_number).strip() if order_number else "")
    if by_number:
        return by_number
    text = (description or "").upper()
    for pattern, category in ORDER_KEYWORDS:
        if pattern.search(text):
            return category
    return OrderCategory.OTHER


def location_scope(row: dict[str, Any]) -> tuple[LocationScope, str]:
    """Evidence-based scope. A missing apartment field alone proves nothing (correction 1)."""
    apt = row.get("apartment")
    text = row.get("novdescription") or ""
    if apt not in (None, ""):
        return LocationScope.UNIT, "apartment field present"
    if _UNIT_TEXT.search(text):
        return LocationScope.UNIT, "description names an apartment"
    if _BUILDING_WIDE_TEXT.search(text):
        return LocationScope.BUILDING_WIDE, "description names the entire building/premises"
    if _COMMON_AREA_TEXT.search(text):
        return LocationScope.COMMON_AREA, "description names a common area"
    return LocationScope.UNSPECIFIED, "no location evidence in the record"


def _yn(value: object) -> bool | None:
    v = str(value).strip().upper() if value is not None else ""
    return {"Y": True, "N": False}.get(v)


def _s(value: object) -> str | None:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def normalize_row(
    row: dict[str, Any],
    plan: LotQueryPlan,
    *,
    query_url: str,
    fetched_at: str,
    data_as_of: str | None,
    as_of: dt.date,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
    extra_flags: tuple[QualityFlag, ...] = (),
) -> NormalizedViolation:
    flags: list[QualityFlag] = list(extra_flags)

    membership, lot_flags = check_lot_identity(
        plan, row.get("bbl"), row.get("boroid"), row.get("block"), row.get("lot")
    )
    flags += lot_flags
    bin_status, b_flags = bin_flags(row.get("bin"))
    flags += b_flags
    attribution = attribute(plan, membership, bin_status, row.get("bin"))

    is_open, s_flags = status_of(row.get("violationstatus"), row.get("currentstatus"))
    flags += s_flags

    dates: dict[str, DateValue] = {}
    for name in EVENT_DATES + DEADLINE_DATES:
        value, flag = parse_date(row.get(name), deadline=name in DEADLINE_DATES, as_of=as_of)
        dates[name] = value
        if flag and flag not in flags:
            flags.append(flag)

    vclass = _s(row.get("class"))
    if vclass not in KNOWN_CLASSES:
        flags.append(QualityFlag.UNKNOWN_CLASS)
    scope, basis = location_scope(row)

    upstream = event_from_sanitized(row)  # markers already present (sanitized fixture)
    public_row, here = redact_row_event(row)
    redacted_fields = here["fields_masked"] + here["text_fields_redacted"]
    redaction = _redaction_info(here, upstream)
    description_redacted = "novdescription" in redaction.text_fields_redacted
    if description_redacted:
        flags.append(QualityFlag.UNIT_DETAIL_REDACTED)
    category = order_category(vclass, row.get("ordernumber"), row.get("novdescription"))

    # Live rows are originals; a replayed fixture row is already sanitized.
    digest_basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    original_sha = canonical_sha256(row)  # private: evidence store only
    original_digest = keyed_digest(row, digest_key)  # public, not brute-forceable
    redacted_sha = canonical_sha256(public_row)
    if evidence is not None:
        evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=str(row.get("violationid")),
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=digest_basis,
                redacted_sha256=redacted_sha,
                redacted_fields=redacted_fields,
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
            )
        )

    return NormalizedViolation(
        dataset_id=DATASET_ID,
        violation_id=str(row.get("violationid")),
        nov_id=_s(row.get("novid")),
        order_number=_s(row.get("ordernumber")),
        building_id=_s(row.get("buildingid")),
        registration_id=_s(row.get("registrationid")),
        source_bbl=_s(row.get("bbl")),
        source_bin=_s(row.get("bin")),
        source_boroid=_s(row.get("boroid")),
        source_block=_s(row.get("block")),
        source_lot=_s(row.get("lot")),
        resolved_bbl=plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        violation_class=vclass,
        is_informational=vclass == "I",
        order_category=category,
        potentially_consequential=category in CONSEQUENTIAL_ORDERS,
        order_interpretation=ORDER_INTERPRETATION.get(category) if category else None,
        description=public_row.get("novdescription"),
        description_redacted=description_redacted,
        redaction=redaction,
        nov_type=_s(row.get("novtype")),
        rent_impairing=_yn(row.get("rentimpairing")),
        violation_status_raw=_s(row.get("violationstatus")),
        current_status_raw=_s(row.get("currentstatus")),
        current_status_id=_s(row.get("currentstatusid")),
        is_open=is_open,
        dates=dates,
        location_scope=scope,
        location_scope_basis=basis,
        quality_flags=flags,
        original_digest=original_digest,
        digest_basis=digest_basis,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=data_as_of,
    )


def _redaction_info(here: dict, upstream: dict) -> RedactionInfo:
    if not event_is_empty(here):
        applied = "this_run"
    elif not event_is_empty(upstream):
        applied = "upstream"
    else:
        return RedactionInfo(applied="none")
    replacements: dict[str, int] = dict(upstream["text_replacements"])
    for k, v in here["text_replacements"].items():
        replacements[k] = replacements.get(k, 0) + v
    return RedactionInfo(
        applied=applied,
        fields_masked=sorted(set(here["fields_masked"]) | set(upstream["fields_masked"])),
        text_fields_redacted=sorted(
            set(here["text_fields_redacted"]) | set(upstream["text_fields_redacted"])
        ),
        text_replacements=dict(sorted(replacements.items())),
    )


def privacy_summary(records: list[NormalizedViolation]) -> PrivacySummary:
    p = PrivacySummary(rows=len(records))
    for r in records:
        red = r.redaction
        p.rows_with_unit_fields_masked += bool(red.fields_masked)
        p.rows_with_text_redacted += bool(red.text_fields_redacted)
        p.rows_with_any_redaction += red.applied != "none"
        p.rows_redacted_this_run += red.applied == "this_run"
        p.rows_redacted_upstream += red.applied == "upstream"
        for k, v in red.text_replacements.items():
            p.text_replacements_by_kind[k] = p.text_replacements_by_kind.get(k, 0) + v
    p.text_replacements_by_kind = dict(sorted(p.text_replacements_by_kind.items()))
    return p


def normalize_fetch(
    fetch: PagedFetch,
    plan: LotQueryPlan,
    *,
    as_of: dt.date,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
) -> list[NormalizedViolation]:
    """Normalize every fetched row, collapsing identical duplicates by violationid."""
    first_seen: dict[str, tuple[int, str]] = {}  # violationid -> (output index, sha)
    out: list[NormalizedViolation] = []
    data_as_of = fetch.provenance.data_as_of
    for i, row in enumerate(fetch.rows):
        page_index = fetch.row_page[i] if i < len(fetch.row_page) else -1
        page = next((p for p in fetch.provenance.pages if p.index == page_index), None)
        url = page.url if page else ""
        fetched_at = page.fetched_at if page else ""
        vid = str(row.get("violationid"))
        sha = canonical_sha256(row)
        extra: tuple[QualityFlag, ...] = ()
        if vid in first_seen:
            idx, prev_sha = first_seen[vid]
            if prev_sha == sha:
                if QualityFlag.DUPLICATE_ID_SAME_CONTENT not in out[idx].quality_flags:
                    out[idx].quality_flags.append(QualityFlag.DUPLICATE_ID_SAME_CONTENT)
                continue
            # Same ID, different content: keep both, flag both, attribute neither as certain.
            if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT not in out[idx].quality_flags:
                out[idx].quality_flags.append(QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
            extra = (QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT,)
        else:
            first_seen[vid] = (len(out), sha)
        out.append(
            normalize_row(
                row,
                plan,
                query_url=url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
                as_of=as_of,
                digest_key=digest_key,
                evidence=evidence,
                extra_flags=extra,
            )
        )
    # Conflicting copies of one record must not be read as being about this building.
    for r in out:
        if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT in r.quality_flags and r.attribution in (
            Attribution.THIS_BUILDING,
            Attribution.OTHER_BUILDING_ON_LOT,
        ):
            r.attribution = Attribution.UNATTRIBUTED_ON_LOT
    return out


def summarize(
    records: list[NormalizedViolation],
    scope: JoinScope | None,
    data_as_of: str | None,
    as_of: dt.date,
) -> ViolationSummary:
    buckets: dict[Attribution, BucketSummary] = {}
    flag_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    for r in records:
        b = buckets.setdefault(r.attribution, BucketSummary())
        b.records += 1
        if r.is_open is True:
            b.open += 1
        elif r.is_open is False:
            b.closed += 1
        else:
            b.status_unknown += 1
        cls = r.violation_class or "unknown"
        if r.is_informational:
            key = (r.order_category or OrderCategory.OTHER).value
            b.information_order_counts[key] = b.information_order_counts.get(key, 0) + 1
        else:
            b.hazard_class_counts[cls] = b.hazard_class_counts.get(cls, 0) + 1
            if r.is_open:
                b.open_hazard_class_counts[cls] = b.open_hazard_class_counts.get(cls, 0) + 1
        if r.is_open and r.rent_impairing:
            b.open_rent_impairing += 1
        inspected = r.dates.get("inspectiondate")
        if inspected and inspected.value:
            age = (as_of - inspected.value).days
            b.inspected_last_12_months += age <= 365
            b.inspected_last_36_months += age <= 3 * 365
        if r.potentially_consequential:
            b.consequential_orders.append(
                {
                    "violation_id": r.violation_id,
                    "order_category": r.order_category.value if r.order_category else None,
                    "is_open": r.is_open,
                    "current_status": r.current_status_raw,
                    "inspection_date": inspected.raw if inspected else None,
                    "record_type": "hpd_order_record",
                    "confirms_current_program_status": False,
                    "interpretation": r.order_interpretation,
                }
            )
        flag_counts.update(f.value for f in r.quality_flags)
        scope_counts[r.location_scope.value] += 1
    conflicts = {f.value: flag_counts.get(f.value, 0) for f in sorted(CONFLICT_FLAGS)}
    return ViolationSummary(
        data_as_of=data_as_of,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING,
        total_records=len(records),
        by_attribution=buckets,
        conflict_counts=conflicts,
        quality_flag_counts=dict(sorted(flag_counts.items())),
        redacted_descriptions=sum(1 for r in records if r.description_redacted),
        privacy=privacy_summary(records),
        location_scope_counts=dict(sorted(scope_counts.items())),
    )


def lot_where(plan: LotQueryPlan) -> str:
    """bbl OR boroid+block+lot. Every value is an int or a validated BBL (no user text)."""
    return (
        f"bbl='{plan.bbl}' OR "
        f"(boroid={plan.boroid:d} AND block={plan.block:d} AND lot={plan.lot:d})"
    )


def retrieve_hpd_violations(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> SourceResult:
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap

    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return SourceResult(
            source=SOURCE,
            dataset_id=DATASET_ID,
            status=RetrievalStatus.UNSUPPORTED_JOIN,
            status_detail=str(exc),
            join_scope=None,
            resolved_bbl=None,
            resolved_bin=None,
            provenance=RetrievalProvenance(dataset_id=DATASET_ID),
        )

    where = lot_where(plan)
    try:
        fetch = fetch_all(
            client,
            DATASET_ID,
            where=where,
            order_by=ORDER_BY,
            unique_key="violationid",
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
        )
    except SourceError as exc:
        logger.warning("%s unavailable for %s: %s", DATASET_ID, plan.bbl, type(exc).__name__)
        return SourceResult(
            source=SOURCE,
            dataset_id=DATASET_ID,
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"{type(exc).__name__}: the source could not be checked",
            join_scope=plan.scope,
            resolved_bbl=plan.bbl,
            resolved_bin=plan.building_bin,
            provenance=RetrievalProvenance(
                dataset_id=DATASET_ID, where=where, order_by=ORDER_BY, error=str(exc)
            ),
        )

    as_of = _as_of_date(fetch, today)
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    records = normalize_fetch(fetch, plan, as_of=as_of, digest_key=digest_key, evidence=evidence)
    prov = fetch.provenance
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = "the source reports 0 violations for this lot"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = f"{len(records)} violations retrieved; matches the source count"

    return SourceResult(
        source=SOURCE,
        dataset_id=DATASET_ID,
        status=status,
        status_detail=detail,
        join_scope=plan.scope,
        resolved_bbl=plan.bbl,
        resolved_bin=plan.building_bin,
        records=records,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summarize(records, plan.scope, prov.data_as_of, as_of),
        provenance=prov,
    )


_EPHEMERAL_KEY = secrets.token_bytes(32)


def _digest_key(settings: Settings, fetch: PagedFetch) -> bytes:
    if settings.evidence_digest_key is not None:
        return settings.evidence_digest_key.get_secret_value().encode("utf-8")
    fetch.provenance.warnings.append(
        "evidence_digest_key not configured: original_digest uses a per-process key and "
        "is only comparable within this run"
    )
    return _EPHEMERAL_KEY


def _as_of_date(fetch: PagedFetch, today: dt.date | None) -> dt.date:
    """The date used for 'future date' checks and 'last 12 months' windows: the fetch date."""
    if today is not None:
        return today
    for page in fetch.provenance.pages:
        try:
            return dt.datetime.fromisoformat(page.fetched_at).date()
        except ValueError:
            continue
    return dt.date.today()
