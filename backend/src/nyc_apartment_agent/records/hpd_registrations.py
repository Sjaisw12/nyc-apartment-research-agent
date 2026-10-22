"""HPD Multiple Dwelling Registrations (tesw-yqqr), Phase 2 Task 3, step 2.

Entry point: `retrieve_hpd_registrations(resolution, client, buildings=...)`.

Measured live 2026-10-21/22 (see the Task 3 design):
- No unique business key: 194,218 registration IDs over 203,887 rows, and some
  (registrationid, buildingid) pairs repeat with different content (e.g. two periods).
  Pages are therefore ordered by Socrata's row id `:id`, which is unique, and duplicate
  business keys are flagged, never used to skip or merge rows.
- One registration can cover several buildings; rows are joined to buildings through
  `buildingid` (and cross-checked against the HPD building record when given), never
  through address strings.
- The data was last updated 2026-08-12 and most end dates are 2026-09-01. Status says
  what the snapshot shows (approved D10/D12); it never says expired or unregistered.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
from typing import Any

from ..config import Settings, get_settings
from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..resolver.models import ResolutionResult
from ..sources.socrata_metadata import fetch_freshness
from ..sources.socrata_pager import PagedFetch, fetch_all
from .admin_models import (
    REGISTRATION_STATUS_NOTE,
    BuildingRegistration,
    BuildingSourceResult,
    HpdRegistrationRecord,
    RegistrationSourceResult,
    RegistrationStatus,
    RegistrationSummary,
)
from .attribution import attribute, bin_flags
from .evidence import EvidenceStore
from .hpd_buildings import lot_membership, lot_where
from .hpd_violations import _as_of_date, _digest_key, _s, parse_date
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    Attribution,
    DataFreshness,
    DateValue,
    DigestBasis,
    JoinScope,
    QualityFlag,
    RawEvidence,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import canonical_sha256, is_sanitized, keyed_digest

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "tesw-yqqr"
ORDER_BY = ":id"
SELECT = "*, :id"
REQUIRED_FIELDS = ("registrationid", "buildingid", "boroid", "block", "lot")
BUILDING_ONLY = (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT)


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
) -> HpdRegistrationRecord:
    membership, flags = lot_membership(plan, row)
    bin_status, b_flags = bin_flags(row.get("bin"))
    flags = flags + b_flags
    attribution = attribute(plan, membership, bin_status, row.get("bin"))
    last, f1 = parse_date(row.get("lastregistrationdate"), deadline=False, as_of=as_of)
    end, f2 = parse_date(row.get("registrationenddate"), deadline=True, as_of=as_of)
    flags += [f for f in (f1, f2) if f and f not in flags]
    number, street = _s(row.get("housenumber")), _s(row.get("streetname"))

    digest_basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, digest_key)
    if evidence is not None:
        evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=str(row.get(":id")),
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=digest_basis,
                redacted_sha256=original_sha,  # no personal fields in this dataset
                redacted_fields=[],
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
            )
        )
    return HpdRegistrationRecord(
        dataset_id=DATASET_ID,
        source_row_id=str(row.get(":id")),
        registration_id=str(row.get("registrationid")),
        building_id=_s(row.get("buildingid")),
        source_boroid=_s(row.get("boroid")),
        source_block=_s(row.get("block")),
        source_lot=_s(row.get("lot")),
        source_bin=_s(row.get("bin")),
        resolved_bbl=plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        address=" ".join(x for x in (number, street) if x) or None,
        last_registration_date=last,
        registration_end_date=end,
        buildings_on_lot_with_this_registration=0,  # filled in normalize_fetch
        quality_flags=flags,
        redaction=RedactionInfo(applied="none"),
        original_digest=original_digest,
        digest_basis=digest_basis,
        redacted_sha256=original_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=data_as_of,
    )


def _flag(rec: HpdRegistrationRecord, flag: QualityFlag) -> None:
    if flag not in rec.quality_flags:
        rec.quality_flags.append(flag)


def _downgrade(rec: HpdRegistrationRecord) -> None:
    if rec.attribution in BUILDING_ONLY:
        rec.attribution = Attribution.UNATTRIBUTED_ON_LOT


def normalize_fetch(
    fetch: PagedFetch,
    plan: LotQueryPlan,
    *,
    as_of: dt.date,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
    buildings: BuildingSourceResult | None = None,
) -> list[HpdRegistrationRecord]:
    pages = {p.index: p for p in fetch.provenance.pages}
    out: list[HpdRegistrationRecord] = []
    seen: dict[tuple, dict[str, int]] = {}  # (registrationid, buildingid) -> {sha: index}
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        key = (str(row.get("registrationid")), str(row.get("buildingid")))
        content = {k: v for k, v in row.items() if k != ":id"}
        sha = canonical_sha256(content)
        copies = seen.setdefault(key, {})
        if sha in copies:  # same business key, same content, different row id
            _flag(out[copies[sha]], QualityFlag.DUPLICATE_ID_SAME_CONTENT)
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

    # Several rows for one building: history (e.g. renewals) or a contradiction.
    by_building: dict[str | None, list[HpdRegistrationRecord]] = defaultdict(list)
    for r in out:
        by_building[r.building_id].append(r)
    for rows in by_building.values():
        if len(rows) > 1:
            for r in rows:
                _flag(r, QualityFlag.MULTIPLE_REGISTRATION_ROWS)
            if len({r.source_bin for r in rows}) > 1:  # same building, different BINs
                for r in rows:
                    _flag(r, QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
                    _downgrade(r)

    # One registration id covering several buildings on this lot.
    per_reg = Counter(reg for reg, _building in seen)
    for r in out:
        r.buildings_on_lot_with_this_registration = per_reg[r.registration_id]
        if per_reg[r.registration_id] > 1:
            _flag(r, QualityFlag.SHARED_REGISTRATION)

    # Cross-check against HPD's building records, joined by building id only.
    if buildings is not None and buildings.status in (
        RetrievalStatus.RECORDS_FOUND,
        RetrievalStatus.NO_RECORDS_FOUND,
    ):
        known = {b.building_id: b for b in buildings.records}
        for r in out:
            b = known.get(r.building_id or "")
            mismatch = b is None or (
                b.source_bin is not None
                and r.source_bin is not None
                and b.source_bin != r.source_bin
            )
            if mismatch:
                _flag(r, QualityFlag.BUILDING_RECORD_MISMATCH)
                _downgrade(r)
    return out


def data_update_date(
    freshness: DataFreshness | None, truth_header: str | None
) -> tuple[dt.date | None, str]:
    """The date of the dataset's last DATA update (not a metadata edit)."""
    if freshness is not None and freshness.data_updated_at:
        return dt.datetime.fromisoformat(freshness.data_updated_at).date(), "catalog rowsUpdatedAt"
    if truth_header:
        try:
            return parsedate_to_datetime(truth_header).date(), "X-SODA2-Truth-Last-Modified"
        except (TypeError, ValueError):
            pass
    return None, "unknown"


def registration_status(
    end: DateValue | None, today: dt.date, data_updated: dt.date | None, any_rows: bool
) -> RegistrationStatus:
    if not any_rows:
        return RegistrationStatus.NONE_ON_FILE
    if end is None or end.value is None:
        return RegistrationStatus.ON_FILE_END_DATE_UNKNOWN
    if end.value > today:
        return RegistrationStatus.ON_FILE_END_DATE_NOT_PASSED
    if data_updated is None:
        return RegistrationStatus.ON_FILE_END_DATE_PASSED_FRESHNESS_UNKNOWN
    if end.value > data_updated:
        return RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE
    return RegistrationStatus.ON_FILE_END_DATE_PASSED_BEFORE_DATA_UPDATE


def _latest(rows: list[HpdRegistrationRecord]) -> HpdRegistrationRecord | None:
    dated = [r for r in rows if r.registration_end_date.value is not None]
    if not dated:
        return rows[0] if rows else None
    return max(
        dated,
        key=lambda r: (
            r.registration_end_date.value,
            r.last_registration_date.value or dt.date.min,
        ),
    )


def building_registration(
    building_id: str | None,
    rows: list[HpdRegistrationRecord],
    attribution: Attribution,
    today: dt.date,
    data_updated: dt.date | None,
    basis_source: str,
) -> BuildingRegistration:
    latest = _latest(rows)
    status = registration_status(
        latest.registration_end_date if latest else None, today, data_updated, bool(rows)
    )
    return BuildingRegistration(
        building_id=building_id,
        attribution=attribution,
        status=status,
        note=REGISTRATION_STATUS_NOTE[status],
        registration_ids=sorted({r.registration_id for r in rows}),
        latest_registration_id=latest.registration_id if latest else None,
        latest_last_registration_date=latest.last_registration_date if latest else None,
        latest_end_date=latest.registration_end_date if latest else None,
        rows_on_file=len(rows),
        status_basis={
            "judged_on": today.isoformat(),
            "data_updated_on": data_updated.isoformat() if data_updated else None,
            "data_updated_source": basis_source,
        },
    )


def summarize(
    records: list[HpdRegistrationRecord],
    scope: JoinScope | None,
    prov: RetrievalProvenance,
    freshness: DataFreshness | None,
    today: dt.date,
    buildings: BuildingSourceResult | None = None,
) -> RegistrationSummary:
    data_updated, basis_source = data_update_date(freshness, prov.data_as_of)
    groups: dict[str | None, list[HpdRegistrationRecord]] = defaultdict(list)
    for r in records:
        groups[r.building_id].append(r)
    per_building = []
    for bid, rows in sorted(groups.items(), key=lambda kv: kv[0] or ""):
        attrs = {r.attribution for r in rows}
        attribution = attrs.pop() if len(attrs) == 1 else Attribution.UNATTRIBUTED_ON_LOT
        per_building.append(
            building_registration(bid, rows, attribution, today, data_updated, basis_source)
        )

    requested = None
    if scope is JoinScope.BUILDING:
        mine = [b for b in per_building if b.attribution is Attribution.THIS_BUILDING]
        if len(mine) == 1:
            requested = mine[0]
        else:
            rows = [r for r in records if r.attribution is Attribution.THIS_BUILDING]
            bid = None
            if not rows and buildings is not None and buildings.summary is not None:
                bid = buildings.summary.requested_building.building_id
            requested = building_registration(
                bid, rows, Attribution.THIS_BUILDING, today, data_updated, basis_source
            )
    flags: Counter[str] = Counter()
    for r in records:
        flags.update(f.value for f in r.quality_flags)
    return RegistrationSummary(
        data_as_of=prov.data_as_of,
        freshness=freshness,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING,
        total_rows=len(records),
        registration_ids=len({r.registration_id for r in records}),
        buildings=per_building,
        requested_building=requested,
        quality_flag_counts=dict(sorted(flags.items())),
    )


def retrieve_hpd_registrations(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    buildings: BuildingSourceResult | None = None,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> RegistrationSourceResult:
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap
    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return RegistrationSourceResult(
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
            unique_key=":id",
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
            select=SELECT,
        )
    except SourceError as exc:
        logger.warning("%s unavailable for %s: %s", DATASET_ID, plan.bbl, type(exc).__name__)
        return RegistrationSourceResult(
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
    freshness = fetch_freshness(client, DATASET_ID, today=as_of)
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    records = normalize_fetch(
        fetch, plan, as_of=as_of, digest_key=digest_key, evidence=evidence, buildings=buildings
    )
    prov = fetch.provenance
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = "the source reports 0 registration rows for this lot (in this snapshot)"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = f"{len(records)} registration rows; matches the source count"
    summary = summarize(records, plan.scope, prov, freshness, as_of, buildings)
    if status is RetrievalStatus.INCOMPLETE_PAGINATION and summary.requested_building:
        # A partial fetch cannot support "none on file" or a latest-row status.
        summary.requested_building = None
    return RegistrationSourceResult(
        source=SOURCE,
        dataset_id=DATASET_ID,
        status=status,
        status_detail=detail,
        join_scope=plan.scope,
        resolved_bbl=plan.bbl,
        resolved_bin=plan.building_bin,
        records=records,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summary,
        freshness=freshness,
        provenance=prov,
    )
