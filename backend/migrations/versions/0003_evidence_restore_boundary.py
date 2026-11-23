"""Step 2 hardening: a trusted restore boundary and an enforced retention window.

Findings this migration answers (tested in tests/db/test_evidence_hardening.py):

1. evidence.restore_expired_raw_row cannot check digest integrity. It has no HMAC key,
   and the plain hash it is given comes from the caller (the tombstone no longer holds
   one). Called directly with matching metadata, it stored a row that did not match its
   digest. So it is not described as enforcing digest integrity. Instead:
   - EXECUTE moves from ingest_writer to a new NOLOGIN role, evidence_restorer, which
     holds nothing else. Only a login deliberately given that role (the server-side
     writer, which verifies every row under the key first) can restore. A login with
     only ingest_writer cannot.
   - The key is NOT copied into the database: that would put it in backups and in
     reach of anyone who can read the owner's objects.
   - Detection stays where the key is: PostgresEvidenceReader re-verifies every row it
     returns and refuses one that does not match.

2. A restore with an old source timestamp stored a row whose retain_until had already
   passed, and kept the first fetch's fetched_at, so retain_until no longer equalled
   fetched_at + retention. Now:
   - restore sets fetched_at, query_url and data_as_of to the restoring fetch, so those
     columns describe the copy actually held;
   - CHECK retain_until <= fetched_at + 180 days: no row can be held longer than the
     D15 maximum after its fetch (a shorter configured period is still allowed);
   - a trigger on INSERT and on UPDATE of fetched_at/retain_until refuses a row whose
     retain_until is not in the future, or whose fetched_at is in the future (5 minutes
     of clock skew allowed). This covers plain inserts and restores alike.

Downgrade restores 0002's function and grant exactly. The role is kept (cluster-wide).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD_SIGNATURE = (
    "evidence.restore_expired_raw_row(text, text, text, text, text, text, jsonb, timestamptz)"
)
NEW_SIGNATURE = (
    "evidence.restore_expired_raw_row("
    "text, text, text, text, text, text, jsonb, timestamptz, text, text, timestamptz)"
)
MAX_RETENTION = "interval '180 days'"
CLOCK_SKEW = "interval '5 minutes'"

# 0002's function, recreated verbatim on downgrade.
OLD_FUNCTION = """
CREATE FUNCTION evidence.restore_expired_raw_row(
    p_digest text, p_dataset_id text, p_source_record_id text, p_basis text,
    p_redacted_sha256 text, p_original_sha256 text, p_original_row jsonb,
    p_retain_until timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
    UPDATE evidence.raw_row
       SET original_row = p_original_row,
           original_sha256 = p_original_sha256,
           expired_at = NULL,
           retain_until = p_retain_until,
           restored_at = now(),
           restore_count = restore_count + 1
     WHERE original_digest = p_digest
       AND expired_at IS NOT NULL
       AND dataset_id = p_dataset_id
       AND source_record_id = p_source_record_id
       AND basis = p_basis
       AND redacted_sha256 = p_redacted_sha256;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 1 THEN
        RAISE EXCEPTION 'no expired evidence matches this digest and metadata'
            USING ERRCODE = 'P0002';
    END IF;
END
$fn$
"""

NEW_FUNCTION = """
CREATE FUNCTION evidence.restore_expired_raw_row(
    p_digest text, p_dataset_id text, p_source_record_id text, p_basis text,
    p_redacted_sha256 text, p_original_sha256 text, p_original_row jsonb,
    p_fetched_at timestamptz, p_query_url text, p_data_as_of text,
    p_retain_until timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $fn$
-- Enforces: tombstone state, matching metadata, and (via the table's check and trigger)
-- the retention window. Does NOT enforce that p_original_row matches p_digest: that
-- needs the HMAC key, which the database does not hold. Callers holding
-- evidence_restorer are trusted to verify first (storage/evidence_pg.py does).
DECLARE n integer;
BEGIN
    UPDATE evidence.raw_row
       SET original_row = p_original_row,
           original_sha256 = p_original_sha256,
           expired_at = NULL,
           fetched_at = p_fetched_at,
           query_url = p_query_url,
           data_as_of = p_data_as_of,
           retain_until = p_retain_until,
           restored_at = now(),
           restore_count = restore_count + 1
     WHERE original_digest = p_digest
       AND expired_at IS NOT NULL
       AND dataset_id = p_dataset_id
       AND source_record_id = p_source_record_id
       AND basis = p_basis
       AND redacted_sha256 = p_redacted_sha256;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 1 THEN
        RAISE EXCEPTION 'no expired evidence matches this digest and metadata'
            USING ERRCODE = 'P0002';
    END IF;
END
$fn$
"""


def _on_database(sql: str) -> str:
    return f"DO $$ BEGIN EXECUTE format('{sql}', current_database()); END $$;"


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evidence_restorer') "
        "THEN CREATE ROLE evidence_restorer NOLOGIN; END IF; END $$;"
    )
    # Database rights belong to the database owner, not nyc_owner (as in 0001).
    op.execute(_on_database("GRANT CONNECT ON DATABASE %I TO evidence_restorer"))
    with as_owner():
        op.execute("GRANT USAGE ON SCHEMA evidence TO evidence_restorer")

        op.execute(
            "ALTER TABLE evidence.raw_row ADD CONSTRAINT retention_at_most_180_days "
            f"CHECK (retain_until <= fetched_at + {MAX_RETENTION})"
        )
        op.execute(
            f"""
            CREATE FUNCTION evidence.raw_row_retention_window() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
            AS $fn$
            BEGIN
                IF NEW.retain_until <= now() THEN
                    RAISE EXCEPTION 'raw evidence is already past its retention date'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.fetched_at > now() + {CLOCK_SKEW} THEN
                    RAISE EXCEPTION 'raw evidence fetch time is in the future'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END
            $fn$
            """
        )
        op.execute(
            "CREATE TRIGGER retention_window BEFORE INSERT OR UPDATE OF fetched_at, retain_until "
            "ON evidence.raw_row FOR EACH ROW EXECUTE FUNCTION evidence.raw_row_retention_window()"
        )

        op.execute(f"DROP FUNCTION {OLD_SIGNATURE}")
        op.execute(NEW_FUNCTION)
        op.execute(f"REVOKE ALL ON FUNCTION {NEW_SIGNATURE} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {NEW_SIGNATURE} TO evidence_restorer")


def downgrade() -> None:
    with as_owner():
        op.execute(f"DROP FUNCTION {NEW_SIGNATURE}")
        op.execute(OLD_FUNCTION)
        op.execute(f"REVOKE ALL ON FUNCTION {OLD_SIGNATURE} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {OLD_SIGNATURE} TO ingest_writer")
        op.execute("DROP TRIGGER retention_window ON evidence.raw_row")
        op.execute("DROP FUNCTION evidence.raw_row_retention_window()")
        op.execute("ALTER TABLE evidence.raw_row DROP CONSTRAINT retention_at_most_180_days")
        op.execute("REVOKE USAGE ON SCHEMA evidence FROM evidence_restorer")
    op.execute(_on_database("REVOKE CONNECT ON DATABASE %I FROM evidence_restorer"))
