"""HPD Registration Contacts (feu5-w2e2), Phase 2 Task 3, step 3.

Entry point: `retrieve_hpd_contacts(resolution, client, registrations=...)`.

Contacts are looked up only for registration IDs returned by the registrations step,
never by address. Each batch of IDs is its own verified fetch (count, :id paging,
freshness header); a failed batch makes the contacts result incomplete without touching
the building or registration results, which keep their own statuses.

Privacy (approved D11): corporate names and registered roles are public. Person names
and titles, street, house number and suite are withheld for every contact, and city,
state and zip too when the contact names a person. Originals stay in the evidence store.

Measured live 2026-10-22: `registrationcontactid` is NOT unique (759,625 distinct over
810,494 rows; the repeats sit mostly on registration id 0), so pages are ordered by
Socrata's `:id`.
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
from ..sources.socrata_pager import fetch_all
from .admin_models import (
    ContactRole,
    ContactSourceResult,
    ContactSummary,
    HpdRegistrationContact,
    RegistrationContacts,
    RegistrationSourceResult,
)
from .evidence import EvidenceStore
from .hpd_violations import _digest_key, _s
from .join_policy import UnsupportedJoinError, plan_lot_query
from .models import (
    Attribution,
    DigestBasis,
    JoinScope,
    RawEvidence,
    RedactionInfo,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import (
    WITHHELD,
    canonical_sha256,
    contact_names_a_person,
    is_sanitized,
    keyed_digest,
    sanitize_contact,
)

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "feu5-w2e2"
ORDER_BY = ":id"
SELECT = "*, :id"
BATCH_SIZE = 50
REQUIRED_FIELDS = ("registrationcontactid", "registrationid", "type")
ROLES = {
    "corporateowner": ContactRole.CORPORATE_OWNER,
    "individualowner": ContactRole.INDIVIDUAL_OWNER,
    "jointowner": ContactRole.JOINT_OWNER,
    "headofficer": ContactRole.HEAD_OFFICER,
    "officer": ContactRole.OFFICER,
    "shareholder": ContactRole.SHAREHOLDER,
    "agent": ContactRole.AGENT,
    "sitemanager": ContactRole.SITE_MANAGER,
    "lessee": ContactRole.LESSEE,
}


def role_of(raw: object) -> ContactRole:
    return ROLES.get((_s(raw) or "").replace(" ", "").lower(), ContactRole.OTHER)


def registration_attribution(registrations: RegistrationSourceResult) -> dict[str, tuple]:
    """registration id -> (attribution, building ids on this lot) from the registrations."""
    out: dict[str, tuple[Attribution, list[str]]] = {}
    if registrations.summary is None:
        return out
    by_reg: dict[str, list] = {}
    for b in registrations.summary.buildings:
        for reg in b.registration_ids:
            by_reg.setdefault(reg, []).append(b)
    order = (
        Attribution.THIS_BUILDING,
        Attribution.OTHER_BUILDING_ON_LOT,
        Attribution.LOT_LEVEL,
        Attribution.UNATTRIBUTED_ON_LOT,
    )
    for reg, blds in by_reg.items():
        attrs = {b.attribution for b in blds}
        attribution = next(a for a in order if a in attrs)
        out[reg] = (attribution, sorted(b.building_id or "" for b in blds))
    return out


def normalize_row(
    row: dict[str, Any],
    attribution: Attribution,
    *,
    query_url: str,
    fetched_at: str,
    data_as_of: str | None,
    digest_key: bytes,
    evidence: EvidenceStore | None = None,
) -> HpdRegistrationContact:
    digest_basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    public, withheld_now = sanitize_contact(row)
    withheld = sorted(k for k, v in public.items() if v == WITHHELD)
    person = contact_names_a_person(row) or any(
        public.get(f) == WITHHELD for f in ("firstname", "lastname", "middleinitial", "title")
    )
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, digest_key)
    redacted_sha = canonical_sha256(public)
    if evidence is not None:
        evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=str(row.get(":id")),
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=digest_basis,
                redacted_sha256=redacted_sha,
                redacted_fields=withheld_now,
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=data_as_of,
            )
        )
    applied = "this_run" if withheld_now else ("upstream" if withheld else "none")

    def pub(field: str) -> str | None:
        value = _s(public.get(field))
        return None if person or value == WITHHELD else value

    return HpdRegistrationContact(
        dataset_id=DATASET_ID,
        source_row_id=str(row.get(":id")),
        registration_contact_id=_s(row.get("registrationcontactid")),
        registration_id=str(row.get("registrationid")),
        role=role_of(row.get("type")),
        role_raw=_s(row.get("type")),
        registration_description=_s(row.get("contactdescription")),
        corporation_name=_s(row.get("corporationname")),
        names_a_person=person,
        public_city=pub("businesscity"),
        public_state=pub("businessstate"),
        public_zip=pub("businesszip"),
        attribution=attribution,
        withheld_fields=withheld,
        redaction=RedactionInfo(applied=applied, fields_masked=withheld),
        original_digest=original_digest,
        digest_basis=digest_basis,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=data_as_of,
    )


def _no_lookup(registrations, plan, status, detail) -> ContactSourceResult:
    prov = RetrievalProvenance(dataset_id=DATASET_ID)
    if status is RetrievalStatus.NO_RECORDS_FOUND:
        prov.source_count = 0
    return ContactSourceResult(
        source=SOURCE,
        dataset_id=DATASET_ID,
        status=status,
        status_detail=detail,
        join_scope=plan.scope if plan else None,
        resolved_bbl=plan.bbl if plan else None,
        resolved_bin=plan.building_bin if plan else None,
        provenance=prov,
    )


def retrieve_hpd_contacts(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    registrations: RegistrationSourceResult,
    settings: Settings | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    batch_size: int = BATCH_SIZE,
    today: dt.date | None = None,
) -> ContactSourceResult:
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap
    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return _no_lookup(registrations, None, RetrievalStatus.UNSUPPORTED_JOIN, str(exc))

    reg_status = registrations.status
    if reg_status is RetrievalStatus.UNSUPPORTED_JOIN:
        return _no_lookup(
            registrations, None, RetrievalStatus.UNSUPPORTED_JOIN, "no supported join"
        )
    if reg_status is RetrievalStatus.SOURCE_UNAVAILABLE:
        return _no_lookup(
            registrations,
            plan,
            RetrievalStatus.SOURCE_UNAVAILABLE,
            "registrations could not be checked, so contacts could not be looked up",
        )
    raw_ids = {r.registration_id for r in registrations.records}
    ids = sorted((i for i in raw_ids if i.isdigit() and i != "0"), key=int)
    skipped = len(raw_ids) - len(ids)  # only plain integers are ever put into a query
    if not ids and not skipped:
        return _no_lookup(
            registrations,
            plan,
            RetrievalStatus.NO_RECORDS_FOUND,
            "no registration on file in this snapshot, so there are no contacts to look up",
        )

    attribution_of = registration_attribution(registrations)
    agg = RetrievalProvenance(
        dataset_id=DATASET_ID,
        where=f"registrationid in (<{len(ids)} ids>) in batches of {batch_size}",
        order_by=ORDER_BY,
        page_size=page_size,
        row_cap=row_cap,
        source_count=0,
    )
    batches: list[RetrievalProvenance] = []
    reasons: list[str] = []
    if skipped:
        reasons.append(f"invalid_registration_ids_skipped: {skipped}")
    if reg_status is RetrievalStatus.INCOMPLETE_PAGINATION:
        reasons.append("registration_list_incomplete: contacts cover only the rows retrieved")
    records: list[HpdRegistrationContact] = []
    seen: set[str] = set()
    checked = 0
    truths: set[str | None] = set()
    for n, start in enumerate(range(0, len(ids), batch_size)):
        chunk = ids[start : start + batch_size]
        where = f"registrationid in ({','.join(chunk)})"
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
            logger.warning("%s batch %d unavailable: %s", DATASET_ID, n, type(exc).__name__)
            reasons.append(f"batch_{n}_unavailable: {type(exc).__name__}")
            batches.append(RetrievalProvenance(dataset_id=DATASET_ID, where=where, error=str(exc)))
            continue
        prov = fetch.provenance
        batches.append(prov)
        checked += len(chunk)
        agg.source_count = (agg.source_count or 0) + (prov.source_count or 0)
        agg.rows_fetched += prov.rows_fetched
        agg.pages.extend(prov.pages)
        agg.restarts += prov.restarts
        agg.warnings.extend(prov.warnings)
        reasons.extend(f"batch_{n}: {r}" for r in prov.incomplete_reasons)
        truths.add(prov.data_as_of)
        pages = {p.index: p for p in prov.pages}
        for i, row in enumerate(fetch.rows):
            content = canonical_sha256({k: v for k, v in row.items() if k != ":id"})
            if content in seen:
                continue  # identical copy under another :id
            seen.add(content)
            page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
            attribution, _ = attribution_of.get(
                str(row.get("registrationid")), (Attribution.UNATTRIBUTED_ON_LOT, [])
            )
            if digest_key is None:
                digest_key = _digest_key(settings, fetch)
            records.append(
                normalize_row(
                    row,
                    attribution,
                    query_url=page.url if page else "",
                    fetched_at=page.fetched_at if page else "",
                    data_as_of=prov.data_as_of,
                    digest_key=digest_key,
                    evidence=evidence,
                )
            )
    if len(truths) > 1:
        reasons.append("dataset_changed_between_batches")
    elif truths:
        agg.data_as_of = next(iter(truths))
    agg.incomplete_reasons = reasons

    if checked == 0 and ids:
        status = RetrievalStatus.SOURCE_UNAVAILABLE
        detail = "no contact batch could be checked"
        records = []
    elif reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(reasons)
    elif agg.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = "the source reports 0 contacts for these registrations"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = f"{len(records)} contacts for {len(ids)} registrations; matches the counts"
    freshness = fetch_freshness(client, DATASET_ID, today=today)
    summary = None
    if status is not RetrievalStatus.SOURCE_UNAVAILABLE:
        summary = summarize(records, plan.scope, agg, freshness, ids, checked, attribution_of)
    return ContactSourceResult(
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
        provenance=agg,
        batch_provenance=batches,
    )


def summarize(records, scope, prov, freshness, ids, checked, attribution_of) -> ContactSummary:
    by_reg: list[RegistrationContacts] = []
    withheld: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    for r in records:
        withheld.update(r.withheld_fields)
        roles[r.role.value] += 1
    for reg in ids:
        mine = [r for r in records if r.registration_id == reg]
        attribution, building_ids = attribution_of.get(reg, (Attribution.UNATTRIBUTED_ON_LOT, []))
        corp: dict[str, list[str]] = {}
        for r in mine:
            if r.corporation_name:
                corp.setdefault(r.role.value, [])
                if r.corporation_name not in corp[r.role.value]:
                    corp[r.role.value].append(r.corporation_name)
        by_reg.append(
            RegistrationContacts(
                registration_id=reg,
                attribution=attribution,
                building_ids=building_ids,
                contacts=len(mine),
                roles=dict(Counter(r.role.value for r in mine)),
                corporate_names_by_role=corp,
                persons_named_withheld=sum(1 for r in mine if r.names_a_person),
            )
        )
    ids_seen = Counter(r.registration_contact_id for r in records)
    return ContactSummary(
        data_as_of=prov.data_as_of,
        freshness=freshness,
        join_scope=scope,
        building_specific_claims_allowed=scope is JoinScope.BUILDING,
        registrations_requested=len(ids),
        registrations_checked=checked,
        total_contacts=len(records),
        by_registration=by_reg,
        requested_building=(
            [c for c in by_reg if c.attribution is Attribution.THIS_BUILDING]
            if scope is JoinScope.BUILDING
            else None
        ),
        withheld_field_counts=dict(sorted(withheld.items())),
        role_counts=dict(sorted(roles.items())),
        duplicate_contact_ids=sum(1 for n in ids_seen.values() if n > 1),
    )
