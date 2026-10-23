"""Canonical building IDs in registration coverage (Task 4, step 5 correction 2).

0008 made both coverage arrays distinct, but distinct is not enough: ['1', ''],
['1', '   '] and ['1', ' 1'] are distinct arrays that name one real building, so they
could still pass as a shared registration or an off-lot building. Reproduced as
ingest_writer. Building IDs are compared exactly and are never transformed here, so
each element must be canonical: non-blank, with no leading or trailing whitespace.

Additive: one helper and one CHECK. NULL arrays (unknown coverage) still pass; NULL
elements are already refused by 0008.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23
"""

from alembic import op

from nyc_apartment_agent.storage.migration_helpers import as_owner

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with as_owner():
        op.execute(
            r"""
            CREATE FUNCTION records.text_array_canonical(a text[]) RETURNS boolean
            LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
            AS $fn$
                SELECT a IS NULL OR NOT EXISTS (
                    SELECT 1 FROM unnest(a) AS x
                    WHERE x !~ '^[^[:space:]](.*[^[:space:]])?$'
                )
            $fn$
            """
        )
        # Called from a CHECK with the inserting role's own rights.
        op.execute(
            "GRANT EXECUTE ON FUNCTION records.text_array_canonical(text[]) TO ingest_writer"
        )
        op.execute(
            """
            ALTER TABLE records.registration_coverage
            ADD CONSTRAINT building_ids_canonical CHECK (
                records.text_array_canonical(building_ids_on_lot)
                AND records.text_array_canonical(covered_building_ids)
            )
            """
        )


def downgrade() -> None:
    with as_owner():
        op.execute(
            "ALTER TABLE records.registration_coverage DROP CONSTRAINT building_ids_canonical"
        )
        op.execute("DROP FUNCTION records.text_array_canonical(text[])")
