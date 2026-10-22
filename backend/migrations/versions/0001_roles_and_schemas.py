"""Roles, schemas, ownership, grants and default privileges (Task 4, step 1).

- Group roles (NOLOGIN), created only if missing because roles are cluster-wide:
  nyc_owner (owns everything), app_reader, ingest_writer, evidence_admin,
  evidence_retention. Deployments grant these to login users.
- Schemas `records` (application-readable) and `evidence` (private), owned by nyc_owner.
- PUBLIC loses CONNECT/TEMP on the database, all rights on both schemas, and EXECUTE on
  functions nyc_owner creates. CREATE on schema `public` is revoked (already the
  PostgreSQL 15+ default; made explicit).
- Default privileges for future tables owned by nyc_owner:
    records:  app_reader SELECT; ingest_writer SELECT, INSERT (no UPDATE/DELETE)
    evidence: evidence_admin SELECT. Writers get per-table grants in step 2, so
              ingest_writer can INSERT raw rows but never read them back.

Downgrade drops both schemas and restores PUBLIC's database defaults. Roles are kept:
they are cluster-wide and may be used by other databases.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = ("nyc_owner", "app_reader", "ingest_writer", "evidence_admin", "evidence_retention")
CONNECTING = ("app_reader", "ingest_writer", "evidence_admin", "evidence_retention")


def _on_database(sql: str) -> str:
    """Run a statement against the current database (its name is not known in advance)."""
    return f"DO $$ BEGIN EXECUTE format('{sql}', current_database()); END $$;"


def upgrade() -> None:
    for role in ROLES:  # constant names defined above; nothing from input reaches SQL
        op.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "  # noqa: S608
            f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;"
        )
    # The migrating login must be able to act as the owner for later migrations.
    op.execute("GRANT nyc_owner TO CURRENT_USER")

    op.execute("CREATE SCHEMA records AUTHORIZATION nyc_owner")
    op.execute("CREATE SCHEMA evidence AUTHORIZATION nyc_owner")
    op.execute("REVOKE ALL ON SCHEMA records FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA evidence FROM PUBLIC")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    op.execute(_on_database("REVOKE ALL ON DATABASE %I FROM PUBLIC"))
    op.execute(_on_database(f"GRANT CONNECT ON DATABASE %I TO nyc_owner, {', '.join(CONNECTING)}"))

    op.execute("GRANT USAGE ON SCHEMA records TO app_reader, ingest_writer")
    op.execute(
        "GRANT USAGE ON SCHEMA evidence TO ingest_writer, evidence_admin, evidence_retention"
    )

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE nyc_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE nyc_owner IN SCHEMA records "
        "GRANT SELECT ON TABLES TO app_reader"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE nyc_owner IN SCHEMA records "
        "GRANT SELECT, INSERT ON TABLES TO ingest_writer"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE nyc_owner IN SCHEMA evidence "
        "GRANT SELECT ON TABLES TO evidence_admin"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS evidence CASCADE")
    op.execute("DROP SCHEMA IF EXISTS records CASCADE")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE nyc_owner GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
    op.execute(
        _on_database(f"REVOKE CONNECT ON DATABASE %I FROM nyc_owner, {', '.join(CONNECTING)}")
    )
    op.execute(_on_database("GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC"))
