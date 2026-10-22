"""Persist and load source-result snapshots (Task 4: HPD violations, step 3; complaints, step 4).

One save is one transaction on the caller's connection, as a login holding ingest_writer
(plus evidence_restorer, only needed when an expired raw row comes back):

    1. skip if this exact result was already saved for this run (idempotency key);
    2. write the raw rows the records cite (storage/evidence_pg.write_raw_rows);
    3. insert the fetch, its pages, (complaints) its complaint groups, its records and
       their source-row links;
    4. the caller commits. At commit a deferred trigger checks the snapshot is complete.

Any failure rolls back all of it, evidence included. Nothing is ever updated: a new
retrieval is a new snapshot with its own provenance (append-only, D14).

Privacy (D16): the address the user typed is never stored. retrieval_run keeps an HMAC
of its normalized form and a whitelisted projection of the resolver result (identifiers,
outcome, level, BIN evidence, reason codes). Public payloads are the already-redacted
public models; the database refuses private keys in any JSONB value. Database errors
are re-raised without the original exception (its DETAIL can quote row values) and SQL
parameters are hidden from logs by make_engine.

Loading rebuilds the exact Pydantic models and validates them again, so a stored row
that breaks a model rule fails loudly. The evidence state of every cited raw row
(held or expired, and its basis) is returned beside the result.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.types import Text

from ..config import Settings
from ..identifiers import normalize_bbl
from ..records.complaint_models import (
    ComplaintGroup,
    ComplaintSourceResult,
    NormalizedComplaintProblem,
)
from ..records.join_policy import UnsupportedJoinError, plan_lot_query
from ..records.models import (
    DigestBasis,
    NormalizedViolation,
    PageRecord,
    RawEvidence,
    SourceResult,
)
from ..records.redaction import canonical_json, canonical_sha256
from ..resolver.models import Outcome, ResolutionResult
from .evidence_pg import EvidenceWriteReport, require_digest_key, write_raw_rows

SCHEMA_VERSION = 1
RESULT_TYPE_VIOLATIONS = "hpd_violations"
RESULT_TYPE_COMPLAINTS = "hpd_complaints"


class RecordWriteError(RuntimeError):
    """A save failed. Row content is never included; the transaction must roll back."""


class StoredResultNotFoundError(LookupError):
    pass


def _scrubbed(exc: DBAPIError) -> RecordWriteError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return RecordWriteError(f"record write failed: {type(exc.orig).__name__} ({sqlstate})")


# ---- runs ------------------------------------------------------------------------------


def input_digest(raw_input: str, key: bytes) -> str:
    """HMAC of the normalized address input. The input itself is never stored (D16)."""
    normalized = " ".join(raw_input.upper().split())
    return hmac.new(key, b"address-input:v1\x00" + normalized.encode(), hashlib.sha256).hexdigest()


def resolution_projection(resolution: ResolutionResult) -> dict:
    """The whitelisted part of a resolver result that may be stored.

    Excluded on purpose: the parsed and raw input, the explanation text, address labels
    and aliases, candidate details and request provenance (whose URLs carry the input).
    """
    projection: dict = {
        "outcome": resolution.outcome.value,
        "resolution_level": resolution.resolution_level.value
        if resolution.resolution_level
        else None,
        "reasons": [r.value for r in resolution.reasons],
        "building": None,
        "options": [
            {"bbl": o.bbl, "bin": o.bin, "bin_evidence": o.bin_evidence.value}
            for o in resolution.options
        ],
        "candidate_bbls": sorted({c.bbl for c in resolution.candidates}),
        "candidate_bins": sorted({c.pad_bin for c in resolution.candidates if c.pad_bin}),
    }
    if resolution.building is not None:
        b = resolution.building
        projection["building"] = {
            "bbl": b.bbl,
            "bin": b.bin,
            "bin_evidence": b.bin_evidence.value,
            "borough": b.borough,
            "borough_source": b.borough_source,
        }
    return projection


_INSERT_RUN = text(
    """
    INSERT INTO records.retrieval_run (
        id, input_digest, resolution_outcome, resolution_level, resolved_bbl,
        resolved_bin, resolution
    ) VALUES (
        :id, :input_digest, :outcome, :level, :bbl, :bin, :resolution
    )
    """
).bindparams(bindparam("resolution", type_=JSONB))


def save_run(
    conn: Connection, resolution: ResolutionResult, raw_input: str, *, digest_key: bytes
) -> uuid.UUID:
    """Record one research request. Caller commits (or saves results in the same txn)."""
    run_id = uuid.uuid4()
    bbl = bin_ = None
    if resolution.outcome is Outcome.VERIFIED and resolution.building is not None:
        bbl = normalize_bbl(resolution.building.bbl)
        try:
            bin_ = plan_lot_query(resolution).building_bin
        except UnsupportedJoinError:
            bin_ = None
    params = {
        "id": run_id,
        "input_digest": input_digest(raw_input, digest_key),
        "outcome": resolution.outcome.value,
        "level": resolution.resolution_level.value if resolution.resolution_level else None,
        "bbl": bbl,
        "bin": bin_,
        "resolution": resolution_projection(resolution),
    }
    try:
        conn.execute(_INSERT_RUN, params)
    except DBAPIError as exc:
        raise _scrubbed(exc) from None
    return run_id


# ---- source results (shared by violations and complaints) -----------------------------

AnyResult = SourceResult | ComplaintSourceResult
AnyRecord = NormalizedViolation | NormalizedComplaintProblem


@dataclass(frozen=True)
class _Kind:
    result_type: str
    record_kind: str
    model: type
    source_record_id: Callable[[AnyRecord], str]
    complaint_id: Callable[[AnyRecord], str | None]


VIOLATIONS = _Kind(
    RESULT_TYPE_VIOLATIONS, "violation", SourceResult, lambda r: r.violation_id, lambda r: None
)
COMPLAINTS = _Kind(
    RESULT_TYPE_COMPLAINTS,
    "complaint_problem",
    ComplaintSourceResult,
    lambda r: r.problem_id,
    lambda r: r.complaint_id,
)


def _kind_of(obj) -> _Kind:
    if isinstance(obj, ComplaintSourceResult | NormalizedComplaintProblem):
        return COMPLAINTS
    if isinstance(obj, SourceResult | NormalizedViolation):
        return VIOLATIONS
    raise TypeError(f"unsupported result type: {type(obj).__name__}")


@dataclass(frozen=True)
class SavedResult:
    fetch_id: uuid.UUID
    created: bool  # False: this exact result was already saved for this run
    evidence: EvidenceWriteReport | None


def result_payload(result: AnyResult) -> dict:
    """The public result without records, complaint groups and pages (own tables)."""
    exclude: dict = {"records": True, "provenance": {"pages"}}
    if isinstance(result, ComplaintSourceResult):
        exclude["complaints"] = True
    return result.model_dump(mode="json", by_alias=True, exclude=exclude)


def record_payload(record: AnyRecord) -> dict:
    return record.model_dump(mode="json", by_alias=True)


def content_sha256(result: AnyResult) -> str:
    return canonical_sha256(result.model_dump(mode="json", by_alias=True))


def idempotency_key(run_id: uuid.UUID, result_type: str, content: str) -> str:
    """Same run + same type + same content: the same snapshot. Anything else is new."""
    return hashlib.sha256(
        canonical_json({"run": str(run_id), "type": result_type, "content": content})
    ).hexdigest()


_FIND_FETCH = text("SELECT id FROM records.source_fetch WHERE idempotency_key = :k")

_AVAILABLE = text(
    "SELECT original_digest FROM records.evidence_availability "
    "WHERE original_digest = ANY(:digests)"
).bindparams(bindparam("digests", type_=ARRAY(Text)))

_INSERT_FETCH = text(
    """
    INSERT INTO records.source_fetch (
        id, run_id, idempotency_key, content_sha256, result_type, source, dataset_id,
        status, status_detail, partial, join_scope, resolved_bbl, resolved_bin,
        source_count, rows_fetched, record_count, source_row_count, page_count,
        group_count, incomplete_reasons, warnings, data_as_of, where_clause, order_by,
        page_size, row_cap, building_id_applied, result_payload, schema_version
    ) VALUES (
        :id, :run_id, :idempotency_key, :content_sha256, :result_type, :source, :dataset_id,
        :status, :status_detail, :partial, :join_scope, :resolved_bbl, :resolved_bin,
        :source_count, :rows_fetched, :record_count, :source_row_count, :page_count,
        :group_count, :incomplete_reasons, :warnings, :data_as_of, :where_clause, :order_by,
        :page_size, :row_cap, :building_id_applied, :result_payload, :schema_version
    )
    """
).bindparams(
    bindparam("result_payload", type_=JSONB),
    bindparam("incomplete_reasons", type_=ARRAY(Text)),
    bindparam("warnings", type_=ARRAY(Text)),
)

_INSERT_PAGE = text(
    """
    INSERT INTO records.fetch_page (
        fetch_id, seq, batch, page_index, url, rows, fetched_at, attempts,
        truth_last_modified, last_modified, request_id
    ) VALUES (
        :fetch_id, :seq, NULL, :page_index, :url, :rows, :fetched_at, :attempts,
        :truth_last_modified, :last_modified, :request_id
    )
    """
)

_INSERT_GROUP = text(
    """
    INSERT INTO records.complaint_group (
        id, fetch_id, ordinal, complaint_id, attribution, problem_attributions,
        problem_count, duplicate_problem_count, payload, schema_version
    ) VALUES (
        :id, :fetch_id, :ordinal, :complaint_id, :attribution, :problem_attributions,
        :problem_count, :duplicate_problem_count, :payload, :schema_version
    )
    """
).bindparams(bindparam("payload", type_=JSONB), bindparam("problem_attributions", type_=JSONB))

_INSERT_RECORD = text(
    """
    INSERT INTO records.normalized_record (
        id, fetch_id, ordinal, record_kind, evidence_kind, dataset_id, source_record_id,
        complaint_id, resolved_bbl, lot_membership, attribution, attribution_basis,
        original_attribution, mapped_bin, location_scope, quality_flags, digest_basis,
        original_digest, redacted_sha256, payload, schema_version
    ) VALUES (
        :id, :fetch_id, :ordinal, :record_kind, :evidence_kind, :dataset_id,
        :source_record_id, :complaint_id, :resolved_bbl, :lot_membership, :attribution,
        :attribution_basis, :original_attribution, :mapped_bin, :location_scope,
        :quality_flags, :digest_basis, :original_digest, :redacted_sha256, :payload,
        :schema_version
    )
    """
).bindparams(bindparam("payload", type_=JSONB), bindparam("quality_flags", type_=ARRAY(Text)))

_INSERT_LINK = text(
    """
    INSERT INTO records.record_source_row (
        record_id, fetch_id, position, source_record_id, source_row_id, original_digest
    ) VALUES (
        :record_id, :fetch_id, :position, :source_record_id, :source_row_id, :original_digest
    )
    """
)


def _links(fetch_id, record_id, record: AnyRecord, source_record_id: str) -> list[dict]:
    """One link per source row the record stands for.

    Registrations and contacts list every source row in `source_rows` (itself first, then
    identical copies collapsed into it), each with its own Socrata :id and raw row. Every
    other record stands for exactly one source row: its own.
    """
    refs = getattr(record, "source_rows", None)
    if refs is None:
        return [
            {
                "record_id": record_id,
                "fetch_id": fetch_id,
                "position": 0,
                "source_record_id": source_record_id,
                "source_row_id": None,
                "original_digest": record.original_digest,
            }
        ]
    return [
        {
            "record_id": record_id,
            "fetch_id": fetch_id,
            "position": i,
            "source_record_id": ref.source_row_id,
            "source_row_id": ref.source_row_id,
            "original_digest": ref.original_digest,
        }
        for i, ref in enumerate(refs)
    ]


def source_row_links(result: AnyResult) -> int:
    """Stored links: source rows behind the records, not the number of records."""
    return sum(len(getattr(r, "source_rows", None) or [r]) for r in result.records)


def _fetch_params(fetch_id, run_id, key, content, result: AnyResult) -> dict:
    kind = _kind_of(result)
    prov = result.provenance
    d9 = result.building_id_attribution
    groups = result.complaints if isinstance(result, ComplaintSourceResult) else []
    return {
        "id": fetch_id,
        "run_id": run_id,
        "idempotency_key": key,
        "content_sha256": content,
        "result_type": kind.result_type,
        "source": result.source,
        "dataset_id": result.dataset_id,
        "status": result.status.value,
        "status_detail": result.status_detail,
        "partial": result.partial,
        "join_scope": result.join_scope.value if result.join_scope else None,
        "resolved_bbl": result.resolved_bbl,
        "resolved_bin": result.resolved_bin,
        "source_count": prov.source_count,
        "rows_fetched": prov.rows_fetched,
        "record_count": len(result.records),
        "source_row_count": source_row_links(result),
        "page_count": len(prov.pages),
        "group_count": len(groups),
        "incomplete_reasons": list(prov.incomplete_reasons),
        "warnings": list(prov.warnings),
        "data_as_of": prov.data_as_of,
        "where_clause": prov.where,
        "order_by": prov.order_by,
        "page_size": prov.page_size,
        "row_cap": prov.row_cap,
        "building_id_applied": bool(d9 and d9.applied),
        "result_payload": result_payload(result),
        "schema_version": SCHEMA_VERSION,
    }


def _record_params(fetch_id, ordinal: int, r: AnyRecord) -> dict:
    kind = _kind_of(r)
    return {
        "id": uuid.uuid4(),
        "fetch_id": fetch_id,
        "ordinal": ordinal,
        "record_kind": kind.record_kind,
        "evidence_kind": r.evidence_kind.value,
        "dataset_id": r.dataset_id,
        "source_record_id": kind.source_record_id(r),
        "complaint_id": kind.complaint_id(r),
        "resolved_bbl": r.resolved_bbl,
        "lot_membership": r.lot_membership.value,
        "attribution": r.attribution.value,
        "attribution_basis": r.attribution_basis.value,
        "original_attribution": r.original_attribution.value if r.original_attribution else None,
        "mapped_bin": r.mapped_bin,
        "location_scope": r.location_scope.value,
        "quality_flags": [f.value for f in r.quality_flags],
        "digest_basis": r.digest_basis.value,
        "original_digest": r.original_digest,
        "redacted_sha256": r.redacted_sha256,
        "payload": record_payload(r),
        "schema_version": SCHEMA_VERSION,
    }


def _group_params(fetch_id, ordinal: int, g: ComplaintGroup) -> dict:
    return {
        "id": uuid.uuid4(),
        "fetch_id": fetch_id,
        "ordinal": ordinal,
        "complaint_id": g.complaint_id,
        "attribution": g.attribution.value,
        "problem_attributions": g.problem_attributions,
        "problem_count": g.problem_count,
        "duplicate_problem_count": g.duplicate_problem_count,
        "payload": g.model_dump(mode="json"),
        "schema_version": SCHEMA_VERSION,
    }


def save_result(
    conn: Connection,
    run_id: uuid.UUID,
    result: AnyResult,
    evidence: Iterable[RawEvidence],
    *,
    digest_key: bytes,
    retention_days: int,
    now: dt.datetime | None = None,
) -> SavedResult:
    """Save one source result and the raw rows it cites. Never commits."""
    kind = _kind_of(result)
    # Validate again: a model changed with model_copy() skips its validators.
    result = kind.model.model_validate(result.model_dump(by_alias=True))
    content = content_sha256(result)
    key = idempotency_key(run_id, kind.result_type, content)
    cited = {
        link["original_digest"]
        for r in result.records
        for link in _links(None, None, r, kind.source_record_id(r))
    }
    items = [i for i in evidence if i.original_digest in cited]  # only what records cite
    try:
        existing = conn.execute(_FIND_FETCH, {"k": key}).scalar()
        if existing is not None:
            return SavedResult(fetch_id=existing, created=False, evidence=None)
        missing = sorted(cited - {i.original_digest for i in items})
        if missing:
            stored = set(conn.execute(_AVAILABLE, {"digests": missing}).scalars())
            if set(missing) - stored:
                raise RecordWriteError(
                    f"{len(set(missing) - stored)} records cite raw rows that are neither "
                    "supplied nor stored"
                )
    except DBAPIError as exc:
        raise _scrubbed(exc) from None

    report = write_raw_rows(
        conn, items, digest_key=digest_key, retention_days=retention_days, now=now
    )
    fetch_id = uuid.uuid4()
    pages = [
        {
            "fetch_id": fetch_id,
            "seq": seq,
            "page_index": p.index,
            "url": p.url,
            "rows": p.rows,
            "fetched_at": p.fetched_at,
            "attempts": p.attempts,
            "truth_last_modified": p.truth_last_modified,
            "last_modified": p.last_modified,
            "request_id": p.request_id,
        }
        for seq, p in enumerate(result.provenance.pages)
    ]
    groups = (
        [_group_params(fetch_id, i, g) for i, g in enumerate(result.complaints)]
        if isinstance(result, ComplaintSourceResult)
        else []
    )
    records = [_record_params(fetch_id, i, r) for i, r in enumerate(result.records)]
    links = [
        link
        for rec, r in zip(records, result.records, strict=True)
        for link in _links(fetch_id, rec["id"], r, rec["source_record_id"])
    ]
    try:
        conn.execute(_INSERT_FETCH, _fetch_params(fetch_id, run_id, key, content, result))
        if pages:
            conn.execute(_INSERT_PAGE, pages)
        if groups:  # before the problems, which reference their complaint
            conn.execute(_INSERT_GROUP, groups)
        if records:
            conn.execute(_INSERT_RECORD, records)
            conn.execute(_INSERT_LINK, links)
    except DBAPIError as exc:
        raise _scrubbed(exc) from None
    return SavedResult(fetch_id=fetch_id, created=True, evidence=report)


def save_violation_result(conn: Connection, run_id, result: SourceResult, evidence, **kw):
    return save_result(conn, run_id, result, evidence, **kw)


def save_complaint_result(conn: Connection, run_id, result: ComplaintSourceResult, evidence, **kw):
    return save_result(conn, run_id, result, evidence, **kw)


def commit_checked(conn: Connection) -> None:
    """Commit, scrubbing a failure of the deferred completeness checks."""
    try:
        conn.commit()
    except DBAPIError as exc:
        raise _scrubbed(exc) from None


def save_with_run(
    engine: Engine,
    resolution: ResolutionResult,
    raw_input: str,
    result: AnyResult,
    evidence: Iterable[RawEvidence],
    settings: Settings,
) -> tuple[uuid.UUID, SavedResult]:
    """A run and one source result in one transaction."""
    key = require_digest_key(settings)
    with engine.connect() as conn:
        try:
            run_id = save_run(conn, resolution, raw_input, digest_key=key)
            saved = save_result(
                conn,
                run_id,
                result,
                evidence,
                digest_key=key,
                retention_days=settings.evidence_retention_days,
            )
            commit_checked(conn)
        except BaseException:
            conn.rollback()
            raise
    return run_id, saved


save_violations = save_with_run
save_complaints = save_with_run


# ---- loading ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceState:
    basis: DigestBasis
    held: bool  # False: expired; the digest remains but the original is gone
    retain_until: dt.datetime
    expired_at: dt.datetime | None


@dataclass(frozen=True)
class StoredResult:
    fetch_id: uuid.UUID
    run_id: uuid.UUID
    result: AnyResult
    evidence: dict[str, EvidenceState]  # original_digest -> state of its raw row


StoredViolationResult = StoredResult

_LOAD_FETCH = text(
    "SELECT run_id, result_type, result_payload FROM records.source_fetch WHERE id = :id"
)
_LOAD_PAGES = text(
    """
    SELECT page_index, url, rows, fetched_at, attempts, truth_last_modified, last_modified,
           request_id
    FROM records.fetch_page WHERE fetch_id = :id ORDER BY seq
    """
)
_LOAD_RECORDS = text(
    "SELECT payload FROM records.normalized_record WHERE fetch_id = :id ORDER BY ordinal"
)
_LOAD_GROUPS = text(
    "SELECT payload FROM records.complaint_group WHERE fetch_id = :id ORDER BY ordinal"
)
_LOAD_EVIDENCE = text(
    """
    SELECT e.original_digest, e.basis, e.held, e.retain_until, e.expired_at
    FROM records.record_source_row l
    JOIN records.evidence_availability e ON e.original_digest = l.original_digest
    WHERE l.fetch_id = :id
    """
)


def _load(conn: Connection, fetch_id: uuid.UUID, kind: _Kind) -> StoredResult:
    """Rebuild a saved result and validate it again. Read-only."""
    row = conn.execute(_LOAD_FETCH, {"id": fetch_id}).first()
    if row is None:
        raise StoredResultNotFoundError(str(fetch_id))
    if row.result_type != kind.result_type:
        raise StoredResultNotFoundError(f"{fetch_id} is not a {kind.result_type} result")
    payload = dict(row.result_payload)
    pages = [
        PageRecord(
            index=p.page_index,
            url=p.url,
            rows=p.rows,
            fetched_at=p.fetched_at,
            attempts=p.attempts,
            truth_last_modified=p.truth_last_modified,
            last_modified=p.last_modified,
            request_id=p.request_id,
        ).model_dump()
        for p in conn.execute(_LOAD_PAGES, {"id": fetch_id})
    ]
    payload["provenance"] = {**payload["provenance"], "pages": pages}
    payload["records"] = list(conn.execute(_LOAD_RECORDS, {"id": fetch_id}).scalars())
    if kind is COMPLAINTS:
        payload["complaints"] = list(conn.execute(_LOAD_GROUPS, {"id": fetch_id}).scalars())
    result = kind.model.model_validate(payload)
    evidence = {
        e.original_digest: EvidenceState(
            basis=DigestBasis(e.basis),
            held=e.held,
            retain_until=e.retain_until,
            expired_at=e.expired_at,
        )
        for e in conn.execute(_LOAD_EVIDENCE, {"id": fetch_id})
    }
    for r in result.records:  # the database checks this too; fail loudly if not
        state = evidence.get(r.original_digest)
        if state is None or state.basis is not r.digest_basis:
            raise RecordWriteError("a record's raw row is missing or has another basis")
    return StoredResult(fetch_id=fetch_id, run_id=row.run_id, result=result, evidence=evidence)


def load_violation_result(conn: Connection, fetch_id: uuid.UUID) -> StoredResult:
    return _load(conn, fetch_id, VIOLATIONS)


def load_complaint_result(conn: Connection, fetch_id: uuid.UUID) -> StoredResult:
    return _load(conn, fetch_id, COMPLAINTS)


__all__ = [
    "EvidenceState",
    "RecordWriteError",
    "SavedResult",
    "StoredResult",
    "StoredResultNotFoundError",
    "StoredViolationResult",
    "commit_checked",
    "input_digest",
    "load_complaint_result",
    "load_violation_result",
    "resolution_projection",
    "save_complaint_result",
    "save_complaints",
    "save_result",
    "save_run",
    "save_violation_result",
    "save_violations",
    "save_with_run",
]
