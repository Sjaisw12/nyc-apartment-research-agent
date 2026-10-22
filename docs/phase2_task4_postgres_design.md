# Phase 2, Task 4 design: PostgreSQL storage for evidence and normalized records

Status: **design only, for review.** No PostgreSQL code, migrations, dependencies or
infrastructure have been added. Implementation starts only after Task 3 is closed and
this design is approved.

## 1. Goal and scope

Task 4 persists what Tasks 1 to 3 produce, so later phases (agent, API, report) can read
stored, cited records instead of re-fetching on every request. The stored data must keep
every guarantee the in-memory models already enforce.

**In scope:**
- schema and migrations;
- a PostgreSQL evidence store (replacing the git-ignored `.evidence/` directory);
- persistence of source results: status, provenance, freshness, records and summaries;
- round-trip loading back into the same Pydantic models;
- database-level constraints that mirror the model invariants;
- access roles;
- retention;
- local and CI test setup.

**Out of scope:** API endpoints, caching or refresh policy, the agent, DOB and 311
sources (Task 5), hosting and deployment.

## 2. Principles carried over

1. **Statuses are stored as returned.** Status and partial flag are stored as returned,
   and `no_records_found` is never stored without a complete fetch with count 0.
   Database CHECK constraints repeat the rules in `check_source_invariants`.
2. **Snapshots, not current state.** Every retrieval is stored as a new, append-only
   snapshot with its own `data_as_of`, freshness and query URLs. Nothing is updated in
   place, so a past report can always be reproduced from what was stored at the time.
3. **Building-level attribution needs building scope.** It may only exist under a
   building-scope fetch (CHECK constraint). BuildingID upgrades keep `original_attribution`
   and `attribution_basis`.
4. **Raw evidence stays private.** Raw rows (with apartment numbers, contact names,
   `complaint_anonymous_flag`, 311 `unique_key`) live in a separate schema that the
   application's read role cannot select from. Public tables hold only the redacted
   records the models already produce.
5. **Every source row is traceable** (Task 3 correction 3). Each normalized record links
   to all of its source rows and their keyed digests.
6. **Source counts and normalized counts are stored separately.**

## 3. Schema

There are two schemas: `records` (public, redacted) and `evidence` (private).

### 3.1 `records.retrieval_run`
One row per research request.

| column | notes |
|---|---|
| `id` | uuid primary key |
| `created_at` | |
| `input_digest` | keyed hash of the normalized address input. The free text is not stored (D16). |
| `resolution_outcome`, `resolution_level` | |
| `resolved_bbl`, `resolved_bin` | `resolved_bin` is only set at building level (CHECK) |
| `resolution` | jsonb: the resolver's public result (no user free text) |

### 3.2 `records.source_fetch`
One row per source per run: violations, complaints, buildings, registrations or
contacts.

| column | notes |
|---|---|
| `id`, `run_id` | |
| `source`, `dataset_id` | |
| `status` | enum of the five statuses |
| `status_detail` | |
| `partial` | bool |
| `join_scope` | `building` / `tax_lot` / null |
| `resolved_bbl`, `resolved_bin` | |
| `where_clause`, `order_by`, `page_size`, `row_cap` | |
| `source_count`, `rows_fetched` | |
| `incomplete_reasons` | text[] |
| `warnings` | text[] |
| `data_as_of` | truth header |
| `data_updated_at`, `metadata_updated_at` | freshness: data update and metadata update kept apart |
| `stated_update_frequency`, `stale` | freshness |
| `summary` | jsonb: the model's summary, as computed at the time |
| `building_id_attribution` | jsonb: the D9 report, when applied |
| `coverage_status` | contacts only |
| `fetched_at` | |

CHECK constraints:
- `records_found` → `partial = false`, no incomplete reasons, `source_count > 0`;
- `no_records_found` → `source_count = 0`;
- `incomplete_pagination` → `partial = true` and at least one reason;
- `unsupported_join` → no pages and no records;
- non-building scope → `resolved_bin is null`.

### 3.3 `records.fetch_page`
One row per HTTP response: `fetch_id`, `batch` (for contact and coverage batches), page
`index` (-1 is the count call), `url`, `rows`, `fetched_at`, `truth_last_modified`. This
is the citation trail.

### 3.4 `records.normalized_record`
One row per normalized record of any kind.

**Kind and identity:**
- `id`, `fetch_id`;
- `record_kind`: `violation`, `complaint_problem`, `hpd_building`, `registration` or
  `contact`;
- `evidence_kind`: `official_finding`, `reported_allegation` or `administrative_record`;
- `dataset_id`, `source_record_id` (violation ID, problem ID, building ID or `:id`).

**Attribution:**
- `resolved_bbl`, `lot_membership`;
- `attribution`, `attribution_basis`, `original_attribution`, `mapped_bin`;
- `location_scope` (where applicable);
- `quality_flags` text[].

**Integrity and payload:**
- `digest_basis`, `original_digest`, `redacted_sha256`;
- `payload` jsonb: the full public Pydantic model, exactly as the API would return it;
- `schema_version`.

Indexes: (`fetch_id`, `attribution`), (`resolved_bbl`, `record_kind`), and
`source_record_id`.

CHECK constraints:
- a building attribution is allowed only when the parent fetch has building scope
  (enforced with a trigger, since it spans two tables);
- `attribution_basis = 'hpd_building_id'` requires `original_attribution =
  'unattributed_on_lot'` and `mapped_bin is not null`.

### 3.5 `records.record_source_row`
`record_id`, `source_row_id`, `original_digest`, and `position` (0 is the record's own
row). Unique on (`fetch_id`, `source_row_id`) for complete fetches. Collapsed identical
copies are listed here, so source-row counts stay derivable.

### 3.6 `records.complaint_group`
Complaint-level grouping (Task 2): `fetch_id`, `complaint_id`, `attribution` (including
`mixed`), `problem_attributions` jsonb, `payload` jsonb.

### 3.7 `evidence.raw_row` (private schema)

| column | notes |
|---|---|
| `original_digest` | primary key: keyed HMAC, the value public records carry |
| `dataset_id`, `source_record_id` | |
| `original_sha256` | private plain hash |
| `basis` | `original_row` / `sanitized_row` |
| `original_row` | jsonb |
| `redacted_sha256`, `redacted_fields` | |
| `query_url`, `fetched_at`, `data_as_of` | |
| `first_seen_fetch_id` | |
| `retain_until` | |

Inserts use `ON CONFLICT DO NOTHING`. The same row seen again gives the same digest, so
it is stored once and referenced many times.

### 3.8 Why typed columns plus JSONB (D13)
- **Typed columns** carry everything we filter, join or constrain on: identifiers,
  status, attribution, flags and digests.
- **The JSONB payload** keeps the complete public model, so loading returns exactly
  what was produced, without a column for every dataset-specific field.

Fully normalized per-dataset tables would add five wide tables and migrations for every
model change, for fields the system never queries.

## 4. Access, privacy and retention

- **Roles:**
  - `app_reader`: SELECT on `records` only;
  - `ingest_writer`: INSERT on both schemas, no UPDATE or DELETE on `records`;
  - `evidence_admin`: SELECT on `evidence`, used only for audits and validation.

  The application never connects as `evidence_admin`.
- **Digest key:** database writes refuse to run without a configured
  `EVIDENCE_DIGEST_KEY`. The per-process fallback key would store digests that can never
  be matched again. Rotating the key needs a documented migration, because stored
  digests are keyed.
- **Retention (D15):** proposed 180 days for `evidence.raw_row`, enforced by a scheduled
  delete on `retain_until`. Public records keep their digest after the raw row expires,
  and the report says the original is no longer held.
- **Privacy test:** a test scans every `records` table and JSONB payload for withheld
  keys and values. The keys checked are `apartment`, `story`, `complaint_anonymous_flag`,
  `unique_key` and contact person fields. The test is the database counterpart of
  `test_fixture_privacy.py`.
- **No logging of row data:** SQL statement parameters are never logged.

## 5. Code shape

- **Database access:** SQLAlchemy Core 2.x (no ORM) with psycopg 3, and Alembic for
  migrations (D17). Core keeps SQL explicit, and Alembic gives reviewable,
  reversible migrations.
- **Evidence store:** `records/pg_evidence.py` holds `PostgresEvidenceStore`. It
  implements the existing `EvidenceStore` protocol (`put`, `get`), so the adapters do not
  change.
- **Repository:** `storage/repository.py` holds:
  - `save_run(resolution) -> run_id`;
  - `save_result(run_id, result)`, one transaction per source result, so a failed write
    leaves nothing partial;
  - `load_result(fetch_id) -> SourceResult | ComplaintSourceResult | ...`, validated
    through the same Pydantic models, so a stored result that breaks an invariant fails
    on load.
- **Configuration:** a `DATABASE_URL` secret, environment only.

## 6. Testing

- The offline suite stays runnable with no database.
- New tests are marked `db` and skipped unless `DATABASE_URL` is set.
- **Local:** `docker compose up db` (PostgreSQL 16).
- **CI:** a PostgreSQL 16 service container in GitHub Actions. Migrations run up, then
  down, then up.
- **Round-trip:** every recorded cassette result (five addresses, five sources, plus the
  D9 reports) is saved and loaded back. Each must equal the original model.
- **Constraints:** direct SQL inserts that break each CHECK are rejected (for example,
  `records_found` with `partial`, building attribution under a tax-lot fetch, and
  `hpd_building_id` basis on a row that was not unattributed).
- **Privacy scan and role tests:** `app_reader` cannot select from `evidence`.
- **Idempotency:** saving the same live rows twice stores each raw row once.

## 7. Proposed implementation steps (separate commits, after approval)

1. Dependencies, Alembic skeleton, compose file, CI service, and the empty-schema
   migration test.
2. `evidence.raw_row` and `PostgresEvidenceStore`, with idempotency and role tests.
3. Runs, fetches, pages and normalized records for violations, with round-trip and
   constraint tests.
4. Complaints and complaint groups.
5. Buildings, registrations, contacts, source-row links and D9 reports.
6. Privacy scan, retention job and report.

## 8. Decisions needed

- **D13. Hybrid storage:** typed columns for keys, status and attribution, plus a JSONB
  payload of the full public model. **Recommended: yes.**
- **D14. Append-only snapshots:** no in-place updates. Refresh and caching policy is
  decided later, with the API. **Recommended: yes.**
- **D15. Raw evidence:** a private schema with separate roles, 180-day retention, and
  database writes that require a configured digest key. **Recommended: yes (confirm 180
  days).**
- **D16. User input:** do not store the user's free-text address; store resolved
  identifiers and a keyed hash of the normalized input. **Recommended: yes.**
- **D17. Tooling:** PostgreSQL 16, SQLAlchemy Core with psycopg 3, Alembic, and a CI
  service container. **Recommended: yes.**

## 9. Acceptance criteria

1. Migrations apply and roll back cleanly in CI.
2. All recorded Task 1 to 3 results round-trip unchanged.
3. Every model invariant has a matching database constraint or trigger, each with a
   rejection test.
4. `app_reader` cannot read raw evidence, and the privacy scan finds no withheld field.
5. The offline suite, live tests, benchmark and CI all pass, with test counts reported as
   run.
