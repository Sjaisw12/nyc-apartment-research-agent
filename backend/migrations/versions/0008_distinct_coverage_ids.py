"""Distinct building IDs in registration coverage (Task 4, step 5 correction).

0007 derives shared_registration and covered_buildings_off_lot from array cardinality,
which only counts buildings if each ID appears once. A row with
building_ids_on_lot = ['1'] and covered_building_ids = ['1', '1'] passed as "shared,
one off-lot building" although it names one building. Reproduced as ingest_writer.

Additive: one helper and one CHECK. Each array must hold distinct, non-NULL IDs, so
cardinality equals the number of distinct buildings and the 0007 derivations hold.
covered_building_ids stays NULL when coverage is unknown (NULL passes this CHECK and
the existing ones still map it to "sharing unknown").

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with as_owner():
        op.execute(
            """
            CREATE FUNCTION records.text_array_distinct(a text[]) RETURNS boolean
            LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
            AS $fn$
                SELECT a IS NULL OR (
                    array_position(a, NULL) IS NULL
                    AND cardinality(a) = (SELECT count(DISTINCT x) FROM unnest(a) AS x)
                )
            $fn$
            """
        )
        # Called from a CHECK with the inserting role's own rights.
        op.execute("GRANT EXECUTE ON FUNCTION records.text_array_distinct(text[]) TO ingest_writer")
        op.execute(
            """
            ALTER TABLE records.registration_coverage
            ADD CONSTRAINT building_ids_distinct CHECK (
                records.text_array_distinct(building_ids_on_lot)
                AND records.text_array_distinct(covered_building_ids)
            )
            """
        )


def downgrade() -> None:
    with as_owner():
        op.execute(
            "ALTER TABLE records.registration_coverage DROP CONSTRAINT building_ids_distinct"
        )
        op.execute("DROP FUNCTION records.text_array_distinct(text[])")
