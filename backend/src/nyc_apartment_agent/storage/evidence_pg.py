"""Private raw evidence in PostgreSQL (Task 4, step 2). Write side and audit side are apart.

Write side, `write_raw_rows(conn, items, ...)`: runs on the CALLER's connection and
transaction and never commits. Step 3's `save_result` calls it in the same transaction
as the public records, so a failure anywhere rolls the evidence back too. Intended for
a login holding `ingest_writer`, which may INSERT raw rows but never read, update or
delete them. It learns what already exists only from the public availability view
(digest and held/expired; no content):

    absent digest   -> INSERT ... ON CONFLICT DO NOTHING (no conflict target: naming
                       one needs SELECT on that column, which the writer does not have;
                       the primary key is the table's only unique constraint)
    held digest     -> nothing to do (content-addressed: same digest, same row)
    expired digest  -> if the copy was fetched AFTER the row expired:
                       evidence.restore_expired_raw_row(...), which restores the
                       original only if the tombstone's dataset, source id, basis and
                       redacted hash all match, and raises otherwise. Needs a login that
                       also holds evidence_restorer (migration 0003).
                       If the copy was fetched at or before the expiry time, it is a
                       repeat sighting from while the row was still held: nothing is
                       written and it is counted as `not_restored` (migration 0010
                       refuses such a restore; repeat sightings never extend retention).

Every item is checked first: its keyed digest and plain hash must match its row under
the configured key. Writes refuse to run without a configured EVIDENCE_DIGEST_KEY,
because digests made with a per-process key could never be matched again.

Digest integrity is enforced HERE, not by the database. PostgreSQL has no key, so
neither a plain INSERT nor the restore function can tell whether a row matches its
digest. Logins holding ingest_writer or evidence_restorer are trusted to write only
verified rows; the reader detects any that are not.

Retention: retain_until = fetched_at + retention_days (at most 180). An item whose
retention date has already passed is refused before anything is written; the database
enforces the same rule and the 180-day cap.

Audit side, `PostgresEvidenceReader`: for a login holding `evidence_admin`.

Expiry, `expire_due(conn, now)`: for a login holding `evidence_retention`. Turns due
rows into tombstones; it can update only the expiry columns and never reads row content.

JSONB is not the HTTP response: it normalizes key order and whitespace. Digests were
never over response bytes; they are over `canonical_json` of the parsed row (sorted
keys, compact, UTF-8), and Socrata values are strings, which JSONB keeps exactly. The
reader re-checks the digest of every row it returns.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.types import Text

from ..config import Settings, get_settings
from ..records.evidence import verify
from ..records.models import DigestBasis, RawEvidence


class EvidenceIntegrityError(ValueError):
    """An item's digest or hash does not match its row under the configured key."""


class DigestKeyNotConfiguredError(RuntimeError):
    pass


class EvidenceRetentionError(ValueError):
    """An item is already past its retention date, or the retention period is invalid."""


MAX_RETENTION_DAYS = 180  # D15; also enforced by evidence.raw_row's check constraint


class EvidenceWriteError(RuntimeError):
    """A database error during an evidence write, with row content removed.

    PostgreSQL's error DETAIL can quote the failing row ("Failing row contains ..."),
    so the original exception is not kept (raised outside the handler, so neither
    __cause__ nor __context__ refers to it) and only the error
    class and SQLSTATE are kept. The caller's transaction must still be rolled back.
    """


def _scrubbed(exc: DBAPIError) -> EvidenceWriteError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return EvidenceWriteError(f"evidence write failed: {type(exc.orig).__name__} ({sqlstate})")


@dataclass(frozen=True)
class EvidenceWriteReport:
    inserted: int  # absent before this write (a concurrent identical insert counts too)
    already_held: int
    restored: int  # expired tombstones given their original back
    # Expired digests whose copy was fetched before they expired: left as tombstones.
    not_restored: int = 0


def require_digest_key(settings: Settings | None = None) -> bytes:
    secret = (settings or get_settings()).evidence_digest_key
    if secret is None:
        raise DigestKeyNotConfiguredError(
            "EVIDENCE_DIGEST_KEY must be configured before evidence is stored"
        )
    return secret.get_secret_value().encode("utf-8")


def _fetched_at(item: RawEvidence) -> dt.datetime:
    when = dt.datetime.fromisoformat(item.fetched_at)
    if when.tzinfo is None:
        raise EvidenceIntegrityError("fetched_at has no time zone")
    return when


_INSERT = text(
    """
    INSERT INTO evidence.raw_row (
        original_digest, dataset_id, source_record_id, basis, original_row,
        original_sha256, redacted_sha256, redacted_fields, query_url, fetched_at,
        data_as_of, retain_until
    ) VALUES (
        :original_digest, :dataset_id, :source_record_id, :basis, :original_row,
        :original_sha256, :redacted_sha256, :redacted_fields, :query_url, :fetched_at,
        :data_as_of, :retain_until
    )
    ON CONFLICT DO NOTHING
    """
).bindparams(
    bindparam("original_row", type_=JSONB), bindparam("redacted_fields", type_=ARRAY(Text))
)

_RESTORE = text(
    """
    SELECT evidence.restore_expired_raw_row(
        :original_digest, :dataset_id, :source_record_id, :basis, :redacted_sha256,
        :original_sha256, :original_row, :fetched_at, :query_url, :data_as_of,
        :retain_until
    )
    """
).bindparams(bindparam("original_row", type_=JSONB))

_AVAILABILITY = text(
    "SELECT original_digest, held, expired_at FROM records.evidence_availability "
    "WHERE original_digest = ANY(:digests)"
).bindparams(bindparam("digests", type_=ARRAY(Text)))


def _params(item: RawEvidence, retention_days: int) -> dict:
    fetched = _fetched_at(item)
    return {
        "original_digest": item.original_digest,
        "dataset_id": item.dataset_id,
        "source_record_id": item.source_record_id,
        "basis": item.basis.value,
        "original_row": item.original_row,
        "original_sha256": item.original_sha256,
        "redacted_sha256": item.redacted_sha256,
        "redacted_fields": list(item.redacted_fields),
        "query_url": item.query_url,
        "fetched_at": fetched,
        "data_as_of": item.data_as_of,
        "retain_until": fetched + dt.timedelta(days=retention_days),
    }


def write_raw_rows(
    conn: Connection,
    items: Iterable[RawEvidence],
    *,
    digest_key: bytes,
    retention_days: int,
    now: dt.datetime | None = None,
) -> EvidenceWriteReport:
    """Write buffered evidence on the caller's transaction. Never commits."""
    if not 1 <= retention_days <= MAX_RETENTION_DAYS:
        raise EvidenceRetentionError(f"retention must be 1 to {MAX_RETENTION_DAYS} days")
    now = now or dt.datetime.now(dt.UTC)
    batch = sorted(items, key=lambda i: i.original_digest)  # stable lock order
    for item in batch:
        if not verify(item, digest_key):
            raise EvidenceIntegrityError("digest or hash does not match the row")
    params = {i.original_digest: _params(i, retention_days) for i in batch}
    for p in params.values():
        if p["retain_until"] <= now:
            raise EvidenceRetentionError(
                f"evidence fetched at {p['fetched_at'].isoformat()} is past its "
                f"{retention_days}-day retention date"
            )
    if not batch:
        return EvidenceWriteReport(0, 0, 0)
    digests = [i.original_digest for i in batch]
    try:
        rows = conn.execute(_AVAILABILITY, {"digests": digests}).all()
        state = {r.original_digest: r for r in rows}
        absent = [i for i in batch if i.original_digest not in state]
        expired = [
            i for i in batch if i.original_digest in state and not state[i.original_digest].held
        ]
        restorable = [
            i
            for i in expired
            if params[i.original_digest]["fetched_at"] > state[i.original_digest].expired_at
        ]
        if absent:
            conn.execute(_INSERT, [params[i.original_digest] for i in absent])
        for item in restorable:
            conn.execute(_RESTORE, params[item.original_digest])
    except DBAPIError as exc:
        error = _scrubbed(exc)
    else:
        error = None
    if error is not None:  # raised outside the handler: no chained DB error
        raise error
    held = sum(1 for i in batch if i.original_digest in state and state[i.original_digest].held)
    return EvidenceWriteReport(
        inserted=len(absent),
        already_held=held,
        restored=len(restorable),
        not_restored=len(expired) - len(restorable),
    )


def save_evidence(
    engine: Engine, items: Iterable[RawEvidence], settings: Settings
) -> EvidenceWriteReport:
    """Evidence on its own, in one transaction (step 3 adds the public records to it)."""
    key = require_digest_key(settings)
    with engine.begin() as conn:
        return write_raw_rows(
            conn, items, digest_key=key, retention_days=settings.evidence_retention_days
        )


_SELECT_HELD = text(
    """
    SELECT original_digest, dataset_id, source_record_id, basis, original_row,
           original_sha256, redacted_sha256, redacted_fields, query_url, fetched_at,
           data_as_of
    FROM evidence.raw_row WHERE original_digest = :d AND expired_at IS NULL
    """
)


class PostgresEvidenceReader:
    """Audit/retrieval of held raw rows. Use with an `evidence_admin` login only."""

    def __init__(self, engine: Engine, digest_key: bytes) -> None:
        self.engine = engine
        self.digest_key = digest_key

    def get(self, original_digest: str) -> RawEvidence | None:
        with self.engine.connect() as conn:
            row = conn.execute(_SELECT_HELD, {"d": original_digest}).mappings().first()
        if row is None:
            return None  # never stored, or expired (see records.evidence_availability)
        item = RawEvidence(
            dataset_id=row["dataset_id"],
            source_record_id=row["source_record_id"],
            original_sha256=row["original_sha256"],
            original_digest=row["original_digest"],
            redacted_sha256=row["redacted_sha256"],
            redacted_fields=list(row["redacted_fields"]),
            basis=DigestBasis(row["basis"]),
            original_row=row["original_row"],
            query_url=row["query_url"],
            fetched_at=row["fetched_at"].isoformat(),
            data_as_of=row["data_as_of"],
        )
        if not verify(item, self.digest_key):
            raise EvidenceIntegrityError("stored row no longer matches its digest")
        return item


_EXPIRE = text(
    """
    UPDATE evidence.raw_row
       SET original_row = NULL, original_sha256 = NULL, expired_at = :now
     WHERE expired_at IS NULL AND retain_until <= :now
    """
)


def expire_due(conn: Connection, now: dt.datetime) -> int:
    """Turn every raw row past its retention date into a tombstone. Caller commits."""
    return conn.execute(_EXPIRE, {"now": now}).rowcount
