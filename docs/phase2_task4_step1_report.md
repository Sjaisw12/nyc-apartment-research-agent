# Phase 2, Task 4, Step 1 report: PostgreSQL infrastructure, roles and schemas

| Commit | Content |
|---|---|
| `741eb36` | Design update with the six required corrections |
| `9bfe1ad` | Step 1 implementation |

Both are on `claude/project-thread-5d0l0f`. Nothing has been merged or deployed. No
tables for records or evidence exist yet; those arrive in steps 2 to 5. Task 1 and
Task 2 HPD Online validations remain **PENDING**.

## What changed

- **Dependencies (D17):** SQLAlchemy 2.1 (Core only), psycopg 3.3 (binary) and Alembic.
  They are pinned in `uv.lock`.
- **`storage/db.py`:** `make_engine()` always sets `hide_parameters=True` and
  `echo=False`. SQL parameters, which will carry raw rows and digests from step 2 on,
  never reach logs or exception text. `DATABASE_URL` is a `SecretStr` setting; a blank
  value means unset, and no engine can be made without it.
- **Alembic:** `alembic.ini` holds no URL. `migrations/env.py` takes a connection from
  the caller (the tests) or `DATABASE_URL`. Offline SQL mode is refused, and SQLAlchemy
  statement logging stays at WARNING.
- **Migration 0001:**
  - **Group roles.** Five NOLOGIN roles, created only if missing: `nyc_owner`,
    `app_reader`, `ingest_writer`, `evidence_admin`, `evidence_retention`. Roles are
    cluster-wide, which is why they are only created when missing.
  - **Schemas.** `records` and `evidence`, both owned by `nyc_owner`.
  - **Taken away from `PUBLIC`:**
    - database `CONNECT` and `TEMPORARY`; only the five roles may connect;
    - every right on both schemas;
    - `CREATE` on `public`;
    - `EXECUTE` on any function `nyc_owner` creates, through default privileges.
  - **Schema `USAGE`:**
    - `records`: `app_reader` and `ingest_writer`;
    - `evidence`: `ingest_writer`, `evidence_admin` and `evidence_retention`;
    - `app_reader` gets none on `evidence`.
  - **Default privileges for future tables owned by `nyc_owner`:**
    - `records`: `app_reader` SELECT; `ingest_writer` SELECT and INSERT, with no UPDATE
      or DELETE.
    - `evidence`: `evidence_admin` SELECT only. The writer's INSERT on `raw_row` and the
      retention job's UPDATE will be granted per table in step 2, so the ingest role can
      write raw rows but never read them back.
  - **Downgrade:** drops both schemas and restores `PUBLIC`'s database defaults. It keeps
    the roles, because they may serve other databases.
- **`docker-compose.yml`:** a local PostgreSQL 16, bound to 127.0.0.1.
- **`.env.example`:** documents `DATABASE_URL`, `TEST_DATABASE_ADMIN_URL` and
  `REQUIRE_DB_TESTS`.
- **CI:** a new `postgres` job runs a `postgres:16` service container. The job:
  - prints the server version;
  - runs every `db` test with `REQUIRE_DB_TESTS=1`;
  - runs the Alembic CLI up, down, up on its own database.

  The existing offline job is unchanged.

## Migration behaviour (verified)

- **Up:** both schemas are owned by `nyc_owner`, the five roles are NOLOGIN, and the
  version is `0001`.
- **Down:** the schemas are gone, the roles remain, `alembic_version` is empty, and
  `PUBLIC` has `CONNECT` again.
- **Up again:** applies cleanly with the roles already present.
- **Failure:** a migration that fails part-way (a pre-existing `evidence` schema) leaves
  nothing behind. `records` is rolled back and no version is recorded.

## Tests

- **14 new `db` tests**, each on a disposable `nyc_test_<random>` database that is
  dropped afterwards. They cover:
  - server version 16;
  - up, down and up;
  - full rollback of a failing migration;
  - removed `PUBLIC` rights;
  - schema grants per role, and that only the owner can create objects;
  - the exact default-ACL entries;
  - seven role tests that connect as **real LOGIN users**, created and dropped per test:
    - `app_reader` reads `records`, but every write is denied. It cannot reach
      `evidence`, not even to resolve a table name. It is a member of no other role, and
      it cannot execute an owner-created function;
    - no `SECURITY DEFINER` functions exist, and `PUBLIC` holds no `EXECUTE` in our
      schemas;
    - `ingest_writer` inserts but cannot update, delete or read evidence;
    - `evidence_admin` reads evidence only;
    - `evidence_retention` has no default rights;
    - a login with no group role cannot connect at all.
- **4 new offline unit tests** for engine settings:
  - parameters are hidden;
  - the psycopg 3 driver is used;
  - the password is not shown in the URL repr;
  - a missing or blank URL raises an explicit error.
- **Local results**, from a PostgreSQL 16.15 cluster with password (scram) auth:
  - offline suite, no database: 487 passed, 37 skipped (23 live and 14 db);
  - with the database required: 501 passed, 23 skipped (live only);
  - database required but none configured: the db tests error, as intended;
  - live: 23 passed;
  - lint and format: clean;
  - Phase 1 benchmark: unchanged;
  - Alembic CLI up, down, up: clean.
- **CI:** run 39717967179 for `9bfe1ad` is green.
  - The offline job passed: lint, format, offline suite and benchmark.
  - The new `postgres` job passed on a `postgres:16` service (16.15). The `db` tests ran
    with `REQUIRE_DB_TESTS=1`, where any skipped test fails the job. The Alembic CLI up,
    down, up also passed.
  - The service log shows the expected `permission denied` errors raised by the role
    tests.

## Integrity guarantees in place now

- Only `nyc_owner` can create objects in our schemas. All future tables inherit the
  grants above without per-migration grant code.
- `app_reader` has no route to private evidence through schema rights, role
  membership or function execution.
- No SQL parameter is logged, and no URL is stored in config.
- Migrations are transactional. A failure leaves the database as it was.

## Limitations

- No data tables yet, so atomic saves, constraints, round-trip, privacy scanning and
  retention are not implemented. The design specifies them and they arrive in steps 2
  to 6.
- **Migration privileges:** 0001 needs a login with `CREATEROLE`, and the right to grant
  `nyc_owner` to itself. Most managed PostgreSQL services allow this for the owner
  login. If roles are pre-created by an administrator, that administrator must also
  grant `nyc_owner` to the migration login.
- `docker compose` could not be exercised in this environment (no Docker daemon). The
  same PostgreSQL 16 configuration was tested on a local cluster and is tested in CI.
- The CI service password is a throwaway value for a disposable container, not a real
  credential.

## Interview concepts

- **Least privilege with group roles:**
  - privileges attach to NOLOGIN roles and deployments grant them to logins;
  - one owner role owns everything, so default privileges apply uniformly;
  - the write role cannot read back what it writes to the private schema.
- **Default privileges:** grants are attached to future objects at creation time, so
  a forgotten GRANT in a later migration cannot open or close access by accident.
- **Testing security with real logins**, not just `SET ROLE` or catalog checks. Name
  resolution itself is denied without schema `USAGE`.
- **Transactional DDL in PostgreSQL:** a failed migration rolls back completely, which
  many other databases cannot do.
- **Tests that must not silently skip:** the offline suite stays database-free, while CI
  turns a skipped database test into a failure.
