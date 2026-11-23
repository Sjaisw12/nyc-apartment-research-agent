"""Private raw-evidence table, availability view, narrow restore function (Task 4, step 2).

All objects are created as nyc_owner (storage/migration_helpers.py), so they are owned by it and
pick up the default privileges from 0001.

evidence.raw_row: one unredacted source row per keyed digest (content-addressed).
  - Held:    original_row and original_sha256 present, expired_at null.
  - Expired: a tombstone. original_row and original_sha256 are null, expired_at set;
             digest, dataset, source id, basis, redacted hash and dates remain, so
             lineage from public records still resolves. (The plain hash is removed
             too: it could be brute-forced back to unit details.)
  - No foreign key to source fetches: those arrive in step 3, and the save order there
    is raw rows first, then the fetch and its records and source-row links, all in one
    transaction. Lineage is fetch -> record -> source-row link -> raw row.

Grants on raw_row (beyond 0001's defaults, which give evidence_admin SELECT):
  - ingest_writer:      INSERT only. It cannot read, update or delete raw rows.
  - evidence_retention: SELECT and UPDATE on the expiry columns only; it can never read
                        original_row.

records.evidence_availability: digest, basis, held, retain_until, expired_at. Owned by
nyc_owner, so app_reader can read it without any right on the evidence schema. It holds
no row content and no plain hash. INSERT, which 0001's defaults would give ingest_writer
on any records relation, is revoked.

evidence.restore_expired_raw_row(...): the one narrowly authorized way to put an original
row back under a digest whose evidence has expired. SECURITY DEFINER with a fixed
search_path, EXECUTE for ingest_writer only. It updates a row only if it is a tombstone
AND its dataset, source record id, basis and redacted hash all match; otherwise it
raises. It can never overwrite held evidence and returns no row content.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

HEX64 = "'^[0-9a-f]{64}$'"


def upgrade() -> None:
    with as_owner():
        op.execute(
            f"""
            CREATE TABLE evidence.raw_row (
                original_digest   text PRIMARY KEY CHECK (original_digest ~ {HEX64}),
                dataset_id        text NOT NULL
                                  CHECK (dataset_id ~ '^[a-z0-9]{{4}}-[a-z0-9]{{4}}$'),
                source_record_id  text NOT NULL CHECK (source_record_id <> ''),
                basis             text NOT NULL CHECK (basis IN ('original_row', 'sanitized_row')),
                original_row      jsonb CHECK (jsonb_typeof(original_row) = 'object'),
                original_sha256   text CHECK (original_sha256 ~ {HEX64}),
                redacted_sha256   text NOT NULL CHECK (redacted_sha256 ~ {HEX64}),
                redacted_fields   text[] NOT NULL DEFAULT '{{}}',
                query_url         text NOT NULL,
                fetched_at        timestamptz NOT NULL,
                data_as_of        text,
                first_seen_at     timestamptz NOT NULL DEFAULT now(),
                retain_until      timestamptz NOT NULL,
                expired_at        timestamptz,
                restored_at       timestamptz,
                restore_count     integer NOT NULL DEFAULT 0 CHECK (restore_count >= 0),
                CONSTRAINT held_or_tombstone CHECK (
                    (expired_at IS NULL) = (original_row IS NOT NULL)
                    AND (original_row IS NULL) = (original_sha256 IS NULL)
                ),
                CONSTRAINT retention_after_fetch CHECK (retain_until > fetched_at)
            )
            """
        )
        op.execute(
            "CREATE INDEX raw_row_due ON evidence.raw_row (retain_until) WHERE expired_at IS NULL"
        )
        op.execute("GRANT INSERT ON evidence.raw_row TO ingest_writer")
        op.execute(
            "GRANT SELECT (original_digest, retain_until, expired_at), "
            "UPDATE (original_row, original_sha256, expired_at) "
            "ON evidence.raw_row TO evidence_retention"
        )

        op.execute(
            """
            CREATE VIEW records.evidence_availability AS
            SELECT original_digest, basis, (expired_at IS NULL) AS held, retain_until, expired_at
            FROM evidence.raw_row
            """
        )
        op.execute("REVOKE INSERT ON records.evidence_availability FROM ingest_writer")

        op.execute(
            """
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
        )
        op.execute(
            "REVOKE ALL ON FUNCTION evidence.restore_expired_raw_row"
            "(text, text, text, text, text, text, jsonb, timestamptz) FROM PUBLIC"
        )
        op.execute(
            "GRANT EXECUTE ON FUNCTION evidence.restore_expired_raw_row"
            "(text, text, text, text, text, text, jsonb, timestamptz) TO ingest_writer"
        )


def downgrade() -> None:
    with as_owner():
        op.execute(
            "DROP FUNCTION evidence.restore_expired_raw_row"
            "(text, text, text, text, text, text, jsonb, timestamptz)"
        )
        op.execute("DROP VIEW records.evidence_availability")
        op.execute("DROP TABLE evidence.raw_row")
