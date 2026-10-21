"""HPD Housing Maintenance Code Complaints and Problems (ygpa-z7cr), Phase 2 Task 2.

Entry point: `retrieve_hpd_complaints(resolution, client)`.

Reuses Task 1 unchanged: join policy, verified pagination, lot-identity and BIN
attribution, redaction with reproducible events, keyed digests, evidence store.

New here (measured live 2026-10-21, see docs/phase2_task2_hpd_complaints_design.md):
- Rows are problems; `complaint_id` groups them. Counts are always given for both.
- The lot is queried by bbl OR borough *name* + block + lot (37,466 rows have no bbl).
- HPD's disposition lives in free text (`status_description`). It is classified by
  phrases from the templates seen in the data; anything else is `other`, with the
  sanitized text kept. A disposition never identifies a specific violation.
- `complaint_anonymous_flag` and `unique_key` (311 request number) are withheld from
  public output and kept only in the evidence store.
- Data dictionary: "A complaint is closed when all associated problems are closed", so
  a CLOSE complaint with an OPEN problem is flagged as a status disagreement.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from typing import Any

from ..config import Settings, get_settings
from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..resolver.models import ResolutionResult
from ..sources.socrata_pager import PagedFetch, fetch_all
from .attribution import attribute, bin_flags, check_lot_identity
from .complaint_models import (
    ClassificationCoverage,
    ComplaintAttribution,
    ComplaintBucket,
    ComplaintGroup,
    ComplaintOutcome,
    ComplaintSourceResult,
    ComplaintSummary,
    HpdDisposition,
    NormalizedComplaintProblem,
    ProblemBucket,
)
from .evidence import EvidenceStore
from .hpd_violations import (
    _as_of_date,
    _digest_key,
    _redaction_info,
    _s,
    parse_date,
    privacy_summary,
)
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    CONFLICT_FLAGS,
    Attribution,
    DateValue,
    DigestBasis,
    JoinScope,
    LocationScope,
    LotMembership,
    QualityFlag,
    RawEvidence,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import (
    canonical_sha256,
    event_from_sanitized,
    is_sanitized,
    keyed_digest,
    redact_row_event,
    withhold_fields,
)

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "ygpa-z7cr"
ORDER_BY = "problem_id"
REQUIRED_FIELDS = (
    "problem_id",
    "complaint_id",
    "borough",
    "block",
    "lot",
    "received_date",
    "complaint_status",
    "problem_status",
    "major_category",
    "type",
    "unit_type",
)
BOROUGH_NAMES = {1: "MANHATTAN", 2: "BRONX", 3: "BROOKLYN", 4: "QUEENS", 5: "STATEN ISLAND"}
BOROUGH_IDS = {v: k for k, v in BOROUGH_NAMES.items()}
DATE_FIELDS = ("received_date", "complaint_status_date", "problem_status_date")

# ---- Disposition templates -----------------------------------------------------------
# Phrases from the status_description templates observed in the data on 2026-10-21
# (24 distinct texts on the five test lots). Order matters: more specific phrases first.
# These are NOT documented by HPD; the data dictionary only says "Status description".
DUPLICATE_PREFIX = "MORE THAN ONE COMPLAINT WAS RECEIVED FOR THIS BUILDING-WIDE CONDITION"
OUTCOME_RULES: tuple[tuple[str, ComplaintOutcome], ...] = (
    ("VIOLATIONS WERE PREVIOUSLY ISSUED", ComplaintOutcome.VIOLATIONS_PREVIOUSLY_ISSUED),
    ("HEAT WAS NOT REQUIRED AT THE TIME OF THE INSPECTION", ComplaintOutcome.NO_VIOLATION_FOUND),
    ("A VIOLATION WAS ISSUED", ComplaintOutcome.VIOLATIONS_ISSUED),
    ("NOT ABLE TO GAIN ACCESS", ComplaintOutcome.NO_ACCESS),
    ("UNABLE TO ACCESS THE ROOMS", ComplaintOutcome.NO_ACCESS),
    ("UNABLE TO COMPLETE THE INSPECTION", ComplaintOutcome.INSPECTION_NOT_COMPLETED),
    ("NO VIOLATIONS WERE ISSUED", ComplaintOutcome.NO_VIOLATION_FOUND),
    ("DID NOT VIOLATE THE HOUSING LAWS", ComplaintOutcome.NO_VIOLATION_FOUND),
    ("VIOLATIONS WERE ISSUED", ComplaintOutcome.VIOLATIONS_ISSUED),
    (
        "VERIFIED THAT THE FOLLOWING CONDITIONS WERE CORRECTED",
        ComplaintOutcome.CORRECTED_PER_CONTACT,
    ),
    ("INDICATED THAT THE CONDITION WAS CORRECTED", ComplaintOutcome.CORRECTED_PER_CONTACT),
    ("HEAT AND HOT WATER HAD BEEN RESTORED", ComplaintOutcome.RESTORED_PER_CONTACT),
    ("COMPLAINT CONDITIONS ARE STILL OPEN", ComplaintOutcome.OPEN_OWNER_NOTIFIED),
)

UNIT_TYPE_SCOPE = {
    "APARTMENT": LocationScope.UNIT,
    "BUILDING-WIDE": LocationScope.BUILDING_WIDE,
    "BUILDING": LocationScope.BUILDING_WIDE,
    "PUBLIC AREA": LocationScope.COMMON_AREA,
    "PUBLIC PARTS": LocationScope.COMMON_AREA,
}


def classify_text(text: str | None) -> ComplaintOutcome:
    if not text or not text.strip():
        return ComplaintOutcome.NO_TEXT
    upper = " ".join(text.upper().split())
    for phrase, outcome in OUTCOME_RULES:
        if phrase in upper:
            return outcome
    return ComplaintOutcome.OTHER


def disposition(text: str | None) -> HpdDisposition:
    upper = " ".join((text or "").upper().split())
    if upper.startswith(DUPLICATE_PREFIX):
        rest = upper[len(DUPLICATE_PREFIX) :]
        return HpdDisposition(
            outcome=ComplaintOutcome.DUPLICATE_OF_BUILDING_WIDE,
            initial_complaint_outcome=classify_text(rest),
            text=text,
        )
    return HpdDisposition(outcome=classify_text(text), text=text)


def _open(value: object) -> bool | None:
    v = str(value).strip().upper() if value is not None else ""
    return {"OPEN": True, "CLOSE": False}.get(v)


def _yn(value: object) -> bool | None:
    v = str(value).strip().upper() if value is not None else ""
    return {"Y": True, "N": False}.get(v)


def _borough_contradicts_bbl(boroid: int | None, source_bbl: object) -> bool:
    """True when the stated borough and the BBL's borough digit are both known and differ."""
    bbl = str(source_bbl).strip() if source_bbl is not None else ""
    return boroid is not None and len(bbl) == 10 and bbl[0].isdigit() and int(bbl[0]) != boroid


def lot_where(plan: LotQueryPlan) -> str:
    """bbl OR borough-name + block + lot. All values come from the validated plan."""
    return (
        f"bbl={int(plan.bbl):d} OR (borough='{BOROUGH_NAMES[plan.boroid]}' "
        f"AND block={plan.block:d} AND lot={plan.lot:d})"
    )


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
) -> NormalizedComplaintProblem:
    flags: list[QualityFlag] = []
    borough = _s(row.get("borough"))
    boroid = BOROUGH_IDS.get(borough.upper()) if borough else None
    membership, lot_flags = check_lot_identity(
        plan, row.get("bbl"), boroid, row.get("block"), row.get("lot")
    )
    flags += lot_flags
    if membership is LotMembership.CONFIRMED and _borough_contradicts_bbl(boroid, row.get("bbl")):
        # Block/lot unusable (e.g. 0/0) but the stated borough points elsewhere: the row's
        # own identifiers disagree, so attribution is blocked (approved correction 5).
        flags.append(QualityFlag.LOT_IDENTIFIER_CONFLICT)
        membership = LotMembership.CONFLICTING
    bin_status, b_flags = bin_flags(row.get("bin"))
    flags += b_flags
    attribution = attribute(plan, membership, bin_status, row.get("bin"))

    complaint_open = _open(row.get("complaint_status"))
    problem_open = _open(row.get("problem_status"))
    if complaint_open is None or problem_open is None:
        flags.append(QualityFlag.STATUS_UNKNOWN)
    elif complaint_open is False and problem_open is True:
        flags.append(QualityFlag.STATUS_FIELDS_DISAGREE)

    dates: dict[str, DateValue] = {}
    for name in DATE_FIELDS:
        value, flag = parse_date(row.get(name), deadline=False, as_of=as_of)
        dates[name] = value
        if flag and flag not in flags:
            flags.append(flag)
    received = _s(row.get("received_date"))
    for name in ("complaint_status_date", "problem_status_date"):
        other = _s(row.get(name))
        if received and other and other < received and QualityFlag.DATE_ORDER not in flags:
            flags.append(QualityFlag.DATE_ORDER)  # ISO strings compare chronologically

    unit_type = _s(row.get("unit_type"))
    scope = UNIT_TYPE_SCOPE.get((unit_type or "").upper(), LocationScope.UNSPECIFIED)
    basis = f"unit_type {unit_type}" if unit_type else "no unit_type in the record"

    # Privacy: unit detail redacted (with reproducible events); withheld fields masked.
    upstream = event_from_sanitized(row)
    public_row, here = redact_row_event(row)
    public_row, withheld_now = withhold_fields(public_row)
    redaction = _redaction_info(here, upstream)
    if redaction.text_fields_redacted:
        flags.append(QualityFlag.UNIT_DETAIL_REDACTED)
    withheld = sorted(
        f for f in ("complaint_anonymous_flag", "unique_key") if _s(row.get(f)) is not None
    )

    # Live rows are originals; a replayed fixture row is already sanitized.
    digest_basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, digest_key)
    redacted_sha = canonical_sha256(public_row)
    if evidence is not None:
        evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=str(row.get("problem_id")),
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=digest_basis,
                redacted_sha256=redacted_sha,
                redacted_fields=here["fields_masked"] + here["text_fields_redacted"] + withheld_now,
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
            )
        )

    return NormalizedComplaintProblem(
        dataset_id=DATASET_ID,
        problem_id=str(row.get("problem_id")),
        complaint_id=str(row.get("complaint_id")),
        building_id=_s(row.get("building_id")),
        source_bbl=_s(row.get("bbl")),
        source_bin=_s(row.get("bin")),
        source_borough=borough,
        source_block=_s(row.get("block")),
        source_lot=_s(row.get("lot")),
        resolved_bbl=plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        type=_s(row.get("type")),
        major_category=_s(row.get("major_category")),
        minor_category=_s(row.get("minor_category")),
        problem_code=_s(row.get("problem_code")),
        unit_type=unit_type,
        space_type=_s(row.get("space_type")),
        location_scope=scope,
        location_scope_basis=basis,
        complaint_status_raw=_s(row.get("complaint_status")),
        problem_status_raw=_s(row.get("problem_status")),
        complaint_is_open=complaint_open,
        problem_is_open=problem_open,
        is_duplicate_report=_yn(row.get("problem_duplicate_flag")),
        hpd_disposition=disposition(public_row.get("status_description")),
        has_311_reference=_s(row.get("unique_key")) is not None,
        dates=dates,
        quality_flags=flags,
        redaction=redaction,
        withheld_fields=withheld,
        original_digest=original_digest,
        digest_basis=digest_basis,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=data_as_of,
    )


def normalize_fetch(
    fetch: PagedFetch,
    plan: LotQueryPlan,
    *,
    as_of: dt.date,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
) -> list[NormalizedComplaintProblem]:
    """Normalize every row. Identical copies collapse; conflicting copies fail closed.

    Same approach as Task 1: when one problem_id appears with different contents, every
    copy is flagged and none keeps building-specific attribution, because we cannot tell
    which copy is right. Grouping and summaries run afterwards, so they see the result.
    """
    out: list[NormalizedComplaintProblem] = []
    seen: dict[str, dict[str, int]] = {}  # problem_id -> {row sha: output index}
    pages = {p.index: p for p in fetch.provenance.pages}
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        pid = str(row.get("problem_id"))
        sha = canonical_sha256(row)
        copies = seen.setdefault(pid, {})
        if sha in copies:
            idx = copies[sha]
            if QualityFlag.DUPLICATE_ID_SAME_CONTENT not in out[idx].quality_flags:
                out[idx].quality_flags.append(QualityFlag.DUPLICATE_ID_SAME_CONTENT)
            continue
        copies[sha] = len(out)
        out.append(
            normalize_row(
                row,
                plan,
                query_url=page.url if page else "",
                fetched_at=page.fetched_at if page else "",
                data_as_of=fetch.provenance.data_as_of,
                as_of=as_of,
                digest_key=digest_key,
                evidence=evidence,
            )
        )
    for copies in seen.values():
        if len(copies) < 2:
            continue
        for idx in copies.values():  # every conflicting copy, the first one included
            rec = out[idx]
            if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT not in rec.quality_flags:
                rec.quality_flags.append(QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
            if rec.attribution in (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT):
                rec.attribution = Attribution.UNATTRIBUTED_ON_LOT
    return out


def group_complaints(records: list[NormalizedComplaintProblem]) -> list[ComplaintGroup]:
    by_id: dict[str, list[NormalizedComplaintProblem]] = {}
    for r in records:
        by_id.setdefault(r.complaint_id, []).append(r)
    groups: list[ComplaintGroup] = []
    for cid, probs in by_id.items():
        buckets = Counter(p.attribution.value for p in probs)
        if len(buckets) == 1:
            attribution = ComplaintAttribution(next(iter(buckets)))
        else:
            attribution = ComplaintAttribution.MIXED
        flags: list[QualityFlag] = []
        received = {p.dates["received_date"].raw for p in probs}
        statuses = {p.complaint_status_raw for p in probs}
        if len(received) > 1 or len(statuses) > 1:
            flags.append(QualityFlag.COMPLAINT_FIELDS_DISAGREE)
        if any(QualityFlag.STATUS_FIELDS_DISAGREE in p.quality_flags for p in probs):
            flags.append(QualityFlag.STATUS_FIELDS_DISAGREE)
        first = min(probs, key=lambda p: p.dates["received_date"].raw or "")
        is_open = first.complaint_is_open if len(statuses) == 1 else None
        groups.append(
            ComplaintGroup(
                complaint_id=cid,
                attribution=attribution,
                problem_attributions=dict(sorted(buckets.items())),
                received_date=first.dates["received_date"],
                complaint_status_raw=first.complaint_status_raw if len(statuses) == 1 else None,
                is_open=is_open,
                problem_ids=[p.problem_id for p in probs],
                problem_count=len(probs),
                duplicate_problem_count=sum(1 for p in probs if p.is_duplicate_report),
                types=sorted({p.type for p in probs if p.type}),
                major_categories=sorted({p.major_category for p in probs if p.major_category}),
                outcomes=dict(Counter(p.hpd_disposition.outcome.value for p in probs)),
                quality_flags=flags,
            )
        )
    return groups


def _age_days(value: DateValue, as_of: dt.date) -> int | None:
    return (as_of - value.value).days if value and value.value else None


def summarize(
    records: list[NormalizedComplaintProblem],
    complaints: list[ComplaintGroup],
    scope: JoinScope | None,
    data_as_of: str | None,
    as_of: dt.date,
) -> ComplaintSummary:
    problems: dict[Attribution, ProblemBucket] = {}
    flag_counts: Counter[str] = Counter()
    scopes: Counter[str] = Counter()
    withheld: Counter[str] = Counter()
    for r in records:
        b = problems.setdefault(r.attribution, ProblemBucket())
        b.problems += 1
        if r.problem_is_open is True:
            b.open += 1
        elif r.problem_is_open is False:
            b.closed += 1
        else:
            b.status_unknown += 1
        if r.is_duplicate_report:
            b.duplicate_reports += 1
        else:
            b.not_flagged_duplicate += 1
        for field, key in ((b.by_type, r.type), (b.by_major_category, r.major_category)):
            k = key or "unknown"
            field[k] = field.get(k, 0) + 1
        o = r.hpd_disposition.outcome.value
        b.by_outcome[o] = b.by_outcome.get(o, 0) + 1
        age = _age_days(r.dates["received_date"], as_of)
        if age is not None:
            b.received_last_12_months += age <= 365
            b.received_last_36_months += age <= 3 * 365
        flag_counts.update(f.value for f in r.quality_flags)
        scopes[r.location_scope.value] += 1
        withheld.update(r.withheld_fields)

    complaint_buckets: dict[ComplaintAttribution, ComplaintBucket] = {}
    for c in complaints:
        b = complaint_buckets.setdefault(c.attribution, ComplaintBucket())
        b.complaints += 1
        if c.is_open is True:
            b.open += 1
        elif c.is_open is False:
            b.closed += 1
        else:
            b.status_unknown += 1
        b.with_duplicate_problems += c.duplicate_problem_count > 0
        b.hpd_reported_violations_issued += (
            c.outcomes.get(ComplaintOutcome.VIOLATIONS_ISSUED.value, 0) > 0
        )
        age = _age_days(c.received_date, as_of)
        if age is not None:
            b.received_last_12_months += age <= 365
            b.received_last_36_months += age <= 3 * 365

    with_text = [r for r in records if r.hpd_disposition.outcome is not ComplaintOutcome.NO_TEXT]
    other = [r for r in with_text if r.hpd_disposition.outcome is ComplaintOutcome.OTHER]
    other_texts = Counter(r.hpd_disposition.text or "" for r in other)
    coverage = ClassificationCoverage(
        problems_with_text=len(with_text),
        classified=len(with_text) - len(other),
        other=len(other),
        no_text=len(records) - len(with_text),
        coverage=round((len(with_text) - len(other)) / len(with_text), 4) if with_text else None,
        other_texts=[{"text": t, "count": n} for t, n in other_texts.most_common(20)],
    )
    conflicts = {f.value: flag_counts.get(f.value, 0) for f in sorted(CONFLICT_FLAGS)}
    conflicts["mixed_attribution_complaints"] = sum(
        1 for c in complaints if c.attribution is ComplaintAttribution.MIXED
    )
    return ComplaintSummary(
        data_as_of=data_as_of,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING,
        total_problems=len(records),
        total_complaints=len(complaints),
        problems_by_attribution=problems,
        complaints_by_attribution=complaint_buckets,
        classification=coverage,
        conflict_counts=conflicts,
        quality_flag_counts=dict(sorted(flag_counts.items())),
        location_scope_counts=dict(sorted(scopes.items())),
        privacy=privacy_summary(records),
        withheld_field_counts=dict(sorted(withheld.items())),
    )


def retrieve_hpd_complaints(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> ComplaintSourceResult:
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap

    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return ComplaintSourceResult(
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
            unique_key="problem_id",
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
        )
    except SourceError as exc:
        logger.warning("%s unavailable for %s: %s", DATASET_ID, plan.bbl, type(exc).__name__)
        return ComplaintSourceResult(
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
    complaints = group_complaints(records)
    prov = fetch.provenance
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = "the source reports 0 complaint problems for this lot"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = (
            f"{len(records)} problem rows in {len(complaints)} complaints; matches the source count"
        )
    return ComplaintSourceResult(
        source=SOURCE,
        dataset_id=DATASET_ID,
        status=status,
        status_detail=detail,
        join_scope=plan.scope,
        resolved_bbl=plan.bbl,
        resolved_bin=plan.building_bin,
        records=records,
        complaints=complaints,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summarize(records, complaints, plan.scope, prov.data_as_of, as_of),
        provenance=prov,
    )
