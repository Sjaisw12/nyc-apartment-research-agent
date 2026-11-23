# Phase 2, Task 4: Step 2 hardening and Step 3 report

| Commit | Content |
|---|---|
| `a5a4f8a` | Step 2 hardening: migration 0003, trusted restore boundary, retention window |
| `bd7956e` | Step 3: migration 0004, violation-result persistence and loading |

Both are on `claude/project-thread-5d0l0f`. Nothing has been merged or deployed. Step 4
(complaints) has not started. The Task 1 and Task 2 HPD Online validations remain
**PENDING**.

## Part 1: Step 2 security hardening

### Finding 1: the restore function cannot verify digest integrity

Tested with real logins against 0002:
- An `ingest_writer` login called `evidence.restore_expired_raw_row()` directly, with the
  tombstone's digest, dataset, source ID, basis and redacted hash, but a forged
  `original_row` (`apartment = FORGED-UNIT`) and the original's plain hash.
- **The forged row was stored.** The function has no HMAC key. The plain hash is supplied
  by the caller, and a tombstone keeps none to compare it with.
- `PostgresEvidenceReader.get()` then refused the row with `EvidenceIntegrityError`.
- The same is true of a plain `INSERT` by `ingest_writer` under an absent digest.

So the SQL function is **not** described as enforcing digest integrity.

Safeguard (migration 0003):
- `EXECUTE` on the restore function moved from `ingest_writer` to a new NOLOGIN role,
  `evidence_restorer`. That role has no table rights at all. Only the server-side writer's
  login is given it, alongside `ingest_writer`. A login holding `ingest_writer` alone is
  now denied.
- The HMAC key is **not** copied into the database. That was considered and rejected: it
  would put the key in backups and within reach of the owner role.

**The resulting guarantee, stated exactly:**
- PostgreSQL never checks that a raw row matches its keyed digest, on insert or on
  restore.
- Prevention is in Python: the writer verifies every row under the key before any SQL
  runs.
- Detection is on read: the reader re-verifies every row and refuses one that fails.
- Logins holding `ingest_writer` or `evidence_restorer` are trusted to write only
  verified rows. Restore additionally requires the separate role.

### Finding 2: restoring with an old source timestamp

Tested against 0002, with a row first fetched 2026-01-01 and expired, then restored from
a copy fetched 2026-02-01:
- The restore succeeded with `retain_until = 2026-07-31`, **already in the past**.
- `fetched_at` still showed the first fetch (2026-01-01), so `retain_until` no longer
  equalled `fetched_at` + 180 days.
- The next expiry run removed the row again at once.

Fixed in 0003:
- A restore now records the restoring fetch: `fetched_at`, `query_url`, `data_as_of`.
  `first_seen_at` keeps the first storage time.
- `CHECK (retain_until <= fetched_at + interval '180 days')`. `EVIDENCE_RETENTION_DAYS`
  is limited to 1 to 180.
- A trigger refuses any insert or restore whose `retain_until` is not in the future, or
  whose `fetched_at` is more than 5 minutes ahead.
- The writer applies the same rule first (`EvidenceRetentionError`). A stale item fails
  the whole save; it is never stored and then expired at once.

Retention semantics now: raw evidence is held at most 180 days after the fetch it came
from, plus the gap until the next expiry run (scheduling is step 6).

**12 tests** in `tests/db/test_evidence_hardening.py` cover both findings. Existing step 2
tests now use a clock-relative fetch time, because the database refuses rows already
past retention. CI run 39724362394 for `a5a4f8a` is green on both jobs.

## Part 2: Step 3, source-result persistence

### Tables (migration 0004)

All owned by `nyc_owner`. `app_reader` has SELECT; `ingest_writer` has SELECT and
INSERT. Nobody has UPDATE or DELETE, so snapshots are append-only.

| Table | One row per |
|---|---|
| `records.retrieval_run` | research request: keyed digest of the typed address, resolver outcome, level, BBL, BIN, whitelisted projection |
| `records.source_fetch` | source result: typed status, scope, counts, provenance; `result_payload` without records and pages |
| `records.fetch_page` | HTTP response (count call is index -1) |
| `records.normalized_record` | public record; `payload` is the full public model |
| `records.record_source_row` | lineage link from a record to its raw row in `evidence.raw_row` |

### Database constraints (each has a rejection test)

**CHECK constraints:**
- `records_found`: not partial, no incomplete reasons, `source_count > 0`, at least one
  record.
- `no_records_found`: not partial, no reasons, `source_count = 0`, `rows_fetched = 0`, no
  records.
- `incomplete_pagination`: partial, with at least one reason.
- `source_unavailable` and `unsupported_join`: not partial, no records or links.
  `unsupported_join` also has no pages and no scope.
- `resolved_bin` only at building scope, on runs and on fetches.
- Links never exceed `rows_fetched`.
- Enumerations for status, scope, attribution, basis, record kind, evidence kind,
  location scope and digest basis.
- Formats: BBL, BIN, dataset ID, hex digests, Socrata page URLs.
- D9: `hpd_building_id` requires `original_attribution = unattributed_on_lot`, a mapped
  BIN, a confirmed lot, and a building-level attribution. `row_identifiers` requires no
  `original_attribution` and no `mapped_bin`.
- Each typed column equals the payload's own value, on fetches and on records.
- `apartment`, `story`, `complaint_anonymous_flag`, `unique_key` and `input` keys are
  refused anywhere inside any JSONB value.
- The resolver projection may only hold whitelisted keys.

**Foreign keys:** run → fetch → page and record; (record, fetch) → link; link →
`evidence.raw_row`.

**Row triggers:**
- A building-scope fetch needs a building-level run, with matching BBL and BIN.
- Records only under `records_found` or `incomplete_pagination`.
- Building attributions and BuildingID upgrades only at building scope.
- A record's lot, dataset and kind must match its fetch.
- **Sealed snapshots:** pages, records and links can only be added in the transaction
  that created the fetch.

**Commit-time check** (a deferred constraint trigger):
- Stored record, page and link counts equal the rows present.
- Every record's first link is its own raw row, and positions have no gaps.
- On `records_found`, links equal `source_count`, or are fewer only when identical
  duplicates were collapsed.
- Each raw row's basis equals the record's `digest_basis`.

**Pydantic:** every result is re-validated before saving (so `model_copy()` cannot skip
validators) and again after loading.

### Round-trip results

Every loaded `SourceResult` equals the saved one (`==` on the models):
- synthetic `records_found` with this-building, other-building and unattributed rows;
- `no_records_found`, `incomplete_pagination`, `source_unavailable`, `unsupported_join`;
- tax-lot scope, which stays `lot_level` with no BIN;
- **all five recorded addresses, before and after D9.** The 87 upgraded rows at 220 W
  17th reload with `attribution_basis = hpd_building_id`, `original_attribution =
  unattributed_on_lot` and their mapped BIN. The typed columns show the same 87. The D9
  report itself round-trips.

Each loaded result also returns the state of every cited raw row: basis, held or expired,
`retain_until`, `expired_at`. Recorded data reloads with both bases present
(`sanitized_row` for fixture rows that carried redaction markers, `original_row` for
untouched rows), each matching its record.

### Rollback tests

- **A real CHECK violation:** the last record's public hash is malformed, which Pydantic
  accepts and SQL rejects. Raw rows, the run, the fetch, pages and four records were
  written first. Afterwards every table, `evidence.raw_row` included, has **zero rows**.
  The error carries no row content and is not chained.
- **At commit:** records inserted without their links. The commit fails on "stored
  counts do not match", and nothing remains.
- A raw-row basis that differs from the record fails at commit.

### Idempotency and snapshots

- Re-saving the same result in the same run returns the same fetch and writes nothing.
- A new retrieval of the same rows is a new snapshot with its own page times. The raw
  rows are shared and stored once.
- The same result under another run is that run's own snapshot.
- The before-D9 and after-D9 versions are separate snapshots.

### Privacy findings

- A sentinel test saves a result whose rows carry apartment and story sentinels and a
  description with a unit ID. The resolver input, explanation and provenance carry an
  address sentinel. Logged in as `app_reader`, every row of every readable table and view
  in `records` and `public` was scanned as text. **No sentinel and no forbidden key was
  found.** The unit detail exists only in `evidence.raw_row`, readable by
  `evidence_admin`.
- The typed address is stored only as `input_digest`, an HMAC of its normalized form.
  Two spellings of the same address give the same digest.
- Every stored page URL and record `query_url` has a `$where` of the form `bbl='…' OR
  (boroid=… AND block=… AND lot=…)`, built only from validated integers.
- No sentinel appeared in captured logs, on success or on failure.

### Permissions (real login roles)

- `app_reader` can load results but cannot save, update or delete.
- `ingest_writer` cannot update or delete any of the five tables.
- `evidence_admin`, `evidence_retention` and `evidence_restorer` cannot read `records`
  tables.
- No new `SECURITY DEFINER` function was added. The four helper functions that CHECKs
  and triggers call are executable by `ingest_writer` only.

### Test outcomes (local, PostgreSQL 16)

| Check | Result |
|---|---|
| Offline suite, no database | 490 passed, 106 skipped (23 live, 83 db) |
| Database required | 573 passed, 23 skipped (live only); 83 db tests |
| Live tests | 23 passed |
| Lint and format | Clean |
| Phase 1 benchmark | Unchanged: 14/14 correct auto-matches, 0 wrong building, 6/6 ambiguity, 4/4 abstentions, 2/2 unresolved BINs |
| Alembic CLI | base → 0004 → base → 0004 clean; no leftover test databases or roles |

CI run 39725581091 for `bd7956e` is green on both jobs (offline job; `postgres` job with `REQUIRE_DB_TESTS=1` and Alembic CLI up, down, up).

### Files changed

- `backend/migrations/versions/0003_evidence_restore_boundary.py` (new)
- `backend/migrations/versions/0004_source_results.py` (new)
- `backend/src/nyc_apartment_agent/storage/repository.py` (new)
- `backend/src/nyc_apartment_agent/storage/evidence_pg.py`: restore parameters,
  retention check
- `backend/src/nyc_apartment_agent/storage/db.py`: `evidence_restorer` role
- `backend/src/nyc_apartment_agent/config.py`: retention limited to 1 to 180 days
- `backend/tests/db/test_evidence_hardening.py`, `test_repository.py` (new)
- `backend/tests/db/conftest.py`: multi-role logins, shared `db` fixture
- `backend/tests/db/test_evidence.py`, `test_migrations.py`: updated for 0003 and 0004
- `docs/phase2_task4_postgres_design.md`: sections 4, 10.2a, 10.4a, 10.4b

## Remaining limitations

- **Digest integrity is not enforced by SQL** (Part 1). It is enforced in Python and
  detected on read.
- **Collapsed duplicates.** The model does not record how many identical copies were
  collapsed, so the link rule can only require "fewer than `source_count`" in that case.
- **Concurrent identical saves.** The second one fails on the unique idempotency key and
  rolls back. The caller must retry. It is never silently merged.
- **Server log.** PostgreSQL's DETAIL can quote failing public rows in the server log.
  Python-side errors are scrubbed. `log_error_verbosity = terse` remains a deployment
  setting.
- **Only violations are persisted.** Complaints (step 4) and buildings, registrations
  and contacts (step 5) are not, though the table enumerations already allow them.
- **Not built yet:** the full-schema privacy scan across all result types, the scheduled
  retention job, and backup handling. These come in step 6.
