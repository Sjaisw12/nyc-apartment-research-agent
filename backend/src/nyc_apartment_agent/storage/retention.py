"""The 180-day raw-evidence retention job (Task 4, step 6).

`run_retention(engine)` turns every held raw row whose retention date has passed into a
tombstone: original_row and original_sha256 become NULL and expired_at is set. The
keyed digest, dataset and source ids, basis, redacted hash, query URL, fetch time and
restore history stay, so public records keep their lineage and report the evidence as
not held. Nothing else in the database is touched.

Run it with a login that holds ONLY `evidence_retention`. That role can read the
digest, retain_until and expired_at columns and update the three expiry columns; it can
never read original_row. The job checks this before it changes anything and refuses a
login that could read raw content (evidence_admin, nyc_owner, a superuser) or that does
not hold evidence_retention.

Bounded and repeatable:
- One cutoff for the whole run: the database clock, or an injected `now` for tests. An
  injected clock ahead of the database is refused (and migration 0010 refuses expiring
  any row the database clock does not consider due).
- At most `max_batches` transactions of at most `batch_size` rows each. Each batch
  commits on its own, so a failure keeps earlier batches and rolls back only its own.
- Rows are taken with FOR UPDATE SKIP LOCKED, so concurrent runs never expire the same
  row twice or wait on each other; a run that skipped locked rows reports them as
  remaining. Every UPDATE also re-checks expired_at IS NULL.
- Idempotent: a second run with the same cutoff expires nothing.

The result is a `RetentionReport` with counts only. Database errors are reduced to the
error class and SQLSTATE (PostgreSQL's DETAIL can quote a failing row). The job never
selects, returns or logs row content or digests.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from .db import EVIDENCE_RETENTION

log = logging.getLogger("nyc_apartment_agent.retention")

MAX_BATCH_SIZE = 10_000
MAX_BATCHES = 10_000

COMPLETE = "complete"  # every row due at the cutoff is now a tombstone
INCOMPLETE = "incomplete"  # stopped at max_batches or skipped locked rows: run again
FAILED = "failed"  # refused or a database error; committed batches are kept


@dataclass(frozen=True)
class RetentionReport:
    status: str
    cutoff: dt.datetime | None
    expired: int  # rows turned into tombstones by committed batches
    batches: int  # committed batches
    remaining_due: int | None  # held rows still due at the cutoff (None after a failure)
    error: str | None = None  # sanitized: a reason, or "ErrorClass (SQLSTATE)"

    def as_log(self) -> dict:
        out = asdict(self)
        out["cutoff"] = self.cutoff.isoformat() if self.cutoff else None
        return out


_PREFLIGHT = text(
    """
    SELECT now() AS db_now,
           pg_has_role(:role, 'MEMBER') AS holds_retention,
           has_column_privilege('evidence.raw_row', 'original_row', 'SELECT') AS reads_content
    """
)

_LIMITS = (
    "SELECT set_config('lock_timeout', :lock, true), set_config('statement_timeout', :stmt, true)"
)

_EXPIRE_BATCH = text(
    """
    WITH due AS (
        SELECT original_digest FROM evidence.raw_row
         WHERE expired_at IS NULL AND retain_until <= :cutoff
         ORDER BY retain_until, original_digest
         LIMIT :limit
         FOR UPDATE SKIP LOCKED
    )
    UPDATE evidence.raw_row r
       SET original_row = NULL, original_sha256 = NULL, expired_at = :cutoff
      FROM due
     WHERE r.original_digest = due.original_digest AND r.expired_at IS NULL
    """
)

_COUNT_DUE = text(
    "SELECT count(*) FROM evidence.raw_row WHERE expired_at IS NULL AND retain_until <= :cutoff"
)


def sanitized_error(exc: DBAPIError) -> str:
    return f"{type(exc.orig).__name__} ({getattr(exc.orig, 'sqlstate', None)})"


def _finish(report: RetentionReport) -> RetentionReport:
    if report.status == FAILED:
        log.warning("raw evidence retention failed", extra={"retention": report.as_log()})
    else:
        log.info("raw evidence retention finished", extra={"retention": report.as_log()})
    return report


def count_due(engine: Engine, now: dt.datetime | None = None) -> int:
    """Held rows due at `now` (default: the database clock). Reads no content."""
    with engine.connect() as conn:
        cutoff = now or conn.execute(text("SELECT now()")).scalar_one()
        return conn.execute(_COUNT_DUE, {"cutoff": cutoff}).scalar_one()


def run_retention(
    engine: Engine,
    *,
    now: dt.datetime | None = None,
    batch_size: int = 500,
    max_batches: int = 100,
    lock_timeout_ms: int = 5_000,
    statement_timeout_ms: int = 60_000,
) -> RetentionReport:
    """Expire due raw evidence in bounded batches. Never raises for database errors."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be 1 to {MAX_BATCH_SIZE}")
    if not 1 <= max_batches <= MAX_BATCHES:
        raise ValueError(f"max_batches must be 1 to {MAX_BATCHES}")
    if now is not None and now.tzinfo is None:
        raise ValueError("now must have a time zone")
    limits = {"lock": f"{lock_timeout_ms}ms", "stmt": f"{statement_timeout_ms}ms"}

    expired = batches = 0
    cutoff: dt.datetime | None = None
    try:
        with engine.connect() as conn:
            pre = conn.execute(_PREFLIGHT, {"role": EVIDENCE_RETENTION}).one()
        if pre.reads_content:
            return _finish(
                RetentionReport(
                    FAILED, None, 0, 0, None, "login can read raw evidence content; refused"
                )
            )
        if not pre.holds_retention:
            return _finish(
                RetentionReport(FAILED, None, 0, 0, None, "login lacks evidence_retention")
            )
        if now is not None and now > pre.db_now:
            return _finish(
                RetentionReport(FAILED, None, 0, 0, None, "injected clock is ahead of the database")
            )
        cutoff = now or pre.db_now

        while batches < max_batches:
            with engine.begin() as conn:
                conn.execute(text(_LIMITS), limits)
                n = conn.execute(_EXPIRE_BATCH, {"cutoff": cutoff, "limit": batch_size}).rowcount
            batches += 1
            expired += n
            if n < batch_size:
                break

        with engine.connect() as conn:
            remaining = conn.execute(_COUNT_DUE, {"cutoff": cutoff}).scalar_one()
    except DBAPIError as exc:
        return _finish(
            RetentionReport(FAILED, cutoff, expired, batches, None, sanitized_error(exc))
        )
    status = COMPLETE if remaining == 0 else INCOMPLETE
    return _finish(RetentionReport(status, cutoff, expired, batches, remaining))
