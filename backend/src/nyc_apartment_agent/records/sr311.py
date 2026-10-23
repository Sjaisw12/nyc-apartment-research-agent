"""311 Service Requests from 2020 to Present (erm2-nwe9), Phase 2 Task 5 step 4.

Entry point: `retrieve_service_requests(resolution, client)`.

Rules (docs/phase2_task5_dob_311_design.md, sections 4, 10, 18.1, 18.2, 18.4 and 18.9):

- Query (D19 revised): `bbl in (<lot family>) AND created_date >= '2020-01-01T00:00:00'
  AND agency not in ('HPD', 'DOB')`, ordered by `unique_key` (the field name only; no
  key value and no Socrata `:id` ever goes into a query). The lot family is the resolved
  BBL plus the `base_bbl` / `mappluto_bbl` that Footprints gives for the lot (lot lookup
  only, at both scopes; no BIN and no HPD lookup). Every value is checked as a BBL first.
- Unstable snapshots (D27 revised): the count and the data must carry the same
  X-SODA2-Truth-Last-Modified header, rows must equal the count and keys must be
  distinct. The pair is retried up to twice; otherwise `incomplete_pagination` with
  `source_snapshot_unstable`, rows kept and marked. Every attempt's headers are kept.
- Excluded rows (D19 revised): HPD- and DOB-routed requests are counted by a separate
  grouped query, never retrieved. The counts are established only when that query
  succeeded, the main fetch is complete, and both carry the same header. Rows with a
  NULL agency cannot match the main filter; a separate count says whether any exist.
- Attribution: never a building. `unattributed_on_lot` at building scope, `lot_level` at
  tax-lot scope for a row whose BBL is on the lot family. No address, location type,
  one-building lot or other context ever upgrades it.
- Privacy (D20, D21): the public reference is the keyed `k311_` token; `unique_key`
  stays in the private `original_row`. Picklist values are shown only if allowlisted;
  `resolution_description` only if it is an allowlisted template, and then redacted.
  Dates only. Location, channel and due date are never public.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from ..config import Settings, get_settings
from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..resolver.models import ResolutionResult
from ..sources.socrata_pager import TRUTH_HEADER, PagedFetch, fetch_all
from .dob_complaints import FOOTPRINTS_ID, _footprints
from .evidence import EvidenceStore
from .hpd_violations import EARLIEST_PLAUSIBLE, _as_of_date, _digest_key, _s
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    Attribution,
    DateValue,
    DigestBasis,
    JoinScope,
    LotMembership,
    QualityFlag,
    RawEvidence,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import (
    canonical_sha256,
    is_sanitized,
    k311_token,
    keyed_digest,
    redact_text_detailed,
)
from .sr311_models import (
    NULL_AGENCY_REASON,
    WITHHELD_VALUE,
    Attempt,
    CountCheck,
    ExcludedCounts,
    NormalizedServiceRequest,
    RequestStatus,
    ServiceRequestResult,
    Sr311Bucket,
    Sr311Summary,
)

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "erm2-nwe9"
UNIQUE_KEY = "unique_key"
WINDOW_START = "2020-01-01T00:00:00"
WINDOW_START_DATE = dt.date(2020, 1, 1)
EXCLUDED_AGENCIES = ("HPD", "DOB")
MAX_RESTARTS = 2  # 18.4: retry a count/data pair up to twice
# Reasons that mean the count and the data did not describe one snapshot. Duplicates
# are retried too (a one-response retrieval must have distinct keys) but keep their own
# more specific reason.
SNAPSHOT_REASONS = ("dataset_changed_during_retrieval", "short_page", "row_count_mismatch")
RETRY_ON = (*SNAPSHOT_REASONS, "page_too_long", "duplicate_unique_key")
REQUIRED_FIELDS = ("unique_key", "created_date", "agency", "complaint_type", "status", "bbl")
# Source fields that may reach a public record (after allowlisting and date truncation).
PUBLIC_SOURCE_FIELDS = frozenset(
    {
        "agency",
        "complaint_type",
        "descriptor",
        "descriptor_2",
        "location_type",
        "resolution_description",
        "status",
        "created_date",
        "closed_date",
        "resolution_action_updated_date",
        "bbl",
    }
)
PICKLISTS = ("complaint_type", "descriptor", "descriptor_2", "location_type")
_TEN = re.compile(r"^[1-5]\d{9}$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?$")
# Fail closed even on an allowlisted value (same patterns as the reference builder).
_UNIT_FLOOR = re.compile(
    r"\b(?:APTS?|APARTMENTS?|UNITS?)\s*(?:NO\.?|#)?\s*\d|\b(?:FLOOR|FLR|FL)\.?\s*#?\d"
    r"|\b\d{1,3}(?:ST|ND|RD|TH)\s+(?:FLOOR|FLR|FL)\b",
    re.IGNORECASE,
)
_PRIVATE = re.compile(r"@|\d{3}[-. )]+\d{3}[-. ]\d{4}|\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]")

STATEMENT = (
    "A 311 service request records that someone reported a problem to the city. It is not "
    "a finding that a violation occurred. A closed request means the agency closed it; it "
    "does not show that the condition was corrected. 311 requests are located by the "
    "reported address's tax lot and are never attributed to a specific building."
)
COVERAGE_NOTES = (
    "311 requests are searched by tax lot (BBL) only, for every lot in the lot family, "
    "from 2020-01-01. Requests without a geovalidated BBL (12.0% of recent rows citywide) "
    "are not found, and no address-based search is made.",
    "Agencies with their own systems (the dictionary names NYCHA and the Department of "
    "Correction) are absent. The dictionary's 'Public View' column suggests some complaint "
    "types are not published (not verified).",
    "Requests routed to HPD or DOB are not retrieved here; those agencies' own datasets "
    "cover them. Their numbers are shown as source counts only when established.",
    "Requests with no agency cannot match the agency filter and are not retrieved; none "
    "was found citywide among rows created since 2026-09-01.",
    "'No 311 requests found' means none found on this lot in the published data since "
    "2020, never that none were made.",
)


# ---- Reference data -------------------------------------------------------------------


@cache
def reference() -> dict[str, Any]:
    """Allowlists, agencies and status mapping (built by scripts/task5_build_311_reference.py)."""
    text = (
        resources.files("nyc_apartment_agent.records")
        .joinpath("data/sr311_reference.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


@cache
def _allow(field: str) -> frozenset[str]:
    if field == "agency":
        return frozenset(reference()["agencies"])
    return frozenset(reference()["allowlists"][field])


def public_value(field: str, raw: str | None) -> tuple[str | None, bool]:
    """(public value, withheld). A value off the allowlist is never passed through."""
    if raw is None:
        return None, False
    if raw in _allow(field) and not (_UNIT_FLOOR.search(raw) or _PRIVATE.search(raw)):
        return raw, False
    return WITHHELD_VALUE, True


def classify_status(raw: str | None) -> tuple[RequestStatus, str | None, bool]:
    """(status, public raw value, withheld). Only measured values are shown as text."""
    ref = reference()
    status = RequestStatus(ref["status"][raw]) if raw in ref["status"] else RequestStatus.UNKNOWN
    if raw is None:
        return RequestStatus.UNKNOWN, None, False
    if raw in ref["status_values_measured"] or raw in ref["status"]:
        return status, raw, False
    return RequestStatus.UNKNOWN, WITHHELD_VALUE, True


def parse_date(raw: object, *, as_of: dt.date) -> tuple[DateValue, QualityFlag | None]:
    """Socrata floating timestamps. Only the date part is ever public (D21)."""
    if raw is None or str(raw).strip() == "":
        return DateValue(raw=None, value=None), None
    m = _DATE.match(str(raw).strip())
    if not m:
        return DateValue(raw=None, value=None), QualityFlag.UNPARSEABLE_DATE
    text = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        value = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return DateValue(raw=None, value=None), QualityFlag.UNPARSEABLE_DATE
    if value < EARLIEST_PLAUSIBLE or value > as_of + dt.timedelta(days=1):
        return DateValue(raw=text, value=None), QualityFlag.IMPLAUSIBLE_DATE
    return DateValue(raw=text, value=value), None


# ---- Query --------------------------------------------------------------------------------


def _bbl_list(family: list[str]) -> str:
    for v in family:
        if not _TEN.match(v):
            raise ValueError("lot family values must be 10-digit BBLs")
    return "bbl in (" + ", ".join(f"'{v}'" for v in sorted(family)) + ")"


def base_where(family: list[str]) -> str:
    return f"{_bbl_list(family)} AND created_date >= '{WINDOW_START}'"


def build_where(family: list[str]) -> str:
    agencies = ", ".join(f"'{a}'" for a in EXCLUDED_AGENCIES)
    return f"{base_where(family)} AND agency not in ({agencies})"


def excluded_where(family: list[str]) -> str:
    agencies = ", ".join(f"'{a}'" for a in EXCLUDED_AGENCIES)
    return f"{base_where(family)} AND agency in ({agencies})"


def null_agency_where(family: list[str]) -> str:
    return f"{base_where(family)} AND agency IS NULL"


@dataclass
class _Lot:
    family: list[str]
    provenance: list[RetrievalProvenance]
    incomplete_reasons: list[str]
    warnings: list[str]


def _lot_family(client: SourceHttpClient, plan: LotQueryPlan, page_size: int) -> _Lot:
    """Resolved BBL plus Footprints base_bbl / mappluto_bbl for the lot (lot lookup only)."""
    out = _Lot(family=[plan.bbl], provenance=[], incomplete_reasons=[], warnings=[])
    family = {plan.bbl}
    try:
        fp = _footprints(client, f"base_bbl='{plan.bbl}' OR mappluto_bbl='{plan.bbl}'", page_size)
        out.provenance.append(fp.provenance)
        if not fp.complete:
            out.incomplete_reasons.append(
                "lot_family_incomplete: footprints lot lookup "
                + "; ".join(fp.provenance.incomplete_reasons)
            )
        for row in fp.rows:
            for f in ("base_bbl", "mappluto_bbl"):
                v = _s(row.get(f))
                if v:
                    family.add(v)
    except SourceError as exc:
        out.provenance.append(
            RetrievalProvenance(dataset_id=FOOTPRINTS_ID, error=type(exc).__name__)
        )
        out.incomplete_reasons.append("lot_family_incomplete: footprints unavailable")
    good = sorted(v for v in family if _TEN.match(v))
    if len(good) != len(family):
        out.warnings.append(
            f"lot_family_value_ignored: {len(family) - len(good)} value(s) not a BBL"
        )
    out.family = good
    return out


# ---- Separate counts (D19) ---------------------------------------------------------------


def _run_count(
    client: SourceHttpClient, params: dict[str, str], *, grouped: bool
) -> tuple[CountCheck, dict[str, int] | None]:
    check = CountCheck(query_where=params["$where"])
    try:
        resp = client.get_json(f"/resource/{DATASET_ID}.json", params=params)
    except SourceError as exc:
        check.error = type(exc).__name__
        return check, None
    check.truth_header = resp.headers.get(TRUTH_HEADER)
    check.fetched_at = resp.fetched_at
    data = resp.data
    counts: dict[str, int] = {}
    try:
        if not isinstance(data, list):
            raise ValueError
        for row in data:
            n = int(row["n"])
            if n < 0:
                raise ValueError
            if grouped:
                agency = row.get("agency")
                if agency not in EXCLUDED_AGENCIES or agency in counts:
                    raise ValueError
                counts[agency] = n
            else:
                counts["null_agency"] = n
        if grouped:
            for a in EXCLUDED_AGENCIES:
                counts.setdefault(a, 0)  # a grouped query returns no row for zero
        elif len(data) != 1:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        check.error = "bad_count_response"
        return check, None
    check.ok = True
    check.counts_observed = counts
    return check, counts


def _establish(check: CountCheck, fetch_complete: bool, data_version: str | None) -> CountCheck:
    if not check.ok:
        check.not_established_reason = f"count query failed: {check.error}"
    elif not fetch_complete:
        check.not_established_reason = "main fetch incomplete"
    elif check.truth_header is None:
        check.not_established_reason = "count response has no data version header"
    elif check.truth_header != data_version:
        check.not_established_reason = (
            f"data version differs: count {check.truth_header}, fetch {data_version}"
        )
    else:
        check.established = True
        check.counts = dict(check.counts_observed or {})
    return check


def excluded_counts(
    client: SourceHttpClient,
    family: list[str],
    *,
    fetch_complete: bool,
    data_version: str | None,
) -> ExcludedCounts:
    by_agency, _ = _run_count(
        client,
        {"$select": "agency, count(*) AS n", "$where": excluded_where(family), "$group": "agency"},
        grouped=True,
    )
    null, _ = _run_count(
        client, {"$select": "count(*) AS n", "$where": null_agency_where(family)}, grouped=False
    )
    return ExcludedCounts(
        by_agency=_establish(by_agency, fetch_complete, data_version),
        null_agency=_establish(null, fetch_complete, data_version),
    )


# ---- Normalization ------------------------------------------------------------------------


@dataclass
class _Ctx:
    plan: LotQueryPlan
    family: list[str]
    as_of: dt.date
    digest_key: bytes
    data_as_of: str | None
    evidence: EvidenceStore | None


def _attribute(ctx: _Ctx, row_bbl: str | None) -> tuple[Attribution, LotMembership, list]:
    flags: list[QualityFlag] = []
    if row_bbl is None:
        flags.append(QualityFlag.BBL_MISSING)
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags
    if row_bbl not in ctx.family:
        flags.append(QualityFlag.LOT_IDENTIFIER_CONFLICT)
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags
    if row_bbl != ctx.plan.bbl:
        flags.append(QualityFlag.CONDO_LOT_FAMILY)
    if ctx.plan.scope is JoinScope.TAX_LOT:
        return Attribution.LOT_LEVEL, LotMembership.CONFIRMED, flags
    # Building scope: the BBL is geocoded from what the submitter typed, and 311 has no
    # BIN, so a request is never placed on a building.
    return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFIRMED, flags


def _flag(flags: list[QualityFlag], flag: QualityFlag) -> None:
    if flag not in flags:
        flags.append(flag)


def normalize_row(
    row: dict[str, Any], ctx: _Ctx, *, token: str, query_url: str, fetched_at: str
) -> NormalizedServiceRequest:
    """`token` is the row's k311_ token (computed by the caller, which rejects bad keys)."""
    raw_bbl = _s(row.get("bbl"))
    row_bbl = raw_bbl if raw_bbl and _TEN.match(raw_bbl) else None
    attribution, membership, flags = _attribute(ctx, row_bbl)
    if raw_bbl and row_bbl is None:
        flags = [QualityFlag.BBL_INVALID if f is QualityFlag.BBL_MISSING else f for f in flags]
    withheld = sorted(k for k in row if k not in PUBLIC_SOURCE_FIELDS and k != "fixture_row_ref")

    public: dict[str, str | None] = {}
    for field in ("agency", *PICKLISTS):
        value, was_withheld = public_value(field, _s(row.get(field)))
        public[field] = value
        if was_withheld:
            withheld.append(field)
            _flag(flags, QualityFlag.UNCOMMON_VALUE_WITHHELD)

    redaction = RedactionInfo(applied="none")
    resolution, res_withheld = public_value(
        "resolution_description", _s(row.get("resolution_description"))
    )
    if res_withheld:
        withheld.append("resolution_description")
        _flag(flags, QualityFlag.UNCOMMON_VALUE_WITHHELD)
    elif resolution is not None:
        resolution, counts = redact_text_detailed(resolution)
        if counts:
            redaction = RedactionInfo(
                applied="this_run",
                text_fields_redacted=["resolution_description"],
                text_replacements=counts,
            )
            _flag(flags, QualityFlag.UNIT_DETAIL_REDACTED)

    status, status_raw, status_withheld = classify_status(_s(row.get("status")))
    if status_withheld:
        withheld.append("status")
        _flag(flags, QualityFlag.UNCOMMON_VALUE_WITHHELD)
    if status is RequestStatus.UNKNOWN:
        _flag(flags, QualityFlag.STATUS_UNKNOWN)

    dates: dict[str, DateValue] = {}
    for name in ("created_date", "closed_date", "resolution_action_updated_date"):
        value, flag = parse_date(row.get(name), as_of=ctx.as_of)
        dates[name] = value
        if flag:
            _flag(flags, flag)
    created = dates["created_date"].value
    for name in ("closed_date", "resolution_action_updated_date"):
        other = dates[name].value
        if created and other and other < created:
            _flag(flags, QualityFlag.DATE_ORDER)
    has_closed = _s(row.get("closed_date")) is not None
    if (status is RequestStatus.CLOSED and not has_closed) or (
        status is RequestStatus.OPEN and has_closed
    ):
        _flag(flags, QualityFlag.STATUS_FIELDS_DISAGREE)

    withheld = sorted(set(withheld))
    public_row = {k: ("[WITHHELD]" if k in withheld else v) for k, v in row.items()}
    public_row.pop("fixture_row_ref", None)
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, ctx.digest_key)
    redacted_sha = canonical_sha256(public_row)
    basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    if ctx.evidence is not None:
        ctx.evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=token,
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=basis,
                redacted_sha256=redacted_sha,
                redacted_fields=withheld,
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=ctx.data_as_of,
            )
        )
    return NormalizedServiceRequest(
        dataset_id=DATASET_ID,
        request_ref=token,
        source_record_id=token,
        source_bbl=row_bbl,
        resolved_bbl=ctx.plan.bbl,
        join_scope=ctx.plan.scope,
        lot_membership=membership,
        attribution=attribution,
        agency=public["agency"],
        complaint_type=public["complaint_type"],
        descriptor=public["descriptor"],
        descriptor_2=public["descriptor_2"],
        location_type=public["location_type"],
        resolution_description=resolution,
        redaction=redaction,
        request_status=status,
        status_raw=status_raw,
        received_date=dates["created_date"],
        closed_date=dates["closed_date"],
        agency_updated_date=dates["resolution_action_updated_date"],
        withheld_fields=withheld,
        quality_flags=flags,
        original_digest=original_digest,
        digest_basis=basis,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=ctx.data_as_of,
    )


def normalize_fetch(
    fetch: PagedFetch, ctx: _Ctx
) -> tuple[list[NormalizedServiceRequest], dict[str, int]]:
    out: list[NormalizedServiceRequest] = []
    rejected: Counter[str] = Counter()
    seen: dict[str, dict[str, int]] = {}  # token -> {content sha: index in out}
    pages = {p.index: p for p in fetch.provenance.pages}
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        try:
            token = k311_token(row.get(UNIQUE_KEY), ctx.digest_key)
        except ValueError:
            # A missing or malformed unique_key: no token, no evidence, no record.
            rejected["unique_key_missing_or_malformed"] += 1
            continue
        record = normalize_row(
            row,
            ctx,
            token=token,
            query_url=page.url if page else "",
            fetched_at=page.fetched_at if page else "",
        )
        copies = seen.setdefault(record.request_ref, {})
        sha = canonical_sha256(row)
        if sha in copies:
            kept = out[copies[sha]]
            kept.collapsed_copies += 1
            _flag(kept.quality_flags, QualityFlag.DUPLICATE_ID_SAME_CONTENT)
            continue
        copies[sha] = len(out)
        out.append(record)
    for c in seen.values():
        if len(c) > 1:
            for idx in c.values():
                _flag(out[idx].quality_flags, QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
    return out, dict(rejected)


# ---- Summary ------------------------------------------------------------------------------


def _inc(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


def _months_back(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    for d in (day.day, 30, 29, 28):
        try:
            return dt.date(y, m, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def summarize(
    records: list[NormalizedServiceRequest],
    scope: JoinScope | None,
    data_as_of: str | None,
    as_of: dt.date,
    source_rows: int,
    *,
    complete: bool,
) -> Sr311Summary:
    buckets: dict[Attribution, Sr311Bucket] = {}
    windows = {12: _months_back(as_of, 12), 36: _months_back(as_of, 36)}
    supported = {m: complete and start >= WINDOW_START_DATE for m, start in windows.items()}
    withheld: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    disagree = open_closed = closed_open = 0
    for r in records:
        b = buckets.setdefault(r.attribution, Sr311Bucket())
        if supported[12] and b.received_last_12_months is None:
            b.received_last_12_months = 0
        if supported[36] and b.received_last_36_months is None:
            b.received_last_36_months = 0
        b.requests += 1
        agency = r.agency or "(none)"
        ctype = r.complaint_type or "(none)"
        _inc(b.by_agency, agency)
        _inc(b.by_complaint_type, ctype)
        _inc(b.by_status, r.request_status.value)
        _inc(b.by_agency_type_status, f"{agency}|{ctype}|{r.request_status.value}")
        received = r.received_date.value
        if received and supported[12] and received >= windows[12]:
            b.received_last_12_months += 1
        if received and supported[36] and received >= windows[36]:
            b.received_last_36_months += 1
        for f in r.withheld_fields:
            if f in PUBLIC_SOURCE_FIELDS:
                _inc(withheld, f)
        for f in r.quality_flags:
            _inc(flag_counts, f.value)
        if QualityFlag.STATUS_FIELDS_DISAGREE in r.quality_flags:
            disagree += 1
            if r.request_status is RequestStatus.OPEN:
                open_closed += 1
            else:
                closed_open += 1
    return Sr311Summary(
        data_as_of=data_as_of,
        join_scope=scope,
        total_requests=len(records),
        source_rows=source_rows,
        complete=complete,
        by_attribution=buckets,
        status_fields_disagree=disagree,
        open_with_closed_date=open_closed,
        closed_without_closed_date=closed_open,
        withheld_value_counts=withheld,
        quality_flag_counts=flag_counts,
        statement=STATEMENT,
    )


# ---- Entry point --------------------------------------------------------------------------


def _result(**kw) -> ServiceRequestResult:
    return ServiceRequestResult(source=SOURCE, dataset_id=DATASET_ID, **kw)


def _attempts(fetch: PagedFetch) -> list[Attempt]:
    return [
        Attempt(
            attempt=a.attempt,
            count_header=a.count_header,
            page_headers=a.page_headers,
            source_count=a.source_count,
            rows=a.rows,
            reasons=a.reasons,
            accepted=a.accepted,
        )
        for a in fetch.attempts
    ]


def _versions(attempts: list[Attempt]) -> list[str]:
    seen = {a.count_header for a in attempts} | {h for a in attempts for h in a.page_headers}
    return sorted(h for h in seen if h is not None)


def retrieve_service_requests(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> ServiceRequestResult:
    """311 requests (2020 to present, agencies other than HPD and DOB) on a resolved lot."""
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap

    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return _result(
            status=RetrievalStatus.UNSUPPORTED_JOIN,
            status_detail=str(exc),
            join_scope=None,
            resolved_bbl=None,
            provenance=RetrievalProvenance(dataset_id=DATASET_ID),
        )

    lot = _lot_family(client, plan, page_size)
    where = build_where(lot.family)

    def common() -> dict[str, Any]:
        return {
            "join_scope": plan.scope,
            "resolved_bbl": plan.bbl,
            "lot_family": lot.family,
            "lot_family_provenance": lot.provenance,
            "coverage_notes": list(COVERAGE_NOTES),
        }

    try:
        fetch = fetch_all(
            client,
            DATASET_ID,
            where=where,
            order_by=UNIQUE_KEY,
            unique_key=UNIQUE_KEY,
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
            max_restarts=MAX_RESTARTS,
            retry_on=RETRY_ON,
        )
    except SourceError as exc:
        logger.warning("%s unavailable: %s", DATASET_ID, type(exc).__name__)
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"{type(exc).__name__}: the source could not be checked",
            provenance=RetrievalProvenance(
                dataset_id=DATASET_ID, where=where, order_by=UNIQUE_KEY, error=type(exc).__name__
            ),
            **common(),
        )

    prov = fetch.provenance
    attempts = _attempts(fetch)
    versions = _versions(attempts)
    missing = [w for w in prov.warnings if w.startswith("fields_absent_from_all_rows")]
    if missing:
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"schema_changed: {missing[0]}",
            provenance=prov,
            attempts=attempts,
            data_versions_seen=versions,
            **common(),
        )

    reasons = list(prov.incomplete_reasons)
    if any(r.startswith(SNAPSHOT_REASONS) for r in reasons):
        reasons.append(
            f"source_snapshot_unstable: no header-matched count and data after "
            f"{prov.restarts} retr{'y' if prov.restarts == 1 else 'ies'}"
        )
    reasons += lot.incomplete_reasons
    prov.warnings.extend(lot.warnings)
    if len(versions) > 1:
        prov.warnings.append(f"data_versions_seen: {len(versions)}")

    as_of = _as_of_date(fetch, today)
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    ctx = _Ctx(plan, lot.family, as_of, digest_key, prov.data_as_of, evidence)
    records, rejected = normalize_fetch(fetch, ctx)
    if rejected:
        reasons.append(f"rows_rejected: {sum(rejected.values())}")

    fetch_complete = not reasons
    accepted = prov.data_as_of if fetch_complete else None
    excluded = excluded_counts(
        client, lot.family, fetch_complete=fetch_complete, data_version=accepted
    )
    null = excluded.null_agency
    if null.established and (null.counts or {}).get("null_agency", 0) > 0:
        reasons.append(f"{NULL_AGENCY_REASON}: {null.counts['null_agency']}")
    elif not null.established:
        prov.warnings.append(f"null_agency_count_not_established: {null.not_established_reason}")

    prov.incomplete_reasons = reasons
    if reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = f"the source reports 0 311 requests for {len(lot.family)} lot(s) since 2020"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = (
            f"{len(records)} requests from {len(fetch.rows)} source rows for "
            f"{len(lot.family)} lot(s); matches the source count"
        )
    logger.info("%s: status=%s records=%d", DATASET_ID, status.value, len(records))
    return _result(
        status=status,
        status_detail=detail,
        records=records,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summarize(
            records,
            plan.scope,
            prov.data_as_of,
            as_of,
            len(fetch.rows),
            complete=not reasons,
        ),
        provenance=prov,
        attempts=attempts,
        data_versions_seen=versions,
        accepted_data_version=accepted,
        rows_rejected=rejected,
        excluded_counts=excluded,
        **common(),
    )
