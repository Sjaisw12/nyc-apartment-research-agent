"""Raw-evidence lifecycle transitions (Task 4, step 6).

evidence_retention holds UPDATE on original_row, original_sha256 and expired_at, because
expiry must null the first two and set the third. Column grants cannot say "only to
NULL" or "only when due", and step 6 reproduced, as a real evidence_retention login:

  - expiring a row that was not yet due (UPDATE ... SET original_row = NULL ... with any
    WHERE clause, or expire_due() with a clock ahead of the database);
  - overwriting held evidence with other JSON (original_row = '{"forged": ...}');
  - turning a tombstone back into "held" evidence with invented content, bypassing the
    restore function (expired_at = NULL, original_row = ..., original_sha256 = ...);
  - rewriting a tombstone's expired_at.

All four committed. This migration adds one BEFORE UPDATE trigger that allows exactly
two transitions, whoever runs the UPDATE:

  expiry   held -> tombstone. Only original_row, original_sha256 and expired_at change,
           the row is due by the DATABASE clock (retain_until <= now()), and expired_at
           lies between retain_until and now().
  restore  tombstone -> held. Only through evidence.restore_expired_raw_row: restore_count
           must go up by exactly one (evidence_retention cannot write that column), the
           identity columns stay the same, and the restored copy must have been fetched
           AFTER the row expired. A copy fetched while the row was still held is a repeat
           sighting, and repeat sightings never extend retention (design doc 10.2d).

Everything else is refused: held rows and tombstones are otherwise immutable. The
existing CHECKs and the 0003 retention-window trigger still apply on top.

No grant changes. The HMAC key stays out of the database; digest integrity is still
checked in Python before writes and on every authorized read.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Columns an expiry may change, and the further columns a restore may change.
EXPIRY_COLUMNS = "ARRAY['original_row', 'original_sha256', 'expired_at']"
RESTORE_COLUMNS = (
    "ARRAY['original_row', 'original_sha256', 'expired_at', 'fetched_at', 'query_url', "
    "'data_as_of', 'retain_until', 'restored_at', 'restore_count']"
)


def upgrade() -> None:
    with as_owner():
        op.execute(
            f"""
            CREATE FUNCTION evidence.raw_row_lifecycle() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
            AS $fn$
            BEGIN
                IF NEW IS NOT DISTINCT FROM OLD THEN
                    RETURN NEW;  -- a no-op update changes nothing
                END IF;
                IF OLD.expired_at IS NULL AND NEW.expired_at IS NOT NULL THEN
                    -- expiry
                    IF to_jsonb(NEW) - {EXPIRY_COLUMNS}
                       IS DISTINCT FROM to_jsonb(OLD) - {EXPIRY_COLUMNS} THEN
                        RAISE EXCEPTION 'expiry may only remove the original row'
                            USING ERRCODE = '23514';
                    END IF;
                    IF OLD.retain_until > now() THEN
                        RAISE EXCEPTION 'raw evidence is not yet due for expiry'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.expired_at < OLD.retain_until OR NEW.expired_at > now() THEN
                        RAISE EXCEPTION 'expiry time must lie between the retention date and now'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END IF;
                IF OLD.expired_at IS NOT NULL AND NEW.expired_at IS NULL THEN
                    -- restore
                    IF NEW.restore_count <> OLD.restore_count + 1 OR NEW.restored_at IS NULL
                       OR to_jsonb(NEW) - {RESTORE_COLUMNS}
                          IS DISTINCT FROM to_jsonb(OLD) - {RESTORE_COLUMNS} THEN
                        RAISE EXCEPTION
                            'expired evidence can only be restored by the restore function'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.fetched_at <= OLD.expired_at THEN
                        RAISE EXCEPTION 'restored evidence must be fetched after it expired'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END IF;
                IF OLD.expired_at IS NULL THEN
                    RAISE EXCEPTION 'held raw evidence cannot be changed' USING ERRCODE = '23514';
                END IF;
                RAISE EXCEPTION 'a raw evidence tombstone cannot be changed'
                    USING ERRCODE = '23514';
            END
            $fn$
            """
        )
        op.execute(
            "CREATE TRIGGER lifecycle BEFORE UPDATE ON evidence.raw_row "
            "FOR EACH ROW EXECUTE FUNCTION evidence.raw_row_lifecycle()"
        )


def downgrade() -> None:
    with as_owner():
        op.execute("DROP TRIGGER lifecycle ON evidence.raw_row")
        op.execute("DROP FUNCTION evidence.raw_row_lifecycle()")
