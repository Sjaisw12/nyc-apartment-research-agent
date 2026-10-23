# Phase 2, Task 4: PostgreSQL storage — completion report

Date: 2026-10-23. Branch: `claude/project-thread-5d0l0f`. Migrations at head: `0010`.
Status: **all six steps implemented; final acceptance requested.** Nothing is deployed,
merged or scheduled. The independent HPD Online validations (Task 1 Part A, Task 2 Part
B) remain **PENDING**.

Detail lives in `docs/phase2_task4_postgres_design.md` (sections 10.1 to 10.4c), the
per-step reports (`phase2_task4_step1_report.md` to `step5_report.md`) and the runbook
`docs/operations/evidence_retention_runbook.md`. This report is the summary of record.

## 1. What Task 4 delivers

Every result the HPD adapters produce (violations, complaint problems and groups,
building records, registrations, registration contacts with coverage, and BuildingID
attribution reports) can be saved as an append-only, validated snapshot in PostgreSQL 16
and loaded back into equal Pydantic models, with row-level lineage to private raw
evidence that expires after at most 180 days.

| Step | Scope | Commits |
|---|---|---|
| 1 | Alembic, roles, schemas, grants, CI service job | 9bfe1ad, 4481dfa |
| 2 | `evidence.raw_row`, availability view, atomic writes, restore boundary (0002, 0003) | 4a14c19, 03dd064, a5a4f8a |
| 3 | Runs, fetches, pages, records, source-row links for violations (0004) | bd7956e, 4ab3cd2 |
| 4 | Complaint problems and groups (0005) | 01c1dfc, 89998a6 |
| 5 | Multi-row lineage, buildings, registrations, contacts, coverage, D9 link (0006 to 0009) | f5fc070, c26b4f0, 0b615d2, a93b400, c8e9a5a |
| 6 | Lifecycle rules (0010), retention job, privacy audit, expiry integrity, operations | 32b6c02, 759474d, 884060d, e1f34e8, 7cc5bed, and this report |

## 2. Architecture and schema

Two schemas, both owned by the NOLOGIN role `nyc_owner`:

- **`records`** (application-readable):
  - `retrieval_run`: one per request; a keyed digest of the typed input (never the text)
    and a whitelisted resolver projection;
  - `source_fetch`: one per source result, typed status columns plus the result payload
    without its record lists, and `buildings_fetch_id` for D9;
  - `fetch_page`: one per HTTP response;
  - `normalized_record`: one per record, typed attribution columns plus the public model;
  - `record_source_row`: the links from a record to each raw row it came from;
  - `complaint_group`: one per complaint;
  - `registration_coverage`: one per registration a contacts fetch checked;
  - views `evidence_availability` (held or expired, no content) and `evidence_identity`
    (digest, dataset, source id; `ingest_writer` only).
- **`evidence`** (private): `raw_row`, one unredacted source row per keyed digest, which
  becomes a tombstone on expiry; and the `SECURITY DEFINER` restore function.

Saves run in one transaction per snapshot (`save_research_snapshot`): raw evidence
first, then the run, building records, the other results, records, groups, coverage and
links. Commit-time triggers then check the whole snapshot. Any failure rolls back all of
it, raw evidence included.

## 3. What PostgreSQL enforces and what Python enforces

**PostgreSQL** (each rule has a rejection test using real logins):

- shapes: enums, CHECKs tying every typed column to its payload, status rules,
  building-scope rules, BuildingID-basis rules, approved registration labels, HPD role
  mapping, private keys refused in every public JSONB value;
- references: run → fetch → pages, records, groups, coverage; record → links → raw row;
  same-run D9 building link; link source id equals its raw row's source id;
- whole-snapshot rules at commit: link counts equal source counts, payload lineage
  equals stored links, complaint groups equal their problems, contact coverage and
  counts agree, D9 report counts equal the stored records;
- sealed snapshots: rows can be added only in the transaction that created the fetch;
- raw evidence: tombstone shape, retention at most 180 days after the fetch, no row
  stored already due or fetched in the future, and (0010) only two allowed updates —
  expiry of a due row and restore through the function with a copy fetched after the
  expiry;
- coverage IDs distinct and canonical (0008, 0009).

**Python** (Pydantic on save and on load, and the storage layer):

- semantic rules spanning nested JSON (mixed complaints, association wording, tax-lot
  rules), re-validated on every load;
- **digest integrity**: every raw row is verified under the HMAC key before writing, and
  re-verified by `PostgresEvidenceReader` on every authorized read. The database never
  holds the key, so it cannot check a row against its digest. This is a stated limit,
  not a gap to close by copying the key into SQL (it would then sit in backups);
- the repeat-sighting rule's Python side (a stale copy is left as a tombstone and
  counted, not failed);
- error scrubbing and logging rules (section 4).

## 4. Privacy and role guarantees

| Login role | Can read | Can write |
|---|---|---|
| `app_reader` | `records` tables and `evidence_availability` | nothing |
| `ingest_writer` | `records`, `evidence_identity` | `INSERT` on `records` and `evidence.raw_row` |
| `evidence_restorer` | nothing | `EXECUTE` on the restore function only |
| `evidence_retention` | three non-content columns of `raw_row` | the three expiry columns, limited by 0010 |
| `evidence_admin` | `evidence` | nothing |

Step 6 full-schema audit (`tests/db/test_privacy_audit.py`, 8 tests): one snapshot with
all five result types, D9, coverage and groups carries distinct sentinels for unit
detail, contact person names and titles, business street, number and suite, the typed
address, the 311 request number, and credentials (the digest key and a database
password).

- Every column `app_reader`, `ingest_writer`, `evidence_retention` and
  `evidence_restorer` can select, in every schema, is read **as that login**, and each
  value is walked recursively (JSON keys and values, arrays, scalars). Findings: none.
  `app_reader` read more than 2,000 values across exactly eight relations.
- `app_reader`'s privileges, enumerated from the catalog: `SELECT` only, only in
  `records`, never `evidence_identity`; no column on any `evidence` relation; no
  `EXECUTE` on any project function; one `SECURITY DEFINER` function in total (restore,
  restorer only); no `CREATE` anywhere; no role memberships; other sessions' query text
  shows `<insufficient privilege>`. Direct attempts are denied.
- Positive controls: unit, person, business and 311 sentinels are in `evidence.raw_row`
  for `evidence_admin`; the typed address and credentials are stored nowhere at all.
  Corporate names, HPD roles, withheld-field markers, `has_311_reference` flags and
  provenance stay public.
- Errors, chained exceptions, formatted tracebacks and every log record (with
  SQLAlchemy's engine logger forced to DEBUG) contain no sentinel.

**Found and fixed in step 6:**

1. With the `sqlalchemy.engine` logger at DEBUG, SQLAlchemy logged result rows, which
   `hide_parameters` does not cover; an `evidence_admin` read logged raw rows. The engine
   logger now drops its DEBUG records (884060d).
2. Scrubbed write errors were raised inside the handler, so `__context__` still held the
   database error whose DETAIL quotes the failing row. They are now raised outside it
   (884060d, e1f34e8).
3. `evidence_retention` could expire undue rows, overwrite held evidence, resurrect a
   tombstone with invented content and rewrite expiry times: all four committed as a real
   login before 0010; all four are refused now (32b6c02).

## 5. Retention policy and operations

- **Window:** `retain_until = fetched_at + EVIDENCE_RETENTION_DAYS` (1 to 180, default
  180), both from the fetch whose copy is held; the database refuses longer.
- **Expiry** is a tombstone: original row and plain hash removed; digest, dataset,
  source id, basis, redacted hash, query URL, fetch time and restore history kept.
- **Repeat sightings** of a held digest do **not** extend retention. Reviewed and kept
  (design 10.4c gives the trade-off); not changed. Step 6 closed a gap in it: a copy
  fetched while the row was still held could restore the tombstone after expiry and
  restart the window. Now only a copy fetched after the expiry restores it, enforced in
  SQL and handled in Python without failing the snapshot.
- **Restoration** keeps the step 2 boundary: only `evidence_restorer` may call the
  function; Python verifies the digest first; the reader re-verifies.
- **The job:** `storage/retention.py` and `scripts/run_retention.py`. A login holding only
  `evidence_retention`; one cutoff per run (database clock, or an injected clock not
  ahead of it); bounded batches, each committed separately; `FOR UPDATE SKIP LOCKED`;
  lock and statement timeouts; counts-only report; exit codes 0 complete, 3 incomplete,
  1 failed, 2 not configured; idempotent and safe to retry.
- **Operations:** the runbook covers a daily schedule, alerts (failure, two incomplete
  runs, no success in 36 hours, an independent overdue-evidence query), failure
  handling, and the restore procedure. **No job is deployed or running.**

## 6. Backups, PITR and logs: limits

- Expiry removes originals from the primary only. Backups, WAL archives, snapshots,
  replicas and dumps taken earlier still hold them. The practical bound is **180 days +
  backup/PITR retention + one scheduling interval**; that retention is a hosting setting
  not yet chosen.
- Required at deployment: encrypted backups and archives, restore rights limited to
  named operators with every restore recorded, bounded retention stated in the privacy
  notice, no ad hoc evidence dumps, and **an immediate retention run after any restore**
  (retention dates are absolute, so nothing restarts).
- Server logs are not sanitized by the application. Measured on the local cluster:
  default `log_error_verbosity` logged a raw row in DETAIL on a failed insert; `terse`
  did not; `log_statement = all` logged bound values unless
  `log_parameter_max_length = 0`. Required settings are in the runbook. Tests prove
  that the per-login `terse` setting takes effect and that bound values never reach
  statement text; they do not prove what a deployed server logs.

## 7. Acceptance matrix (run 2026-10-23 at 7cc5bed plus docs)

| Check | Result |
|---|---|
| Offline suite, no database | 508 passed, 216 skipped (the live and database tests) |
| PostgreSQL 16 required (`REQUIRE_DB_TESTS=1`, local) | 701 passed, 23 skipped (live only); 203 db tests collected |
| CI run 39880336682 (7cc5bed) | Offline job and PostgreSQL 16 job green; the db job fails if no db test runs or any is skipped |
| Live tests (`RUN_LIVE_TESTS=1 -m live`) | 23 passed |
| Alembic CLI on a disposable database | base → 0010 → base → 0010; `current` = 0010 (head); no leftover test databases or roles |
| Migration test in the suite | up, down, up; head 0010 |
| Grant and boundary tests | pass (`test_roles`, `test_evidence`, `test_evidence_hardening`, `test_privacy_audit`, `test_raw_row_lifecycle`) |
| Privacy scans | pass (step 3 to 5 scans and the step 6 full audit) |
| Retention and restore | 17 job tests and 5 lifecycle/restore tests pass |
| Atomic rollback, idempotency, round trips | pass (existing step 2 to 5 tests, mid-run job failure, resave tests) |
| Historical snapshots after expiry | 2 tests pass; records schema byte-identical before and after the job |
| Lint and format | clean |
| Phase 1 benchmark | unchanged: 14/14 correct, 0 wrong building, 6/6 ambiguity, 4/4 abstentions, 2/2 unresolved BINs |
| Five recorded addresses | unchanged: admin counts and associations as in the step 5 report; complaint problems 912 / 105 / 51 / 10 / 2,653; `hpd_task1_results.json` and `hpd_task2_results.json` regenerate byte-identical |
| D9 at 220 W 17th | 87 violations and 84 complaint problems moved, all to `other_building_on_lot`, none to the requested BIN 1081543; unchanged after expiry |

Step 6 tests: `tests/db/test_raw_row_lifecycle.py` (5), `test_retention_job.py` (17),
`test_privacy_audit.py` (8), `test_snapshot_after_expiry.py` (2), with the shared
synthetic snapshot in `tests/db/full_snapshot.py`.

## 8. Limitations

- PostgreSQL cannot verify a raw row against its keyed digest; Python prevents and the
  reader detects. Logins holding `ingest_writer` or `evidence_restorer` are trusted.
- The owner and superusers can bypass every trigger; the guarantees are for the
  application roles.
- Retention depends on a scheduler that does not exist yet; until then nothing expires.
- Backups and server logs are outside the application's control (section 6).
- Test time travel uses a superuser connection with triggers disabled for one
  statement; CHECK constraints still apply.
- Concurrency is tested with four threads on one machine, not under production load.
- Only HPD datasets are stored. DOB and 311 are not integrated; when 311 arrives, its
  request number is a source id that `evidence_identity` already keeps from
  `app_reader`.
- The independent HPD Online validations remain PENDING.

## 9. Reproducing

```
cd backend
uv sync
uv run ruff format --check . && uv run ruff check .
uv run pytest -q                                   # offline
TEST_DATABASE_ADMIN_URL=postgresql://postgres:<pw>@localhost:5432/postgres \
  REQUIRE_DB_TESTS=1 uv run pytest -q              # with PostgreSQL 16
RUN_LIVE_TESTS=1 uv run pytest -q -m live          # live NYC APIs
uv run python scripts/evaluate_resolver.py         # Phase 1 benchmark
DATABASE_URL=.../scratch uv run alembic upgrade head && uv run alembic downgrade base \
  && uv run alembic upgrade head
```

`docker compose up -d postgres` provides a local PostgreSQL 16. No secret is needed for
the offline suite; tests use invented keys and passwords.

## 10. Interview decisions

1. **Two schemas and five roles instead of one app user.** The web app's login can never
   reach raw rows, even through views or functions; the audit enumerates that surface.
2. **Typed columns plus JSONB.** Typed columns carry what SQL must constrain; JSONB keeps
   the exact public model, so loads are equal, and CHECKs tie the two together.
3. **Constraints in the database, semantics in Pydantic.** Each rule lives where it can
   be enforced; whole-snapshot rules run as deferred commit-time triggers.
4. **Content-addressed raw evidence with tombstones.** Shared rows are stored once;
   expiry keeps lineage while removing content and the brute-forceable plain hash.
5. **No HMAC key in PostgreSQL.** A trusted restore role plus verification in Python and
   on read, rather than a key that would live in every backup.
6. **Least privilege beyond grants.** Column grants could not express "only to NULL, only
   when due", so a lifecycle trigger does; found by attacking the role as a real login.
7. **Retention job design.** Bounded batches, skip-locked concurrency, one cutoff,
   counts-only reporting, and refusing over-privileged logins.
8. **Repeat sightings do not extend retention.** The strict reading of the 180-day rule,
   now enforced on restore too; changing it is a policy decision, not a code fix.
9. **Honest limits.** Backups, PITR and server logs are documented as deployment
   requirements with measured behaviour, not claimed as solved by application code.
