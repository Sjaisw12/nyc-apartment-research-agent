# Phase 2, Task 4, Step 2 report: private evidence table and atomic evidence writes

Commit `4a14c19` is on `claude/project-thread-5d0l0f`. Nothing has been merged or
deployed. Step 3 has not started. The Task 1 and Task 2 HPD Online validations remain
**PENDING**.

## How each requirement was met

### 1. Migration ownership

`env.py` does not switch roles, so migrations now wrap their DDL in `as_owner()`
(`storage/migration_helpers.py`). It runs `SET LOCAL ROLE nyc_owner` inside the
migration's own transaction (Alembic runs one transaction per migration), then
`RESET ROLE` before Alembic writes `alembic_version`. If the migration fails, the role
switch rolls back with it.

Tested:
- `evidence.raw_row`, `records.evidence_availability` and the restore function are owned
  by `nyc_owner`;
- `alembic_version` stays with the migrating login;
- the inherited grants on the new table and view match exactly what was intended, for
  all four roles, including column-level rights.

### 2. Evidence store permissions

`EvidenceStore` is now split into a **writer** side (`put`, the only call adapters make)
and a **reader** side (`get`).

- **Retrieval** uses `BufferedEvidenceStore`, an in-memory buffer. Nothing touches the
  database during retrieval.
- **Writing:** `write_raw_rows(conn, ...)` runs on the caller's transaction as
  `ingest_writer` and never commits. That role holds `INSERT` on `evidence.raw_row` and
  nothing else.
- **Reading:** `PostgresEvidenceReader` is for `evidence_admin` only. It re-checks each
  row's digest when it reads it.

What each SQL operation did as `ingest_writer`, run by a real login:

| Operation | Result |
|---|---|
| `INSERT … ON CONFLICT DO NOTHING`, twice | One row stored |
| Plain duplicate `INSERT` | Unique violation; nothing is silently overwritten |
| `ON CONFLICT (original_digest) DO NOTHING` | Permission denied: naming a conflict target needs `SELECT` on that column |
| `ON CONFLICT … DO UPDATE` | Permission denied |
| `… RETURNING original_digest` | Permission denied |
| `SELECT` (any column), `UPDATE`, `DELETE` on `raw_row` | Permission denied |
| `SELECT` on the availability view | Allowed (metadata only) |
| `INSERT` or `UPDATE` through the view | Permission denied |

Because of the conflict-target result, the writer uses `ON CONFLICT DO NOTHING` with no
target. The primary key is the table's only unique constraint, so nothing else can be
absorbed. **I discovered this while testing; the design originally named the target.**

### 3. Expiry and reinsertion

Expiry leaves a **tombstone**. `original_row` and `original_sha256` become null and
`expired_at` is set. The digest, dataset, source ID, basis, redacted hash and dates stay,
so lineage still resolves. `expire_due()` runs as `evidence_retention`, which has
column-level rights only and cannot read row content.

If a writer supplies an original row whose digest is already a tombstone, it is **not
ignored**. The writer calls `evidence.restore_expired_raw_row(...)`, a narrowly authorized
function:
- `SECURITY DEFINER`, owned by `nyc_owner`, with a pinned `search_path`;
- `EXECUTE` is granted to `ingest_writer` only;
- it updates a row only if that row is a tombstone **and** its dataset, source record ID,
  basis and redacted hash all match;
- otherwise it raises, and the whole save rolls back;
- it cannot overwrite held evidence and returns nothing.

The writer's own permissions were not broadened. It is the only `SECURITY DEFINER`
function, and the step 1 test now allow-lists it by name. It also checks that every such
function has a pinned search path.

### 4. Atomicity and foreign keys

Evidence stays in memory until it is saved. `write_raw_rows` takes the caller's
connection, so step 3's `save_result` will write raw rows, the fetch, records and
source-row links in **one transaction**.

The planned `first_seen_fetch_id` is **dropped** and replaced by `first_seen_at`. A
foreign key from raw rows to fetches would point at rows that do not exist in step 2,
and in step 3 it would force the fetch to be saved before its evidence. Instead, the
save order will be raw rows first, then the fetch, then records and links (links
reference raw rows). Lineage is fetch → record → source-row link → raw row, with no
circular dependency.

### 5. Availability view

`records.evidence_availability` has five columns: `original_digest`, `basis`, `held`,
`retain_until`, `expired_at`. It holds no row content and no plain hash. Tests confirm:
- `app_reader` can read it, and gets exactly those five columns;
- the same login is denied on `evidence.raw_row` and on the restore function;
- nobody can write through the view.

### 6. Tests and results

- **16 new database tests** in `tests/db/test_evidence.py`. Each uses one real login per
  role. They cover:
  - ownership and grants;
  - the exact SQL operations above;
  - idempotency: three items written twice, stored once;
  - rollback: a failure after the evidence write, either a SQL error or a Python
    exception, leaves zero rows;
  - tamper checks: a wrong row, a wrong key or a time without a zone is refused before
    anything is written;
  - database errors scrubbed of row content, and not chained;
  - a configured digest key required;
  - digest round-trip through JSONB, which still verifies;
  - basis kept: `original_row` and `sanitized_row` both preserved;
  - real adapter output, moved from the buffer to PostgreSQL;
  - expiry tombstones and lineage, including that nothing expires before it is due;
  - restore after expiry;
  - restore refused when metadata does not match, which rolls back the whole write;
  - restore refused on held evidence.
- **Updated step 1 tests:** the head is now 0002; up/down/up includes a one-step
  downgrade; and the `SECURITY DEFINER` allow-list.
- **3 new offline tests** for the buffer.
- **Local results**, on PostgreSQL 16.15 with password auth:

| Check | Result |
|---|---|
| Offline suite, no database | 490 passed, 53 skipped (23 live, 30 db) |
| Database required | 520 passed, 23 skipped (live only) |
| Live tests | 23 passed |
| Lint and format | Clean |
| Phase 1 benchmark | Unchanged: 14/14 correct auto-matches, 0 wrong building, 6/6 ambiguity handled, 4/4 correct abstentions, 2/2 unresolved BINs preserved |
| Alembic CLI | 0001 → 0002 → base → 0002 clean; no leftover test databases or roles |

- **CI:** run 39721276573 for `4a14c19` is green on both jobs:
  - the offline job: lint, format, offline suite and benchmark;
  - the `postgres` job on a `postgres:16` service, with `REQUIRE_DB_TESTS=1`, so any
    skipped database test would have failed it. Alembic CLI up, down, up also passed.

## Limitations

- **Error detail in the server log.** PostgreSQL's server log can contain the failing
  row in an error's DETAIL. Python-side errors are scrubbed. Production should set
  `log_error_verbosity = terse` for the database; that needs a superuser and is a
  deployment setting, not testable here.
- **Inserted count.** It counts rows absent before the write, so an identical row
  inserted at the same moment by another transaction is counted in both.
- **Retention on a repeat sighting.** Seeing a held row again does not extend its
  retention; this is left for review in step 6. Restored rows get a new 180 days from
  the new fetch.
- **No scheduled retention job yet.** Only the expiry function and its tests exist.
  Scheduling, backups, and the window after deletion come in step 6.
- **Reader timestamps.** `PostgresEvidenceReader` returns `fetched_at` in PostgreSQL's
  timestamp format. Digests do not depend on it.

## Interview concepts

- **Column-level privileges and `ON CONFLICT`.** An insert-only role can deduplicate
  with `ON CONFLICT DO NOTHING`, but naming a conflict target or using `RETURNING`
  needs read access. Testing the exact statement as the real role caught this.
- **Content-addressed storage.** The keyed digest is the primary key, so re-saving is
  idempotent by construction.
- **Tombstones versus deletes.** Expiry removes the sensitive content but keeps the
  identity, so foreign keys and lineage survive. The public view distinguishes "a
  digest exists" from "the original can still be verified".
- **Narrow privilege escalation.** One audited `SECURITY DEFINER` function with a pinned
  search path, a single permitted state change and strict preconditions, instead of
  granting `UPDATE` to the writer.
- **Transaction composition.** Write functions take the caller's connection and never
  commit, so steps compose into one atomic save.
- **Error hygiene.** Database error messages can quote data. Re-raising without the
  original exception keeps raw rows out of logs and tracebacks.
