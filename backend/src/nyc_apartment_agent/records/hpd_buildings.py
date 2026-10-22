"""HPD Buildings Subject to HPD Jurisdiction (kj4p-ruqc), Phase 2 Task 3, step 1.

Entry point: `retrieve_hpd_buildings(resolution, client)`.

What HPD has on file for each building on the resolved lot: HPD building ID, BIN, legal
units and stories, DOB building class, management program, the registration ID on the
building record, and the record's lifecycle and status.

Reused from Tasks 1-2: join policy, verified pagination, BIN classification, keyed
digest with digest basis, evidence store, the five statuses.

Specific to this dataset (measured live 2026-10-21, see the Task 3 design):
- No `bbl` field: the lot is queried by boroid + block + lot only.
- `buildingid` is unique (380,119 of 380,119), so it is the paging key.
- Several records can share one BIN (368,130 BIN values, 356,862 distinct), e.g. an
  inactive or demolished record next to the current one. Only records with lifecycle
  `Building` and status `Active` count as current; if more than one current record has
  the requested BIN, no single record is chosen.
- Data updates are monthly and were last made 2026-08-12. Freshness is reported with
  every result (data-update vs metadata-update timestamps kept apart).
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
from ..sources.socrata_metadata import fetch_freshness
from ..sources.socrata_pager import PagedFetch, fetch_all
from .admin_models import (
    BuildingBucket,
    BuildingSourceResult,
    BuildingSummary,
    HpdBuildingRecord,
    RequestedBuilding,
    RequestedBuildingStatus,
)
from .attribution import attribute, bin_flags
from .evidence import EvidenceStore
from .hpd_violations import _as_of_date, _digest_key, _s
from .join_policy import LotQueryPlan, UnsupportedJoinError, compose_bbl, plan_lot_query
from .models import (
    Attribution,
    DataFreshness,
    DigestBasis,
    JoinScope,
    LotMembership,
    QualityFlag,
    RawEvidence,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import canonical_sha256, is_sanitized, keyed_digest

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "kj4p-ruqc"
ORDER_BY = "buildingid"
REQUIRED_FIELDS = ("buildingid", "boroid", "block", "lot", "lifecycle", "recordstatus")


def lot_where(plan: LotQueryPlan) -> str:
    return f"boroid={plan.boroid:d} AND block={plan.block:d} AND lot={plan.lot:d}"


def _int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def lot_membership(plan: LotQueryPlan, row: dict[str, Any]) -> tuple[LotMembership, list]:
    """The row's boroid/block/lot must rebuild exactly the resolved BBL."""
    composite = compose_bbl(row.get("boroid"), row.get("block"), row.get("lot"))
    if composite is None:
        return LotMembership.CONFLICTING, [
            QualityFlag.LOT_PARTS_MISSING,
            QualityFlag.LOT_IDENTIFIER_CONFLICT,
        ]
    if composite != plan.bbl:
        return LotMembership.CONFLICTING, [QualityFlag.LOT_IDENTIFIER_CONFLICT]
    return LotMembership.CONFIRMED, []


def is_current(row: dict[str, Any]) -> bool:
    lifecycle = (_s(row.get("lifecycle")) or "").lower()
    status = (_s(row.get("recordstatus")) or "").lower()
    return lifecycle == "building" and status == "active"


def normalize_row(
    row: dict[str, Any],
    plan: LotQueryPlan,
    *,
    query_url: str,
    fetched_at: str,
    data_as_of: str | None,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
) -> HpdBuildingRecord:
    membership, flags = lot_membership(plan, row)
    bin_status, b_flags = bin_flags(row.get("bin"))
    flags = flags + b_flags
    attribution = attribute(plan, membership, bin_status, row.get("bin"))
    current = is_current(row)
    if not current:
        flags.append(QualityFlag.NON_CURRENT_RECORD)

    reg_raw = _s(row.get("registrationid"))
    reg_id = reg_raw if reg_raw and reg_raw != "0" else None
    number, street = _s(row.get("housenumber")), _s(row.get("streetname"))
    address = " ".join(x for x in (number, street) if x) or None

    digest_basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, digest_key)
    if evidence is not None:
        evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=str(row.get("buildingid")),
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=digest_basis,
                redacted_sha256=original_sha,  # nothing to redact in this dataset
                redacted_fields=[],
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
            )
        )
    return HpdBuildingRecord(
        dataset_id=DATASET_ID,
        building_id=str(row.get("buildingid")),
        source_boroid=_s(row.get("boroid")),
        source_block=_s(row.get("block")),
        source_lot=_s(row.get("lot")),
        source_bin=_s(row.get("bin")),
        resolved_bbl=plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        address=address,
        low_house_number=_s(row.get("lowhousenumber")),
        high_house_number=_s(row.get("highhousenumber")),
        zip=_s(row.get("zip")),
        management_program=_s(row.get("managementprogram")),
        dob_building_class=_s(row.get("dobbuildingclass")),
        legal_stories=_int(row.get("legalstories")),
        legal_class_a_units=_int(row.get("legalclassa")),
        legal_class_b_units=_int(row.get("legalclassb")),
        registration_id=reg_id,
        registration_id_raw=reg_raw,
        lifecycle=_s(row.get("lifecycle")),
        record_status=_s(row.get("recordstatus")),
        is_current=current,
        quality_flags=flags,
        redaction=RedactionInfo(applied="none"),
        original_digest=original_digest,
        digest_basis=digest_basis,
        redacted_sha256=original_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=data_as_of,
    )


def normalize_fetch(
    fetch: PagedFetch,
    plan: LotQueryPlan,
    *,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
) -> list[HpdBuildingRecord]:
    pages = {p.index: p for p in fetch.provenance.pages}
    out: list[HpdBuildingRecord] = []
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        out.append(
            normalize_row(
                row,
                plan,
                query_url=page.url if page else "",
                fetched_at=page.fetched_at if page else "",
                data_as_of=fetch.provenance.data_as_of,
                digest_key=digest_key,
                evidence=evidence,
            )
        )
    # More than one current record for one assigned BIN: flag them all.
    per_bin = Counter(
        r.source_bin
        for r in out
        if r.is_current and r.lot_membership is LotMembership.CONFIRMED and _assigned(r)
    )
    for r in out:
        if r.is_current and _assigned(r) and per_bin.get(r.source_bin, 0) > 1:
            r.quality_flags.append(QualityFlag.MULTIPLE_RECORDS_FOR_BIN)
    return out


def _assigned(r: HpdBuildingRecord) -> bool:
    return not any(
        f in r.quality_flags
        for f in (QualityFlag.BIN_MISSING, QualityFlag.BIN_PLACEHOLDER, QualityFlag.BIN_INVALID)
    )


def requested_building(
    records: list[HpdBuildingRecord], scope: JoinScope | None
) -> RequestedBuilding:
    if scope is not JoinScope.BUILDING:
        return RequestedBuilding(status=RequestedBuildingStatus.NOT_APPLICABLE_TAX_LOT)
    mine = [r for r in records if r.attribution is Attribution.THIS_BUILDING]
    current = [r for r in mine if r.is_current]
    ids = [r.building_id for r in mine]
    if len(current) == 1:
        r = current[0]
        return RequestedBuilding(
            status=RequestedBuildingStatus.ONE_CURRENT_RECORD,
            building_id=r.building_id,
            legal_class_a_units=r.legal_class_a_units,
            legal_class_b_units=r.legal_class_b_units,
            legal_stories=r.legal_stories,
            dob_building_class=r.dob_building_class,
            management_program=r.management_program,
            registration_id=r.registration_id,
            candidate_building_ids=ids,
        )
    if len(current) > 1:
        status = RequestedBuildingStatus.MULTIPLE_CURRENT_RECORDS
    elif mine:
        status = RequestedBuildingStatus.ONLY_NON_CURRENT_RECORDS
    else:
        status = RequestedBuildingStatus.NO_RECORD_FOR_BIN
    return RequestedBuilding(status=status, candidate_building_ids=ids)


def summarize(
    records: list[HpdBuildingRecord],
    scope: JoinScope | None,
    data_as_of: str | None,
    freshness: DataFreshness | None,
) -> BuildingSummary:
    buckets: dict[Attribution, BuildingBucket] = {}
    flags: Counter[str] = Counter()
    lifecycles: Counter[str] = Counter()
    for r in records:
        b = buckets.setdefault(r.attribution, BuildingBucket())
        b.records += 1
        b.building_ids.append(r.building_id)
        if r.is_current:
            b.current += 1
            b.current_legal_class_a_units += r.legal_class_a_units or 0
        else:
            b.non_current += 1
        flags.update(f.value for f in r.quality_flags)
        lifecycles[f"{r.lifecycle or 'unknown'}/{r.record_status or 'unknown'}"] += 1
    return BuildingSummary(
        data_as_of=data_as_of,
        freshness=freshness,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING,
        total_records=len(records),
        current_records=sum(1 for r in records if r.is_current),
        by_attribution=buckets,
        requested_building=requested_building(records, scope),
        quality_flag_counts=dict(sorted(flags.items())),
        lifecycle_counts=dict(sorted(lifecycles.items())),
    )


def retrieve_hpd_buildings(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> BuildingSourceResult:
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap
    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return BuildingSourceResult(
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
            unique_key="buildingid",
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
        )
    except SourceError as exc:
        logger.warning("%s unavailable for %s: %s", DATASET_ID, plan.bbl, type(exc).__name__)
        return BuildingSourceResult(
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

    freshness = fetch_freshness(client, DATASET_ID, today=_as_of_date(fetch, today))
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    records = normalize_fetch(fetch, plan, digest_key=digest_key, evidence=evidence)
    prov = fetch.provenance
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = "the source reports 0 HPD building records for this lot"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = f"{len(records)} HPD building records; matches the source count"
    return BuildingSourceResult(
        source=SOURCE,
        dataset_id=DATASET_ID,
        status=status,
        status_detail=detail,
        join_scope=plan.scope,
        resolved_bbl=plan.bbl,
        resolved_bin=plan.building_bin,
        records=records,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summarize(records, plan.scope, prov.data_as_of, freshness),
        freshness=freshness,
        provenance=prov,
    )
