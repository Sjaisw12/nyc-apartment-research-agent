"""Source-row lineage for records that stand for several source rows (Task 4, step 5).

HPD registrations and registration contacts have no unique business key. When identical
rows arrive under different Socrata `:id`s, the normalizer keeps ONE record and lists
every row in its `source_rows` (itself first). So "one record = one source row" no
longer holds, and three counts must be kept apart:

  fetched source rows  provenance.rows_fetched / source_count (what Socrata returned)
  normalized records   source_fetch.record_count (rows in normalized_record)
  stored links         source_fetch.source_row_count (rows in record_source_row)

Changes to 0004:
  - DROP UNIQUE (fetch_id, original_digest) on record_source_row. In an incomplete fetch
    the pager can return the same `:id` twice (it then marks the fetch incomplete); both
    copies are collapsed into one record whose source_rows lists the same row twice, and
    that must be storable as it was fetched. Uniqueness is now checked where it holds:
    at commit, for records_found fetches only.
  - Index on record_source_row (fetch_id) for the commit-time checks.
  - records.check_fetch_complete() is replaced. Kept from 0004: stored counts match the
    rows; a record's first link is its own raw row; no position gaps; raw-row basis
    equals the record's digest basis. New:
      * a record whose payload has source_rows: the payload list equals its links in
        position order, by (source_row_id, original_digest). No row can be dropped,
        added or reordered between the model and the database;
      * a record without source_rows: exactly one link, with no source_row_id;
      * a link's source_record_id is its own source_row_id when it has one;
      * records_found: no raw row is linked twice in the fetch; with row-level lineage,
        links equal source_count and every source_row_id is distinct; without it, the
        0004 rule (equal, or fewer only when identical duplicates were collapsed).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""

# ruff: noqa: S608  (SQL assembled from constant fragments only; no input)

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_HEAD = """
    CREATE OR REPLACE FUNCTION records.check_fetch_complete() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $fn$
    DECLARE
        f records.source_fetch;
        n_records integer;
        n_pages integer;
        n_links integer;
        collapsed boolean;
"""

_COUNTS_AND_FIRST_LINK = """
    BEGIN
        SELECT * INTO f FROM records.source_fetch WHERE id = NEW.id;
        SELECT count(*) INTO n_records FROM records.normalized_record WHERE fetch_id = f.id;
        SELECT count(*) INTO n_pages FROM records.fetch_page WHERE fetch_id = f.id;
        SELECT count(*) INTO n_links FROM records.record_source_row WHERE fetch_id = f.id;
        IF n_records <> f.record_count OR n_pages <> f.page_count
           OR n_links <> f.source_row_count THEN
            RAISE EXCEPTION 'fetch %: stored counts do not match its rows', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.normalized_record r
            WHERE r.fetch_id = f.id AND NOT EXISTS (
                SELECT 1 FROM records.record_source_row l
                WHERE l.record_id = r.id AND l.position = 0
                  AND l.original_digest = r.original_digest)
        ) THEN
            RAISE EXCEPTION 'fetch %: a record''s first source row is not its own row', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.record_source_row
            WHERE fetch_id = f.id GROUP BY record_id HAVING max(position) + 1 <> count(*)
        ) THEN
            RAISE EXCEPTION 'fetch %: source-row positions have gaps', f.id
                USING ERRCODE = '23514';
        END IF;
"""

_BASIS_AND_END = """
        IF EXISTS (
            SELECT 1 FROM records.record_source_row l
            JOIN records.normalized_record r ON r.id = l.record_id
            JOIN records.evidence_availability e ON e.original_digest = l.original_digest
            WHERE l.fetch_id = f.id AND e.basis <> r.digest_basis
        ) THEN
            RAISE EXCEPTION
                'fetch %: raw row basis differs from the record''s digest basis', f.id
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END
    $fn$
"""

NEW_CHECK = (
    _HEAD
    + "        row_level boolean;\n"
    + _COUNTS_AND_FIRST_LINK
    + """
        -- Row-level lineage: the payload's source_rows are exactly the stored links.
        IF EXISTS (
            SELECT 1 FROM records.normalized_record r
            WHERE r.fetch_id = f.id AND r.payload ? 'source_rows'
              AND r.payload -> 'source_rows' IS DISTINCT FROM (
                  SELECT jsonb_agg(jsonb_build_object(
                             'source_row_id', l.source_row_id,
                             'original_digest', l.original_digest) ORDER BY l.position)
                  FROM records.record_source_row l WHERE l.record_id = r.id)
        ) THEN
            RAISE EXCEPTION 'fetch %: a record''s source_rows differ from its stored links', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.normalized_record r
            WHERE r.fetch_id = f.id AND NOT (r.payload ? 'source_rows')
              AND (SELECT count(*) = 1 AND bool_and(l.source_row_id IS NULL)
                   FROM records.record_source_row l WHERE l.record_id = r.id) IS NOT TRUE
        ) THEN
            RAISE EXCEPTION 'fetch %: a single-row record must have exactly one plain link', f.id
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM records.record_source_row l
            JOIN records.normalized_record r ON r.id = l.record_id
            WHERE l.fetch_id = f.id
              AND l.source_record_id <> coalesce(l.source_row_id, r.source_record_id)
        ) THEN
            RAISE EXCEPTION 'fetch %: a link''s source id differs from its row', f.id
                USING ERRCODE = '23514';
        END IF;
        IF f.status = 'records_found' THEN
            IF (SELECT count(DISTINCT original_digest) FROM records.record_source_row
                WHERE fetch_id = f.id) <> n_links THEN
                RAISE EXCEPTION 'fetch %: a source row is linked more than once', f.id
                    USING ERRCODE = '23514';
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM records.record_source_row
                WHERE fetch_id = f.id AND source_row_id IS NOT NULL
            ) INTO row_level;
            SELECT EXISTS (
                SELECT 1 FROM records.normalized_record
                WHERE fetch_id = f.id AND 'duplicate_id_same_content' = ANY (quality_flags)
            ) INTO collapsed;
            IF row_level THEN
                IF n_links <> f.source_count
                   OR (SELECT count(DISTINCT source_row_id) FROM records.record_source_row
                       WHERE fetch_id = f.id) <> n_links THEN
                    RAISE EXCEPTION
                        'fetch %: source rows do not account for the source count', f.id
                        USING ERRCODE = '23514';
                END IF;
            ELSIF (collapsed AND n_links >= f.source_count)
                  OR (NOT collapsed AND n_links <> f.source_count) THEN
                RAISE EXCEPTION 'fetch %: source rows do not account for the source count', f.id
                    USING ERRCODE = '23514';
            END IF;
        END IF;
"""
    + _BASIS_AND_END
)

# The 0004 body, restored on downgrade.
OLD_CHECK = (
    _HEAD
    + _COUNTS_AND_FIRST_LINK
    + """
        IF f.status = 'records_found' THEN
            SELECT EXISTS (
                SELECT 1 FROM records.normalized_record
                WHERE fetch_id = f.id AND 'duplicate_id_same_content' = ANY (quality_flags)
            ) INTO collapsed;
            IF (collapsed AND n_links >= f.source_count)
               OR (NOT collapsed AND n_links <> f.source_count) THEN
                RAISE EXCEPTION 'fetch %: source rows do not account for the source count', f.id
                    USING ERRCODE = '23514';
            END IF;
        END IF;
"""
    + _BASIS_AND_END
)


def upgrade() -> None:
    with as_owner():
        op.execute(
            "ALTER TABLE records.record_source_row "
            "DROP CONSTRAINT record_source_row_fetch_id_original_digest_key"
        )
        op.execute("CREATE INDEX record_source_row_fetch ON records.record_source_row (fetch_id)")
        op.execute(NEW_CHECK)


def downgrade() -> None:
    with as_owner():
        op.execute(OLD_CHECK)
        op.execute("DROP INDEX records.record_source_row_fetch")
        op.execute(
            "ALTER TABLE records.record_source_row "
            "ADD CONSTRAINT record_source_row_fetch_id_original_digest_key "
            "UNIQUE (fetch_id, original_digest)"
        )
