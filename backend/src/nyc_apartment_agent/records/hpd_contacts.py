"""HPD Registration Contacts (feu5-w2e2), Phase 2 Task 3, step 3.

Entry point: `retrieve_hpd_contacts(resolution, client, registrations=...)`.

Contacts are looked up only for registration IDs returned by the registrations step,
never by address. Each batch of IDs is its own verified fetch (count, :id paging,
freshness header); a failed batch makes the contacts result incomplete without touching
the building or registration results, which keep their own statuses.

Privacy (approved D11): corporate names and registered roles are public. Person names
and titles, street, house number and suite are withheld for every contact, and city,
state and zip too when the contact names a person. Originals stay in the evidence store.

Contacts belong to a registration, and one registration can cover several HPD buildings,
on this lot or others (measured 2026-10-22: 80 registration IDs cover more than one
building, 55 of them across lots, one covering 87 buildings). So the registrations dataset
is also queried by registration ID to list every covered building, and a contact is never
presented as belonging exclusively to the requested building unless that check completed.

Identical rows under different :ids are collapsed into one record, but every source row
stays listed (`source_rows`, with its own keyed digest and evidence-store entry), and
source-row counts are reported next to normalized counts.

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
    ASSOCIATION_NOTE,
    ContactAssociation,
    ContactRole,
    ContactSourceResult,
    ContactSummary,
    HpdRegistrationContact,
    RegistrationContacts,
    RegistrationSourceResult,
    SourceRowRef,
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
REGISTRATIONS_DATASET = "tesw-yqqr"
COVERAGE_SELECT = "registrationid, buildingid, boroid, block, lot, :id"
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
        source_rows=[
            SourceRowRef(source_row_id=str(row.get(":id")), original_digest=original_digest)
        ],
        registration_contact_id=_s(row.get("registrationcontactid")),
        registration_id=str(row.get("registrationid")),
        association=ContactAssociation.UNATTRIBUTED,  # set once coverage is known
        registration_shared=None,
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
    if status is RetrievalStatus.INCOMPLETE_PAGINATION:
        prov.incomplete_reasons = [detail]
    return ContactSourceResult(
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
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
    if not ids and not skipped and reg_status is RetrievalStatus.INCOMPLETE_PAGINATION:
        # The registration list itself is partial: absence of contacts is not shown.
        return _no_lookup(
            registrations,
            plan,
            RetrievalStatus.INCOMPLETE_PAGINATION,
            "registration_list_incomplete: no registration ID was retrieved, so contacts "
            "could not be looked up; this is not evidence that none exist",
        )
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
    seen: dict[str, int] = {}  # content hash without :id -> index in records
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
            page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
            attribution, _ = attribution_of.get(
                str(row.get("registrationid")), (Attribution.UNATTRIBUTED_ON_LOT, [])
            )
            if digest_key is None:
                digest_key = _digest_key(settings, fetch)
            record = normalize_row(  # every source row reaches the evidence store
                row,
                attribution,
                query_url=page.url if page else "",
                fetched_at=page.fetched_at if page else "",
                data_as_of=prov.data_as_of,
                digest_key=digest_key,
                evidence=evidence,
            )
            if content in seen:  # identical copy under another :id: keep the row reference
                records[seen[content]].source_rows.extend(record.source_rows)
                continue
            seen[content] = len(records)
            records.append(record)
    if len(truths) > 1:
        reasons.append("dataset_changed_between_batches")
    elif truths:
        agg.data_as_of = next(iter(truths))
    agg.incomplete_reasons = reasons

    coverage, coverage_status, coverage_provs = ({}, "not_needed", [])
    if records:
        coverage, coverage_status, coverage_provs = fetch_coverage(
            client, ids, batch_size=batch_size, page_size=page_size, row_cap=row_cap
        )
    association_of = associations(attribution_of, coverage, ids)
    for r in records:
        assoc, shared, _covered = association_of[r.registration_id]
        r.association, r.registration_shared = assoc, shared

    n_rows = sum(len(r.source_rows) for r in records)
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
        detail = (
            f"{n_rows} source rows for {len(ids)} registrations match the source counts; "
            f"{len(records)} normalized contacts ({n_rows - len(records)} identical copies "
            "collapsed, each row still listed)"
        )
    freshness = fetch_freshness(client, DATASET_ID, today=today)
    summary = None
    if status is not RetrievalStatus.SOURCE_UNAVAILABLE:
        summary = summarize(
            records,
            plan.scope,
            agg,
            freshness,
            ids,
            checked,
            attribution_of,
            association_of,
            coverage_status,
        )
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
        coverage_provenance=coverage_provs,
    )


def fetch_coverage(
    client: SourceHttpClient, ids: list[str], *, batch_size: int, page_size: int, row_cap: int
) -> tuple[dict[str, set[str]], str, list[RetrievalProvenance]]:
    """registration id -> every HPD building id with that registration (citywide).

    Only ids from batches that were fetched completely are returned; the status is
    complete, incomplete (some batches missing or partial) or unavailable (none)."""
    covered: dict[str, set[str]] = {}
    provs: list[RetrievalProvenance] = []
    complete_batches = 0
    batches = [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]
    for n, chunk in enumerate(batches):
        where = f"registrationid in ({','.join(chunk)})"
        try:
            fetch = fetch_all(
                client,
                REGISTRATIONS_DATASET,
                where=where,
                order_by=ORDER_BY,
                unique_key=":id",
                page_size=page_size,
                row_cap=row_cap,
                required_fields=("registrationid", "buildingid"),
                select=COVERAGE_SELECT,
            )
        except SourceError as exc:
            logger.warning("coverage batch %d unavailable: %s", n, type(exc).__name__)
            provs.append(
                RetrievalProvenance(dataset_id=REGISTRATIONS_DATASET, where=where, error=str(exc))
            )
            continue
        provs.append(fetch.provenance)
        rows_ok = all(_s(row.get("buildingid")) for row in fetch.rows)
        if fetch.provenance.incomplete_reasons or not rows_ok:
            continue
        complete_batches += 1
        for reg in chunk:
            covered[reg] = set()
        for row in fetch.rows:
            reg = str(row.get("registrationid"))
            if reg in covered:
                covered[reg].add(str(row["buildingid"]))
    if complete_batches == len(batches):
        status = "complete"
    else:
        status = "incomplete" if complete_batches else "unavailable"
    return covered, status, provs


def associations(attribution_of, coverage, ids) -> dict[str, tuple]:
    """registration id -> (association, shared or None, covered building ids or None)."""
    out: dict[str, tuple[ContactAssociation, bool | None, list[str] | None]] = {}
    for reg in ids:
        attribution, on_lot = attribution_of.get(reg, (Attribution.UNATTRIBUTED_ON_LOT, []))
        covered = sorted(coverage[reg] | set(on_lot), key=str) if reg in coverage else None
        if len(on_lot) > 1 or (covered is not None and len(covered) > 1):
            shared: bool | None = True
        else:
            shared = False if covered is not None else None
        if attribution is Attribution.THIS_BUILDING:
            assoc = {
                True: ContactAssociation.SHARED_INCLUDING_REQUESTED_BUILDING,
                False: ContactAssociation.REQUESTED_BUILDING_ONLY,
                None: ContactAssociation.REQUESTED_BUILDING_SHARING_UNKNOWN,
            }[shared]
        elif attribution is Attribution.OTHER_BUILDING_ON_LOT:
            assoc = ContactAssociation.OTHER_BUILDINGS_ONLY
        elif attribution is Attribution.LOT_LEVEL:
            assoc = ContactAssociation.LOT_LEVEL
        else:
            assoc = ContactAssociation.UNATTRIBUTED
        out[reg] = (assoc, shared, covered)
    return out


def summarize(
    records, scope, prov, freshness, ids, checked, attribution_of, association_of, coverage_status
) -> ContactSummary:
    by_reg: list[RegistrationContacts] = []
    withheld: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    for r in records:
        withheld.update(r.withheld_fields)
        roles[r.role.value] += 1
    for reg in ids:
        mine = [r for r in records if r.registration_id == reg]
        attribution, building_ids = attribution_of.get(reg, (Attribution.UNATTRIBUTED_ON_LOT, []))
        assoc, shared, covered = association_of[reg]
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
                association=assoc,
                association_note=ASSOCIATION_NOTE[assoc],
                shared_registration=shared,
                building_ids_on_lot=building_ids,
                covered_building_ids=covered,
                covered_buildings_off_lot=(
                    len(set(covered) - set(building_ids)) if covered is not None else None
                ),
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
        source_rows=sum(len(r.source_rows) for r in records),
        identical_rows_collapsed=sum(len(r.source_rows) - 1 for r in records),
        coverage_status=coverage_status,
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
