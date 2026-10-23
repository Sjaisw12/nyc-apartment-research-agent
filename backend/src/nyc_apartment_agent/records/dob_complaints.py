"""DOB Complaints Received (eabe-havv), Phase 2 Task 5 step 2.

Entry point: `retrieve_dob_complaints(resolution, client)`.

Rules (docs/phase2_task5_dob_311_design.md, sections 4 to 9, 18.3 and 18.6):

- DOB complaints carry a BIN and no lot. They are searched **only at building scope**:
  a tax-lot resolution returns `unsupported_join` and sends no request at all.
- BIN search set: the resolved BIN, plus the BINs that Footprints places on the lot
  (base_bbl or mappluto_bbl) and the BINs of current HPD building records on the lot.
  Each BIN keeps its sources. A BIN known to only one of Footprints and HPD is
  `bin_single_source` (silence is not a conflict). A BIN that one source places on the
  lot and Footprints places on a lot outside the lot family is `bin_lot_conflict`: it is
  not searched, and if it is the resolved BIN its rows are never attributed to a building.
  Placeholder BINs are never searched. At most 25 BINs are searched.
- Attribution: resolved BIN -> `this_building`; another BIN on the lot ->
  `other_building_on_lot` (basis `bin_lot_mapping`); anything else -> `unattributed_on_lot`.
  Address text is never compared.
- Disposition codes use the approved mapping (reference file built from
  docs/task5/proposed_mappings.json). DOB's own text is kept verbatim; a code with no
  official definition is `unknown_code` with no description. Order dispositions become
  historical order events whose wording never says an order is in effect.
- Complaint status and disposition outcome stay separate. ACTIVE with a final outcome, or
  CLOSED with no disposition, keeps both values and is flagged `status_fields_disagree`.
- Duplicate complaint numbers: identical copies collapse (every source row id kept);
  differing copies are all kept, flagged, and lose building attribution.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from ..config import Settings, get_settings
from ..errors import InvalidIdentifierError, SourceError
from ..http_client import SourceHttpClient
from ..identifiers import BinStatus, classify_bin
from ..resolver.models import ResolutionResult
from ..sources.socrata_pager import PagedFetch, fetch_all
from .admin_models import BuildingSourceResult
from .attribution import bin_flags
from .dob_models import (
    DOB_CONFLICT_FLAGS,
    AttributionBucket,
    BinEvidence,
    BinSource,
    DobComplaintOutcome,
    DobComplaintResult,
    DobComplaintStatus,
    DobComplaintSummary,
    DobSourceRow,
    NormalizedDobComplaint,
    OrderAction,
    OrderEvent,
    OrderEventKind,
)
from .evidence import EvidenceStore
from .hpd_buildings import retrieve_hpd_buildings
from .hpd_violations import EARLIEST_PLAUSIBLE, _as_of_date, _digest_key, _s
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    Attribution,
    AttributionBasis,
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
from .redaction import canonical_sha256, keyed_digest, redact_text_detailed

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "eabe-havv"
FOOTPRINTS_ID = "5zhs-2jue"
ROW_ID = ":id"
REQUIRED_FIELDS = ("complaint_number", "status", "date_entered", "bin", "complaint_category")
MAX_BINS = 25
FOOTPRINTS_ROW_CAP = 5000
_DOB_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DIGITS = re.compile(r"^\d{7}$")
NOT_PUBLIC = ("dobrundate",)  # DOB's extract time, private evidence only

ORDER_STATEMENT = (
    'DOB recorded disposition {code} ("{text}") on {date} for complaint {number}. '
    "This dataset does not show whether the order is still in effect. "
    "Check DOB's own records."
)
COVERAGE_NOTE = (
    "DOB complaints are searched only by BIN. A complaint filed under a BIN that is not on "
    "the current Footprints or HPD lists for this lot (for example a retired BIN) is not "
    "found."
)


# ---- Reference data -------------------------------------------------------------------


@cache
def reference() -> dict[str, Any]:
    """The approved disposition mapping and DOB's category list (see the build script)."""
    text = (
        resources.files("nyc_apartment_agent.records")
        .joinpath("data/dob_complaints_reference.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def classify_disposition(code: str | None) -> dict[str, Any]:
    """Outcome, verbatim definition and order event for a code. Never guesses."""
    if code is None:
        return {"outcome": DobComplaintOutcome.NONE, "definition": None, "source": None}
    entry = reference()["dispositions"].get(code)
    if entry is None or entry["outcome"] == DobComplaintOutcome.UNKNOWN_CODE.value:
        return {"outcome": DobComplaintOutcome.UNKNOWN_CODE, "definition": None, "source": None}
    out: dict[str, Any] = {
        "outcome": DobComplaintOutcome(entry["outcome"]),
        "definition": entry["definition"],
        "source": entry["definition_source"],
    }
    if entry["order_event"]:
        out["order_event"] = OrderEventKind(entry["order_event"])
        out["order_action"] = OrderAction(entry["order_action"])
    return out


def final_outcomes() -> frozenset[DobComplaintOutcome]:
    return frozenset(DobComplaintOutcome(o) for o in reference()["final_outcomes"])


# ---- Dates ------------------------------------------------------------------------------


def parse_dob_date(raw: object, *, as_of: dt.date) -> tuple[DateValue, QualityFlag | None]:
    """DOB writes MM/DD/YYYY. The raw text is always kept."""
    if raw is None or str(raw).strip() == "":
        return DateValue(raw=None, value=None), None
    text = str(raw).strip()
    m = _DOB_DATE.match(text)
    if not m:
        return DateValue(raw=text, value=None), QualityFlag.UNPARSEABLE_DATE
    try:
        value = dt.date(int(m[3]), int(m[1]), int(m[2]))
    except ValueError:
        return DateValue(raw=text, value=None), QualityFlag.UNPARSEABLE_DATE
    if value < EARLIEST_PLAUSIBLE or value > as_of + dt.timedelta(days=1):
        return DateValue(raw=text, value=None), QualityFlag.IMPLAUSIBLE_DATE
    return DateValue(raw=text, value=value), None


# ---- BIN evidence (design 6.3 and 18.3) ---------------------------------------------------


@dataclass
class BinPlan:
    evidence: dict[str, BinEvidence] = field(default_factory=dict)
    search_bins: list[str] = field(default_factory=list)
    lot_family: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    provenance: list[RetrievalProvenance] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)


def _quote(values: list[str]) -> str:
    for v in values:
        if not _DIGITS.match(v):  # values come from validated sources; refuse anything else
            raise ValueError(f"refusing to quote a non-BIN value: {v!r}")
    return ", ".join(f"'{v}'" for v in values)


def _footprints(client, where: str, page_size: int) -> PagedFetch:
    return fetch_all(
        client,
        FOOTPRINTS_ID,
        where=where,
        order_by=f"bin, {ROW_ID}",
        unique_key=ROW_ID,
        select=f"bin, base_bbl, mappluto_bbl, {ROW_ID}",
        page_size=page_size,
        row_cap=FOOTPRINTS_ROW_CAP,
    )


def _assigned(value: object) -> str | None:
    v = _s(value)
    try:
        return v if v is not None and classify_bin(v) is BinStatus.ASSIGNED else None
    except InvalidIdentifierError:  # a malformed BIN is simply not usable
        return None


def plan_bins(
    client: SourceHttpClient,
    plan: LotQueryPlan,
    hpd: BuildingSourceResult,
    *,
    page_size: int,
) -> BinPlan:
    out = BinPlan()
    sources: dict[str, set[BinSource]] = {plan.building_bin: {BinSource.RESOLVER}}
    family = {plan.bbl}
    footprints_ok = False

    # 1. Footprints: BINs whose base_bbl or mappluto_bbl is the resolved lot.
    try:
        fp = _footprints(client, f"base_bbl='{plan.bbl}' OR mappluto_bbl='{plan.bbl}'", page_size)
        out.provenance.append(fp.provenance)
        if fp.complete:
            footprints_ok = True
        else:
            out.incomplete_reasons.append(
                "bin_evidence_incomplete: footprints lot lookup "
                + "; ".join(fp.provenance.incomplete_reasons)
            )
        for row in fp.rows:
            for f in ("base_bbl", "mappluto_bbl"):
                if _s(row.get(f)):
                    family.add(str(row[f]).strip())
            b = _assigned(row.get("bin"))
            if b:
                sources.setdefault(b, set()).add(BinSource.FOOTPRINTS)
    except SourceError as exc:
        out.provenance.append(
            RetrievalProvenance(dataset_id=FOOTPRINTS_ID, error=f"{type(exc).__name__}")
        )
        out.incomplete_reasons.append("bin_evidence_incomplete: footprints unavailable")

    # 2. HPD: current building records on the lot (Task 3 result).
    out.provenance.append(hpd.provenance)
    if hpd.status in (RetrievalStatus.RECORDS_FOUND, RetrievalStatus.NO_RECORDS_FOUND):
        hpd_ok = True
    else:
        hpd_ok = False
        out.incomplete_reasons.append(f"bin_evidence_incomplete: hpd buildings {hpd.status.value}")
    for rec in hpd.records:
        b = _assigned(rec.source_bin)
        if b and rec.is_current and rec.lot_membership is LotMembership.CONFIRMED:
            sources.setdefault(b, set()).add(BinSource.HPD)

    # 3. BINs that Footprints does not place on the lot: look them up by BIN, to tell
    #    silence (no Footprints row) from conflict (Footprints puts it on another lot).
    unplaced = sorted(b for b, s in sources.items() if BinSource.FOOTPRINTS not in s)
    lots_by_bin: dict[str, set[str]] = {}
    lookup_ok = True
    if unplaced and footprints_ok:
        try:
            fb = _footprints(client, f"bin in ({_quote(unplaced)})", page_size)
            out.provenance.append(fb.provenance)
            if not fb.complete:
                lookup_ok = False
            for row in fb.rows:
                b = _s(row.get("bin"))
                lots = {str(row[f]).strip() for f in ("base_bbl", "mappluto_bbl") if _s(row.get(f))}
                lots_by_bin.setdefault(b, set()).update(lots)
        except SourceError as exc:
            lookup_ok = False
            out.provenance.append(
                RetrievalProvenance(dataset_id=FOOTPRINTS_ID, error=f"{type(exc).__name__}")
            )
    elif unplaced:
        lookup_ok = False
    if unplaced and not lookup_ok:
        out.incomplete_reasons.append(
            "bin_evidence_incomplete: could not check Footprints for "
            f"{len(unplaced)} BIN(s) known only to the resolver or HPD"
        )

    out.lot_family = sorted(family)
    candidates: list[str] = []
    for b in sorted(sources):
        src = sources[b]
        ev = BinEvidence(
            bin=b,
            sources=sorted(src),
            is_resolved_bin=b == plan.building_bin,
            searched=False,
            footprints_lots_for_bin=sorted(lots_by_bin.get(b, ())),
        )
        if b in unplaced:
            if not lookup_ok and b != plan.building_bin:
                ev.note = "not searched: its place on the lot could not be checked in Footprints"
                out.evidence[b] = ev
                continue
            if lots_by_bin.get(b) and not (lots_by_bin[b] & family):
                ev.flags.append(QualityFlag.BIN_LOT_CONFLICT)
                ev.note = "Footprints places this BIN on another lot"
                out.conflicts.append(b)
                if b != plan.building_bin:
                    out.evidence[b] = ev  # never searched
                    continue
        known = {BinSource.FOOTPRINTS, BinSource.HPD} & src
        if b != plan.building_bin and len(known) == 1 and footprints_ok and hpd_ok:
            ev.flags.append(QualityFlag.BIN_SINGLE_SOURCE)
        elif b == plan.building_bin and not known and footprints_ok and hpd_ok:
            ev.flags.append(QualityFlag.BIN_SINGLE_SOURCE)
        out.evidence[b] = ev
        candidates.append(b)

    # 4. At most MAX_BINS, the resolved BIN always included.
    others = [b for b in candidates if b != plan.building_bin]
    chosen = sorted([plan.building_bin, *others[: MAX_BINS - 1]])
    if len(others) > MAX_BINS - 1:
        out.incomplete_reasons.append(
            f"bin_set_too_large: {len(candidates)} BINs, only {MAX_BINS} searched"
        )
        for b in others[MAX_BINS - 1 :]:
            out.evidence[b].note = "not searched: BIN cap reached"
    for b in chosen:
        out.evidence[b].searched = True
    out.search_bins = chosen
    return out


# ---- Rows ---------------------------------------------------------------------------------


@dataclass
class _Ctx:
    plan: LotQueryPlan
    bins: BinPlan
    as_of: dt.date
    digest_key: bytes
    data_as_of: str | None
    evidence: EvidenceStore | None


def _attribute(ctx: _Ctx, bin_value: str | None, flags: list[QualityFlag]):
    """(attribution, basis, lot membership, bin sources) for a row's own BIN."""
    status, b_flags = bin_flags(bin_value)
    flags += b_flags
    if status is not BinStatus.ASSIGNED:
        return (
            Attribution.UNATTRIBUTED_ON_LOT,
            AttributionBasis.ROW_IDENTIFIERS,
            (LotMembership.CONFLICTING),
            [],
        )
    b = str(bin_value).strip()
    ev = ctx.bins.evidence.get(b)
    if ev is None or b not in ctx.bins.search_bins:
        flags.append(QualityFlag.BIN_NOT_ON_CURRENT_LOT)
        return (
            Attribution.UNATTRIBUTED_ON_LOT,
            AttributionBasis.ROW_IDENTIFIERS,
            (LotMembership.CONFLICTING),
            [],
        )
    flags += [f for f in ev.flags if f not in flags]
    if QualityFlag.BIN_LOT_CONFLICT in ev.flags:
        return (
            Attribution.UNATTRIBUTED_ON_LOT,
            AttributionBasis.ROW_IDENTIFIERS,
            (LotMembership.CONFLICTING),
            ev.sources,
        )
    if b == ctx.plan.building_bin:
        return (
            Attribution.THIS_BUILDING,
            AttributionBasis.ROW_IDENTIFIERS,
            (LotMembership.VIA_BIN_MAPPING),
            ev.sources,
        )
    return (
        Attribution.OTHER_BUILDING_ON_LOT,
        AttributionBasis.BIN_LOT_MAPPING,
        (LotMembership.VIA_BIN_MAPPING),
        ev.sources,
    )


def _status(raw: str | None) -> DobComplaintStatus:
    return {"ACTIVE": DobComplaintStatus.ACTIVE, "CLOSED": DobComplaintStatus.CLOSED}.get(
        (raw or "").upper(), DobComplaintStatus.UNKNOWN
    )


def _public_address(row: dict[str, Any]) -> tuple[str | None, RedactionInfo]:
    """DOB's building address, shown after the unit and floor redaction pass (18.2)."""
    redacted: list[str] = []
    replacements: Counter[str] = Counter()
    parts = []
    for f in ("house_number", "house_street"):
        text, counts = redact_text_detailed(_s(row.get(f)))
        if counts:
            redacted.append(f)
            replacements.update(counts)
        if text:
            parts.append(text)
    info = (
        RedactionInfo(
            applied="this_run",
            text_fields_redacted=redacted,
            text_replacements=dict(sorted(replacements.items())),
        )
        if redacted
        else RedactionInfo(applied="none")
    )
    return (" ".join(parts) or None), info


def normalize_row(
    row: dict[str, Any], ctx: _Ctx, *, query_url: str, fetched_at: str
) -> NormalizedDobComplaint:
    flags: list[QualityFlag] = []
    number = _s(row.get("complaint_number")) or "(none)"
    attribution, basis, membership, bin_sources = _attribute(ctx, _s(row.get("bin")), flags)

    status_raw = _s(row.get("status"))
    status = _status(status_raw)
    if status is DobComplaintStatus.UNKNOWN:
        flags.append(QualityFlag.STATUS_UNKNOWN)

    code = _s(row.get("disposition_code"))
    disp = classify_disposition(code)
    outcome: DobComplaintOutcome = disp["outcome"]
    if outcome is DobComplaintOutcome.UNKNOWN_CODE:
        flags.append(QualityFlag.DISPOSITION_CODE_UNKNOWN)
    if (status is DobComplaintStatus.ACTIVE and outcome in final_outcomes()) or (
        status is DobComplaintStatus.CLOSED and outcome is DobComplaintOutcome.NONE
    ):
        flags.append(QualityFlag.STATUS_FIELDS_DISAGREE)

    dates: dict[str, DateValue] = {}
    for name, src in (
        ("received", "date_entered"),
        ("inspection", "inspection_date"),
        ("disposition", "disposition_date"),
    ):
        value, flag = parse_dob_date(row.get(src), as_of=ctx.as_of)
        dates[name] = value
        if flag and flag not in flags:
            flags.append(flag)
    received = dates["received"].value
    for name in ("inspection", "disposition"):
        other = dates[name].value
        if received and other and other < received and QualityFlag.DATE_ORDER not in flags:
            flags.append(QualityFlag.DATE_ORDER)

    order_event = None
    if "order_event" in disp:
        when = dates["disposition"]
        order_event = OrderEvent(
            kind=disp["order_event"],
            action=disp["order_action"],
            disposition_code=code,
            dob_text=disp["definition"],
            disposition_date=when,
            complaint_number=number,
            statement=ORDER_STATEMENT.format(
                code=code,
                text=disp["definition"],
                date=when.value.isoformat() if when.value else "an unrecorded date",
                number=number,
            ),
        )

    category = _s(row.get("complaint_category"))
    cat = reference()["categories"].get(category) if category else None
    if category and cat is None:
        flags.append(QualityFlag.COMPLAINT_CATEGORY_UNKNOWN)

    address, redaction = _public_address(row)
    if redaction.text_fields_redacted:
        flags.append(QualityFlag.UNIT_DETAIL_REDACTED)

    public_row = {k: v for k, v in row.items() if k not in NOT_PUBLIC}
    for f in ("house_number", "house_street"):
        if f in redaction.text_fields_redacted:
            public_row[f] = redact_text_detailed(_s(row.get(f)))[0]
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, ctx.digest_key)
    redacted_sha = canonical_sha256(public_row)
    # DOB complaint rows are not sanitized in fixtures (nothing personal), so the row is
    # the original as received, live or replayed.
    basis_kind = DigestBasis.ORIGINAL_ROW
    if ctx.evidence is not None:
        ctx.evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=number,
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=basis_kind,
                redacted_sha256=redacted_sha,
                redacted_fields=list(NOT_PUBLIC) + redaction.text_fields_redacted,
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=ctx.data_as_of,
            )
        )

    return NormalizedDobComplaint(
        dataset_id=DATASET_ID,
        complaint_number=number,
        source_rows=[
            DobSourceRow(
                source_row_id=str(row.get(ROW_ID) or ""),
                original_digest=original_digest,
                query_url=query_url,
                fetched_at=fetched_at,
            )
        ],
        source_bin=_s(row.get("bin")),
        bin_sources=bin_sources,
        resolved_bbl=ctx.plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        attribution_basis=basis,
        complaint_category=category,
        complaint_category_description=cat["description"] if cat else None,
        complaint_category_priority=cat["priority"] if cat else None,
        complaint_status=status,
        status_raw=status_raw,
        disposition_code=code,
        disposition_description=disp["definition"],
        disposition_definition_source=disp["source"],
        outcome=outcome,
        order_event=order_event,
        received_date=dates["received"],
        inspection_date=dates["inspection"],
        disposition_date=dates["disposition"],
        dob_unit=_s(row.get("unit")),
        community_board=_s(row.get("community_board")),
        recorded_address=address,
        recorded_zip_code=_s(row.get("zip_code")),
        redaction=redaction,
        quality_flags=flags,
        original_digest=original_digest,
        digest_basis=basis_kind,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=ctx.data_as_of,
    )


def _content(row: dict[str, Any]) -> str:
    """A row's content without its Socrata row id (two copies always differ in :id)."""
    return canonical_sha256({k: v for k, v in row.items() if k != ROW_ID})


def normalize_fetch(fetch: PagedFetch, ctx: _Ctx) -> list[NormalizedDobComplaint]:
    out: list[NormalizedDobComplaint] = []
    seen: dict[str, dict[str, int]] = {}  # complaint_number -> {content sha: index}
    pages = {p.index: p for p in fetch.provenance.pages}
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        number = _s(row.get("complaint_number")) or "(none)"
        sha = _content(row)
        copies = seen.setdefault(number, {})
        # Every row is normalized, so every row's original goes to the evidence store
        # with its own digest and page lineage, even when it is then collapsed.
        record = normalize_row(
            row,
            ctx,
            query_url=page.url if page else "",
            fetched_at=page.fetched_at if page else "",
        )
        if sha in copies:
            kept = out[copies[sha]]
            kept.source_rows.extend(record.source_rows)
            if QualityFlag.DUPLICATE_ID_SAME_CONTENT not in kept.quality_flags:
                kept.quality_flags.append(QualityFlag.DUPLICATE_ID_SAME_CONTENT)
            continue
        copies[sha] = len(out)
        out.append(record)
    for copies in seen.values():
        if len(copies) < 2:
            continue
        for idx in copies.values():  # every differing copy is kept and fails closed
            rec = out[idx]
            if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT not in rec.quality_flags:
                rec.quality_flags.append(QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
            if rec.attribution in (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT):
                out[idx] = rec.model_copy(
                    update={
                        "attribution": Attribution.UNATTRIBUTED_ON_LOT,
                        "attribution_basis": AttributionBasis.ROW_IDENTIFIERS,
                    }
                )
    return out


# ---- Summary ------------------------------------------------------------------------------


def summarize(
    records: list[NormalizedDobComplaint],
    scope: JoinScope,
    data_as_of: str | None,
    as_of: dt.date,
    source_rows: int,
    *,
    resolved_bin_conflict: bool = False,
) -> DobComplaintSummary:
    buckets: dict[Attribution, AttributionBucket] = {}
    flags: Counter[str] = Counter()
    unknown_codes: Counter[str] = Counter()
    unknown_cats: Counter[str] = Counter()
    events: list[OrderEvent] = []
    finals = final_outcomes()
    active_final = closed_none = 0
    for r in records:
        b = buckets.setdefault(r.attribution, AttributionBucket())
        b.complaints += 1
        if r.complaint_status is DobComplaintStatus.ACTIVE:
            b.active += 1
        elif r.complaint_status is DobComplaintStatus.CLOSED:
            b.closed += 1
        else:
            b.status_unknown += 1
        b.by_outcome[r.outcome.value] = b.by_outcome.get(r.outcome.value, 0) + 1
        cat = r.complaint_category or "none"
        b.by_category[cat] = b.by_category.get(cat, 0) + 1
        bin_key = r.source_bin or "none"
        b.by_bin[bin_key] = b.by_bin.get(bin_key, 0) + 1
        if r.received_date.value:
            age = (as_of - r.received_date.value).days
            b.received_last_12_months += age <= 365
            b.received_last_36_months += age <= 3 * 365
        flags.update(f.value for f in r.quality_flags)
        if r.outcome is DobComplaintOutcome.UNKNOWN_CODE:
            unknown_codes[r.disposition_code or ""] += 1
        if QualityFlag.COMPLAINT_CATEGORY_UNKNOWN in r.quality_flags:
            unknown_cats[r.complaint_category or ""] += 1
        if r.order_event:
            events.append(r.order_event)
        active_final += r.complaint_status is DobComplaintStatus.ACTIVE and r.outcome in finals
        closed_none += (
            r.complaint_status is DobComplaintStatus.CLOSED
            and r.outcome is DobComplaintOutcome.NONE
        )
    for b in buckets.values():
        for d in (b.by_outcome, b.by_category, b.by_bin):
            items = sorted(d.items())
            d.clear()
            d.update(items)
    events.sort(key=lambda e: (e.disposition_date.value or dt.date.max, e.complaint_number))
    return DobComplaintSummary(
        data_as_of=data_as_of,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING and not resolved_bin_conflict,
        building_claims_denied_reason=(
            "the resolved BIN has conflicting lot evidence (bin_lot_conflict); no complaint "
            "is attributed to the requested building"
            if scope is JoinScope.BUILDING and resolved_bin_conflict
            else None
        ),
        total_complaints=len(records),
        source_rows=source_rows,
        by_attribution=dict(sorted(buckets.items())),
        order_events=events,
        status_fields_disagree=flags.get(QualityFlag.STATUS_FIELDS_DISAGREE.value, 0),
        active_with_final_disposition=active_final,
        closed_with_no_disposition=closed_none,
        unknown_disposition_codes=dict(sorted(unknown_codes.items())),
        unknown_category_codes=dict(sorted(unknown_cats.items())),
        quality_flag_counts=dict(sorted(flags.items())),
        conflict_counts={f.value: flags.get(f.value, 0) for f in sorted(DOB_CONFLICT_FLAGS)},
    )


# ---- Entry point --------------------------------------------------------------------------


def _result(**kw) -> DobComplaintResult:
    return DobComplaintResult(source=SOURCE, dataset_id=DATASET_ID, **kw)


def retrieve_dob_complaints(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    hpd_buildings: BuildingSourceResult | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> DobComplaintResult:
    """DOB complaints for a building-level resolution.

    `hpd_buildings` may pass a Task 3 result already retrieved for this resolution; it
    is fetched here otherwise. Nothing is requested at tax-lot scope.
    """
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
            resolved_bin=None,
            provenance=RetrievalProvenance(dataset_id=DATASET_ID),
        )
    if plan.scope is not JoinScope.BUILDING:
        return _result(
            status=RetrievalStatus.UNSUPPORTED_JOIN,
            status_detail=(
                "DOB complaints are keyed by BIN only, and a tax-lot resolution never "
                "authorizes a BIN join; no request was made"
            ),
            join_scope=plan.scope,
            resolved_bbl=plan.bbl,
            resolved_bin=None,
            provenance=RetrievalProvenance(dataset_id=DATASET_ID),
        )

    if hpd_buildings is None:
        hpd_buildings = retrieve_hpd_buildings(
            resolution, client, settings=settings, page_size=page_size, today=today
        )
    elif hpd_buildings.resolved_bbl != plan.bbl:
        raise ValueError("hpd_buildings was retrieved for a different lot")
    bins = plan_bins(client, plan, hpd_buildings, page_size=page_size)
    common = {
        "join_scope": plan.scope,
        "resolved_bbl": plan.bbl,
        "resolved_bin": plan.building_bin,
        "search_bins": bins.search_bins,
        "bin_evidence": sorted(bins.evidence.values(), key=lambda e: e.bin),
        "lot_family": bins.lot_family,
        "bin_lot_conflicts": sorted(bins.conflicts),
        "bin_evidence_provenance": bins.provenance,
        "bin_evidence_complete": not bins.incomplete_reasons,
        "coverage_notes": [COVERAGE_NOTE],
    }

    where = f"bin in ({_quote(bins.search_bins)})"
    try:
        fetch = fetch_all(
            client,
            DATASET_ID,
            where=where,
            order_by=ROW_ID,
            unique_key=ROW_ID,
            select=f"*, {ROW_ID}",
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
        )
    except SourceError as exc:
        logger.warning("%s unavailable: %s", DATASET_ID, type(exc).__name__)
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"{type(exc).__name__}: the source could not be checked",
            provenance=RetrievalProvenance(
                dataset_id=DATASET_ID, where=where, order_by=ROW_ID, error=str(exc)
            ),
            **common,
        )

    prov = fetch.provenance
    missing = [w for w in prov.warnings if w.startswith("fields_absent_from_all_rows")]
    if missing:
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"schema_changed: {missing[0]}",
            provenance=prov,
            **common,
        )

    as_of = _as_of_date(fetch, today)
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    ctx = _Ctx(plan, bins, as_of, digest_key, prov.data_as_of, evidence)
    records = normalize_fetch(fetch, ctx)
    prov.incomplete_reasons = prov.incomplete_reasons + bins.incomplete_reasons
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = f"the source reports 0 complaints for {len(bins.search_bins)} BIN(s) on this lot"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = (
            f"{len(records)} complaints from {len(fetch.rows)} source rows for "
            f"{len(bins.search_bins)} BIN(s); matches the source count"
        )
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
            resolved_bin_conflict=plan.building_bin in bins.conflicts,
        ),
        provenance=prov,
        **common,
    )
