# Phase 2, Task 4 design: PostgreSQL storage for evidence and normalized records

Status: **approved 2026-10-22 (D13 to D17, all yes), revised with the six required
corrections (section 10).** Implementation proceeds in separate, reviewable steps
(section 8). Step 1 is the only step authorized to start now.

## 1. Goal and scope

Task 4 persists what Tasks 1 to 3 produce, so later phases (agent, API, report) can read
stored, cited records instead of re-fetching on every request. The stored data must keep
every guarantee the in-memory models already enforce.

**In scope:**
- schema and migrations;
- a PostgreSQL evidence store (replacing the git-ignored `.evidence/` directory);
- persistence of source results: status, provenance, freshness, records and summaries;
- round-trip loading back into the same validated Pydantic models;
- database constraints, foreign keys and triggers for the invariants SQL can express;
- least-privilege roles;
- retention;
- local and CI test setup.

**Out of scope:** API endpoints, caching or refresh policy, the agent, DOB and 311
sources (Task 5), hosting and deployment.

## 2. Principles

1. **Statuses are stored as returned.** `no_records_found` always means a complete
   retrieval with a confirmed source count of 0.
2. **Append-only snapshots (D14).** Every retrieval is a new snapshot with its own
   provenance and freshness. Historical source results are never overwritten.
3. **Building-level attribution needs building scope.** It may only exist under a
   building-scope fetch. BuildingID upgrades keep `original_attribution` and
   `attribution_basis`.
4. **Private evidence (D15).** Raw rows live in a separate schema that the application's
   read role cannot reach.
5. **Every source row is traceable.** Every normalized record links to all of its source
   rows, and source counts and normalized counts are stored separately.
6. **Two layers of checks** (correction 2). The database enforces what SQL can express.
   Everything else is validated by the Pydantic models before saving and again after
   loading.

## 3. Schema

The schemas are `records` (readable by the application) and `evidence` (private). All
schemas, tables, views and functions are owned by the NOLOGIN role `nyc_owner`
(section 4).

### 3.1 `records.retrieval_run`
One row per research request.

| column | notes |
|---|---|
| `id` | uuid primary key |
| `created_at` | |
| `input_digest` | HMAC of the normalized address input (D16). The free text is never stored. |
| `resolution_outcome`, `resolution_level` | enums |
| `resolved_bbl`, `resolved_bin` | `resolved_bin` only set at building level |
| `resolution` | jsonb: a **whitelisted projection** of the resolver result (identifiers, outcome, level, BIN evidence, candidate BBL/BIN lists, reason codes). The parsed input, the raw input and any address text are excluded. |

### 3.2 `records.source_fetch`
One row per source result: violations, complaints, buildings, registrations or contacts.

| column | notes |
|---|---|
| `id` | uuid |
| `run_id` | FK to `retrieval_run` |
| `idempotency_key` | unique: SHA-256 of run id, dataset, and page URLs with fetch times |
| `source`, `dataset_id`, `result_type` | `result_type` is an enum of the five result models |
| `status` | enum |
| `status_detail` | |
| `partial` | bool |
| `join_scope` | `building` / `tax_lot` / null |
| `resolved_bbl`, `resolved_bin` | |
| `source_count`, `rows_fetched` | |
| `incomplete_reasons`, `warnings` | text[] |
| `data_as_of` | truth header |
| `data_updated_at`, `metadata_updated_at`, `stated_update_frequency`, `stale` | freshness |
| `result_payload` | jsonb: the full public result model **without** its record lists (summary, provenance, freshness, batch and coverage provenance, D9 report) |
| `fetched_at` | |

`where_clause`, `order_by`, `page_size` and `row_cap` are typed copies of provenance
fields, kept for querying.

### 3.3 `records.fetch_page`
One row per HTTP response, with primary key (`fetch_id`, `seq`):
- `batch` (contacts and coverage batches);
- page `index` (-1 is the count call);
- `url`, `rows`, `fetched_at`, `truth_last_modified`.

### 3.4 `records.normalized_record`
One row per normalized record.

- **Identity:** `id`, `fetch_id`, `record_kind`, `evidence_kind`, `dataset_id`,
  `source_record_id`, `source_row_id`.
- **Attribution:** `resolved_bbl`, `lot_membership`, `attribution`, `attribution_basis`,
  `original_attribution`, `mapped_bin`, `location_scope`, `quality_flags` text[].
- **Integrity and payload:** `digest_basis`, `original_digest`, `redacted_sha256`,
  `payload` jsonb (the full public model, dumped by alias), `schema_version`, `ordinal`
  (keeps record order for round-trip).

There is a unique (`id`, `fetch_id`), so child tables can carry a composite foreign key.

### 3.5 `records.record_source_row`
Columns: `record_id`, `fetch_id`, `position`, `source_row_id`, `original_digest`.
- composite FK (`record_id`, `fetch_id`) to `normalized_record`;
- FK `original_digest` to `evidence.raw_row`;
- unique (`fetch_id`, `source_row_id`) and (`record_id`, `position`).

As built (0004, changed by 0006): the per-fetch unique (`fetch_id`, `original_digest`)
was dropped in step 5, because a collapsed registration or contact in an incomplete
fetch can list one repeated source row twice. Uniqueness of linked rows is checked at
commit for `records_found` fetches instead (section 10.2c).

### 3.6 `records.complaint_group`
Columns: `fetch_id`, `complaint_id`, `attribution` (including `mixed`),
`problem_attributions` jsonb, `payload` jsonb, `ordinal`. Unique (`fetch_id`,
`complaint_id`).

### 3.7 `evidence.raw_row` (private)

| column | notes |
|---|---|
| `original_digest` | primary key |
| `dataset_id`, `source_record_id` | |
| `basis` | `original_row` / `sanitized_row` |
| `original_row` | jsonb, nullable after expiry |
| `original_sha256` | private plain hash, nullable after expiry |
| `redacted_sha256`, `redacted_fields` | |
| `query_url`, `fetched_at`, `data_as_of` | |
| `first_seen_at` | (step 2) Replaces the planned `first_seen_fetch_id`. Fetches do not exist in step 2, and a foreign key from raw rows to fetches would invert the save order. Lineage is fetch → record → source-row link → raw row. |
| `retain_until` | |
| `expired_at` | |

A CHECK requires that `original_row` and `original_sha256` are both null exactly when
`expired_at` is set.

### 3.8 `records.evidence_availability` (view)
Columns: `original_digest`, `basis`, `held` (bool), `retain_until`, `expired_at`. The view
is owned by `nyc_owner`, so `app_reader` can see whether an original is still held
without any privilege on `evidence`. It exposes no row content and no plain hash.

### 3.9 Typed columns plus JSONB (D13)
Typed columns carry everything we filter, join or constrain on. JSONB keeps the complete
public models, so loading reconstructs exactly what was produced.

## 4. Roles, ownership and grants (D15)

All group roles are NOLOGIN. Deployments grant them to login users.

| role | privileges |
|---|---|
| `nyc_owner` | owns the schemas and every object in them; migrations run `SET ROLE nyc_owner` |
| `app_reader` | `USAGE` on `records`; `SELECT` on `records` tables and views. **No** `USAGE` on `evidence`. |
| `ingest_writer` | `USAGE` on both schemas; `SELECT, INSERT` on `records`; `INSERT` on `evidence.raw_row`. No `UPDATE` or `DELETE` anywhere. |
| `evidence_admin` | `USAGE` and `SELECT` on `evidence` (audits and validation only) |
| `evidence_retention` | `USAGE` on `evidence`; `SELECT, UPDATE` on `evidence.raw_row` (the expiry job only) |
| `evidence_restorer` | `USAGE` on `evidence`; `EXECUTE` on `evidence.restore_expired_raw_row` only. Granted with `ingest_writer` to the server-side writer (migration 0003). |

**Hardening:**
- `REVOKE ALL ON SCHEMA evidence FROM PUBLIC`.
- `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
- Default privileges `FOR ROLE nyc_owner`:
  - revoke `EXECUTE` on functions from `PUBLIC`, in both schemas;
  - grant the table privileges above on future tables.
- **No `SECURITY DEFINER` functions, with one reviewed exception (step 2):**
  `evidence.restore_expired_raw_row`. It has a pinned `search_path`, `EXECUTE` is
  granted to `evidence_restorer` only (since migration 0003; `ingest_writer` in 0002),
  and a test allow-lists it by name.

The roles are cluster-wide, so a migration downgrade drops the schemas but keeps the
roles (they may serve other databases). This is documented and tested.

## 5. Code shape (D17)

- **Database access:** SQLAlchemy Core 2.x (no ORM), psycopg 3 and Alembic.
- **Engine settings:** created with `hide_parameters=True` and `echo=False`, so SQL
  parameters (raw rows, digests) never appear in logs or exception messages.
- **Connection URL:** `DATABASE_URL` comes from the environment and is a secret. It is
  never logged.
- **Evidence buffer:** `storage/evidence_buffer.py` holds `BufferedEvidenceStore`, which
  implements the existing `EvidenceStore` protocol. Adapters `put` into memory; nothing
  touches the database during retrieval.
- **Repository:** `storage/repository.py` has three functions.
  - `save_run(...)`.
  - `save_result(run_id, result, evidence_buffer)`. It validates the model first, then
    writes everything in **one transaction** (section 10.1).
  - `load_result(fetch_id)`. It rebuilds the model and validates it again.

## 6. Testing

- **Offline suite:** unchanged and runnable with no database. Database tests are marked
  `db` and skip with a clear reason when no database is configured.
- **CI:** a separate `postgres` job uses a PostgreSQL 16 service container and sets
  `REQUIRE_DB_TESTS=1`. With that set, a missing or unreachable database is a test
  **failure**, not a skip. The job also fails if zero `db` tests ran.
- **Disposable databases:** each test session creates `nyc_test_<random>` and drops it
  afterwards. The migration test uses its own database and runs up, down and up.
- **Role tests:** they use real login roles, not just `SET ROLE` (section 10.3).

## 7. Retention summary (D15)

Raw evidence is kept for 180 days. Section 10.4 covers expiry, lineage and backups.

## 8. Implementation steps (separate commits, each reported)

1. **Infrastructure (now):**
   - dependencies;
   - Alembic skeleton;
   - migration 0001 (roles, schemas, ownership, grants, default privileges);
   - local PostgreSQL via `docker compose`;
   - CI service job;
   - migration up/down/up test;
   - schema, role and privilege tests.
2. `evidence.raw_row`, `evidence_availability`, the buffered evidence store, and
   atomic, idempotent evidence writes.
3. Runs, fetches, pages, normalized records and source-row links for violations. Covers
   constraints, triggers, round-trip, rollback and idempotency tests.
4. Complaints and complaint groups.
5. Buildings, registrations, contacts (with coverage), and D9 reports.
6. The full-schema privacy scan, the retention job and expiry tests, and the final Task 4
   report.

## 9. Acceptance criteria

1. Migrations apply, roll back and re-apply in CI on PostgreSQL 16.
2. All five result types, complaint groups, contact coverage, source-row references and
   D9 reports round-trip into equal, validated models.
3. Every SQL-enforceable invariant has a constraint, FK or trigger, each with a rejection
   test. The rest is validated in Pydantic on save and on load.
4. `app_reader` cannot read raw evidence through any path, tested with real roles.
5. The privacy scan covers every readable column and finds nothing.
6. Retention expires originals without breaking lineage.
7. The offline suite, live tests, benchmark and CI all pass, with counts reported as run.

## 10. Required corrections (2026-10-22) and how the design meets them

### 10.1 Transaction atomicity

`save_result` runs on one connection, in one transaction, as a login holding
`ingest_writer`. The order is:
1. insert raw rows (`ON CONFLICT (original_digest) DO NOTHING`);
2. insert the fetch;
3. insert pages, records, source-row links and complaint groups;
4. commit.

Any error rolls back all of it, so a failed public-record insert leaves no new evidence
rows and no partial links.

Raw rows written by an earlier, committed snapshot may be referenced; they are not
modified.

**Idempotency:**
- `source_fetch.idempotency_key` is unique. Re-saving the same result returns the
  existing fetch id and writes nothing.
- Raw rows are content-addressed by their keyed digest.

**Tests:**
- Inject a failure after the evidence insert (an invalid record that violates a
  constraint). Afterwards, there must be zero new rows in every table.
- Save the same result twice: one copy only.
- Two snapshots sharing raw rows: rows are stored once and referenced by both.

### 10.2 Database invariants

**SQL-enforced (each with a rejection test):**

- Enum types for status, scope, attribution, basis, record kind, evidence kind and
  digest basis.
- CHECK constraints on `source_fetch`:
  - `records_found` → `partial = false`, no incomplete reasons, `source_count > 0`;
  - `no_records_found` → `partial = false`, no incomplete reasons, `source_count = 0`,
    `rows_fetched = 0`;
  - `incomplete_pagination` → `partial = true` and at least one reason;
  - `source_unavailable` and `unsupported_join` → `partial = false`;
  - non-building scope → `resolved_bin is null`.
- CHECK constraints on `normalized_record`:
  - `attribution_basis = 'hpd_building_id'` → `original_attribution =
    'unattributed_on_lot'`, `mapped_bin is not null`, `lot_membership = 'confirmed'`;
  - basis `row_identifiers` → `original_attribution` and `mapped_bin` are null.
- Foreign keys: run → fetch → page, record, group; record → source row; source row →
  raw row.
- Unique constraints: idempotency key; (`fetch_id`, `source_row_id`); (`record_id`,
  `position`); (`fetch_id`, `complaint_id`).
- Triggers:
  - **row-level:** a record or group may not be added to a fetch whose status is
    `no_records_found`, `source_unavailable` or `unsupported_join`;
  - **row-level:** building attributions (`this_building`, `other_building_on_lot`) only
    under a fetch with building scope;
  - **deferred constraint trigger, checked at commit:**
    - a `records_found` fetch has at least one record;
    - on a complete fetch, its source-row links equal `source_count`;
    - each record's position-0 link is its own source row.

**Pydantic-only, checked on save and on load:** semantic rules that span JSON
structure. Examples:
- a mixed complaint really spans buckets;
- a contact association agrees with coverage;
- no address detail on a contact that names a person;
- a requested building is never named at tax-lot scope.

`load_result` runs the full model validation, so a stored row that breaks a rule fails
loudly on read.

### 10.2a Step 3 decisions (migration 0004, 2026-10-22)

Where step 3 differs from the plan above, and why:

- **Idempotency key** = SHA-256 of run id, result type and a hash of the full result
  content, not of page URLs alone. A D9-upgraded result has the same pages as the
  original but different records; a page-only key would have treated it as a duplicate.
- **Source-row uniqueness** is (`fetch_id`, `original_digest`). Violation rows carry no
  Socrata row id, and two different-content copies of one `violationid` share their ID,
  so the digest is the row identity. `source_row_id` stays nullable for step 5.
- **Link count rule.** On a `records_found` fetch, links equal `source_count`, except
  when identical duplicate rows were collapsed (flag `duplicate_id_same_content`); then
  there must be fewer. The model does not record how many copies were collapsed.
- **No duplication between payload and child tables.** `result_payload` excludes
  `records` and `provenance.pages`; those live only in `normalized_record` and
  `fetch_page`. CHECK constraints tie each typed column to the payload's own value.
- **Sealed snapshots.** `source_fetch.created_txid` is set by trigger; pages, records and
  links may only be added in the transaction that created the fetch.
- **Helper functions** used inside CHECKs and triggers run with the writer's rights,
  so `ingest_writer` holds `EXECUTE` on those four functions (pure, or reading only
  `records.source_fetch`). No `SECURITY DEFINER` was added.

### 10.2b Step 4 decisions (migration 0005, 2026-10-22)

- **Problems are records, complaints are groups.** Each problem row is a
  `normalized_record` (`record_kind = complaint_problem`) with a typed `complaint_id`.
  Each complaint is one `records.complaint_group` row. A foreign key (`fetch_id`,
  `complaint_id`) means no problem exists without its complaint.
- **Group attribution is derived, and checked at commit.** A group's problem count,
  duplicate count, `problem_ids`, per-bucket counts and attribution (`mixed` exactly
  when its problems span more than one bucket) must equal what its problem records say.
  A group can never claim a building its problems do not support.
- **Withheld-field counts.** `summary.withheld_field_counts` is keyed by the names
  `complaint_anonymous_flag` and `unique_key`, with numeric counts. The private-key
  CHECK skips that one object and requires its values to be numbers. The values
  themselves exist only in `evidence.raw_row`.
- **Additive migration.** 0005 adds a table, a column, constraints and triggers; the
  only change to 0004 objects is replacing the result-payload privacy CHECK as above.

### 10.2c Step 5 decisions (migrations 0006 and 0007, 2026-10-22)

- **Three counts, kept apart.** Fetched source rows (`rows_fetched`, `source_count`),
  normalized records (`record_count`) and stored links (`source_row_count`). Identical
  registration or contact rows under different Socrata `:id`s are one record with one
  link per `:id`, so links can exceed records. `source_row_count` now counts links.
- **Lineage equals the model (0006).** At commit, a record's payload `source_rows` must
  equal its links in position order, by (`source_row_id`, `original_digest`). Records
  without `source_rows` have exactly one plain link. On `records_found`, no raw row is
  linked twice and, with row-level lineage, links equal `source_count` with distinct
  `:id`s. The per-fetch digest UNIQUE was dropped (see 3.5).
- **Link identity (0007).** Each link's source id must be the source id its raw row was
  stored under, in the record's dataset, via `records.evidence_identity` (digest,
  dataset, source id; readable by `ingest_writer` only, since future 311 rows use the
  request number as source id).
- **Admin record shape.** `hpd_building`, `registration` and `contact` are exactly the
  `administrative_record` kinds. Buildings and registrations are attributed by
  `row_identifiers`; contacts by `registration_coverage` (a new basis value: a contact
  has no location of its own). No location scope, original attribution or mapped BIN.
  Contacts have no lot membership. Building `is_current`, the registration
  shared flag, approved registration status labels, partial registration fetches
  stating no requested-building status, HPD role mapping and "a person's contact
  publishes no city, state or zip" are CHECKs.
- **Private keys.** Person-name and business-address keys (`firstname`,
  `middleinitial`, `lastname`, `title`, `businesshousenumber`, `businessstreetname`,
  `businessapartment`, `businesscity`, `businessstate`, `businesszip`) are refused in
  every public JSONB value. Field-name counts under `summary.withheld_field_counts`
  remain the one exception (10.2b).
- **Cross-lot coverage.** `records.registration_coverage` holds one row per
  registration ID a contacts fetch looked up: buildings on this lot, every building
  citywide with that registration (NULL when the lookup did not complete), the off-lot
  count and the association. CHECKs derive shared / requested-only / unknown from the
  coverage. At commit every contact must agree with its registration's row, contact and
  person counts must match, the coverage status must match which rows have coverage,
  and `summary.requested_building` must equal the this-building rows. The list is not
  repeated in `result_payload`.
- **BuildingID basis (D9).** `source_fetch.buildings_fetch_id` (same-run foreign key)
  names the stored building records a violations or complaints result was attributed
  with; an applied report requires it, and the linked fetch must match the report's
  dataset, status and data version. Every BuildingID-upgraded record must map to exactly
  one current, confirmed-lot building record with that building ID and the mapped BIN.
  At commit the report's before, after, upgraded and (complaints) group-after counts
  must equal the stored records. `save_research_snapshot` saves building records first
  and links them, all in one transaction.

- **Distinct coverage IDs (0008, review correction).** Sharing and off-lot counts are
  array cardinalities, so `building_ids_on_lot` and `covered_building_ids` must hold
  distinct, non-NULL IDs (CHECK `building_ids_distinct`; Pydantic refuses repeats too).
  Unknown coverage stays NULL.

### 10.3 Privacy

- **The scan covers every column** of every table and view in `records`, and in
  `public`, cast to text. That includes resolver JSON, query URLs, page URLs, status
  details, provenance, warnings, quality flags and every JSONB payload.
- **Sentinel test:** tests save results built from synthetic rows seeded with unique
  sentinel values. These include an apartment, a story, contact person names, business
  street and suite, `complaint_anonymous_flag`, the 311 `unique_key`, and a free-text
  address passed as resolver input. No sentinel may appear in any readable column.
  Forbidden keys are checked too.
- **Query URLs** contain only integer lot parts, validated BBLs and digit-only
  registration IDs. A test asserts this for every stored URL.
- **Real-role tests in CI.** They create login users that are members of `app_reader`
  and of `ingest_writer`, connect as each, and assert the following:
  - `SELECT` on `evidence.raw_row` is denied, and `has_schema_privilege(...,
    'evidence', 'USAGE')` is false;
  - no function in our schemas is `SECURITY DEFINER`;
  - `PUBLIC` holds no `EXECUTE` on our functions;
  - `app_reader` is a member of no other role;
  - the `evidence_availability` view exposes only its five columns;
  - `ingest_writer` cannot `UPDATE` or `DELETE`.
- **Logging:** `hide_parameters=True`, no row data in log messages, and a log-capture
  test with the sentinels.

### 10.4 Evidence retention

- `retain_until = fetched_at + 180 days` (configurable, default 180).
- **Expiry is a tombstone, not a delete.** The retention job, running as
  `evidence_retention`:
  - sets `original_row` and `original_sha256` to null and `expired_at` to now;
  - keeps the digest, dataset, source record ID, basis, redacted hash and fetch time.

  The plain hash is removed too, because it could be brute-forced back to unit details.
- **Lineage stays intact.** Foreign keys from `record_source_row` still resolve, and
  public records keep their keyed digest.
- **Two separate states.** `evidence_availability.held` tells "a public digest is
  retained" apart from "the original evidence can still be verified independently".
  After expiry the report states the original is no longer held. The digest then proves
  nothing by itself.
- **Backups.** Deleting from the live database does not remove copies in backups. The
  practical deletion window is 180 days plus the backup and point-in-time-recovery
  retention period of the hosting provider, which is set at deployment. Backups must be
  encrypted with restricted access, and any restore must re-run the retention job before
  the database is used. The Task 4 report will state this limit plainly.
- **Tests:**
  - expiry nulls the content and keeps foreign keys;
  - records still load, and they load as "not held";
  - re-saving a row that has expired re-holds it, under a new retention date;
  - nothing is purged before `retain_until`.

### 10.4a Step 2 decisions (2026-10-22)

- **Writer without read access.** `ingest_writer` holds `INSERT` on `evidence.raw_row`
  only. It uses `ON CONFLICT DO NOTHING` **without** a conflict target, because naming
  one requires `SELECT` on that column. The primary key is the table's only unique
  constraint, so nothing else can be absorbed. `RETURNING` is refused for the same
  reason. The writer learns held or expired state from `records.evidence_availability`
  (metadata only).
- **Restoring expired evidence.** An original row supplied for an expired tombstone is
  never silently ignored. It goes through `evidence.restore_expired_raw_row`, which
  updates a row only when it is a tombstone and its dataset, source record ID, basis and
  redacted hash all match. Otherwise it raises, and the whole save rolls back. It cannot
  touch held evidence and returns nothing. The writer gets no `UPDATE` right. Since
  migration 0003, only `evidence_restorer` may call it (see 10.4b).
- **Retention on a repeat sighting.** Seeing a held row again does **not** extend its
  retention: `retain_until` comes from its first fetch. A restored row gets a new
  `retain_until`, set from the new fetch. Whether a repeat sighting should extend
  retention is left for review in step 6.
- **Expiry rights.** `evidence_retention` has column-level rights only: `SELECT` on
  digest, `retain_until` and `expired_at`, and `UPDATE` on `original_row`,
  `original_sha256` and `expired_at`. It cannot read row content.
- **Error text.** Database errors during evidence writes are re-raised without the
  original exception, because PostgreSQL's DETAIL can quote the failing row. The server
  log can do the same, so production should set `log_error_verbosity = terse` for this
  database. That is a deployment setting, needs a superuser, and is not testable here.

### 10.4b Step 2 hardening (migration 0003, 2026-10-22)

**Finding 1: the restore function cannot enforce digest integrity.** Tested with real
logins: a direct call with matching digest and metadata but a forged `original_row`
stored the forged row. The function has no HMAC key, and the plain hash it receives is
supplied by the caller (a tombstone keeps none to compare against). The same holds for a
plain `INSERT` by `ingest_writer` under an absent digest.

The resulting guarantee, stated exactly:

- **PostgreSQL does not verify that a raw row matches its keyed digest**, on insert or on
  restore. It enforces the row's shape, tombstone state, matching metadata on restore,
  and the retention window, and nothing more about content.
- **Prevention** is in Python: `write_raw_rows` verifies every item under the configured
  key before any SQL runs.
- **Detection** is on read: `PostgresEvidenceReader` re-verifies each row and raises
  `EvidenceIntegrityError` instead of returning one that does not match.
- **Trusted restore boundary:** `EXECUTE` on the restore function moved from
  `ingest_writer` to a new NOLOGIN role, `evidence_restorer`, which holds no other right
  (no table access). Only the server-side writer's login is given it, alongside
  `ingest_writer`. A login with `ingest_writer` alone cannot restore. Anyone holding
  `evidence_restorer` or `ingest_writer` is trusted to write only verified rows.
- **Rejected alternative:** copying the HMAC key into the database (for example for
  `pgcrypto.hmac`) so SQL could verify. That would put the key in backups and within
  reach of the owner role and any SQL injection running as it, for a check the reader
  already makes.

**Finding 2: old source timestamps.** Restoring a copy fetched long ago stored a row whose
`retain_until` had already passed, and kept the first fetch's `fetched_at`, so
`retain_until` no longer equalled `fetched_at` + retention. Now:

- `retain_until = fetched_at + retention_days`, with both values from the fetch whose
  copy is currently held. A restore updates `fetched_at`, `query_url` and `data_as_of`
  to the restoring fetch. `first_seen_at` keeps the first storage time.
- `CHECK (retain_until <= fetched_at + interval '180 days')`: no row can be held longer
  than 180 days after its fetch. `EVIDENCE_RETENTION_DAYS` is limited to 1 to 180.
- A trigger on `INSERT` and on `UPDATE OF fetched_at, retain_until` refuses a row whose
  `retain_until` is not in the future, or whose `fetched_at` is more than 5 minutes in
  the future. The Python writer makes the same check first and raises
  `EvidenceRetentionError`, so a stale item fails the whole save rather than being
  stored and then immediately expired.
- Raw evidence is therefore held for at most 180 days after the fetch it came from, plus
  the gap until the next expiry run (scheduling arrives in step 6).

### 10.5 Round-trip and digest correctness

- `basis` (`original_row` / `sanitized_row`) is stored as an enum on each raw row and in
  each record's `digest_basis`, and round-trips.
- **JSONB is not the original HTTP response bytes.** It normalizes key order and
  whitespace and drops duplicate keys. Our digests were never over response bytes: they
  are over `canonical_json` (sorted keys, compact separators, UTF-8) of the parsed row.
  Socrata values are strings, which JSONB preserves exactly.
- **Digest check:** a test re-computes `keyed_digest` and `canonical_sha256` from every
  stored `original_row` after loading, and they must equal the stored digests. If a value
  ever changed in JSONB, verification fails and is reported, not hidden. Fixture page
  body hashes (`original_body_sha256`) stay in the fixtures and are not presented as
  stored response bytes.
- **Round-trip tests:** every recorded cassette result is saved and loaded back. That
  covers five addresses, all five result types, complaint groups, contact coverage and
  batch provenance, source-row references, and D9 reports. Each loaded model must equal
  the original (`==` on the Pydantic models), and validation runs again on load.

### 10.6 CI and migrations

- The offline job is unchanged and needs no database.
- The new `postgres` job:
  - runs the PostgreSQL 16 service container with a health check;
  - sets `REQUIRE_DB_TESTS=1`, so a skipped database test fails the job;
  - runs the `db` tests and fails if zero ran.
- Migrations run up, down and up on a disposable database.
- After `up`, tests check:
  - schema ownership (`nyc_owner`);
  - grants and default privileges;
  - no `CREATE` for `PUBLIC` on `public`.
- After `down`, tests check that the schemas are gone and the roles remain.
- Later steps add foreign-key, constraint, rollback and idempotency tests as each table
  arrives.
