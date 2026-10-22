# Phase 2, Task 4, Step 4 report: HPD complaints and complaint groups in PostgreSQL

All work is on `claude/project-thread-5d0l0f`, the existing Phase 2 branch. Nothing has
been merged, deployed, or opened as a PR. Step 5 and DOB/311 integration have not
started. The Task 1 and Task 2 HPD Online validations remain **PENDING**.

## What was built

- **Migration 0005** (`0005_complaint_groups.py`). It is additive on top of 0004:
  - a new table, `records.complaint_group`;
  - a `complaint_id` column on `records.normalized_record`;
  - a `group_count` column on `records.source_fetch`;
  - new constraints and triggers.

  The only change to a 0004 object is the replacement of the result-payload privacy
  CHECK (see below).
- **`storage/repository.py`**:
  - **One save path for both result types.** `save_result` handles violations and
    complaints, keeping the step 3 entry points.
  - **New complaint entry points:** `save_complaint_result`, `save_complaints` and
    `load_complaint_result`.
  - **Save order, all in one transaction:** raw rows, then the fetch, pages, complaint
    groups, problem records and source-row links.

## How the model maps to tables

| Concept | Stored as |
|---|---|
| Complaint (`complaint_id`) | one `records.complaint_group` row |
| Problem row (`problem_id`) | one `records.normalized_record` row, `record_kind = complaint_problem`, with typed `complaint_id` |
| Source row behind a problem | one `records.record_source_row` link to `evidence.raw_row` |
| Duplicate report flag | `is_duplicate_report` in the problem payload; `duplicate_problem_count` on the group |
| Identical source copies | collapsed into one record, flagged `duplicate_id_same_content` |
| Conflicting copies (same `problem_id`, different content) | kept as separate records, each with its own raw row and link, flagged and unattributed |

## Actual counts (recorded data, sanitized fixtures)

All five addresses were `records_found`, both before and after D9. The groups were
recomputed by D9, and the same attribution counts were found in the database after
reloading.

| Address | Source count | Rows fetched | Problems | Complaints | Duplicate-flag problems | Groups before D9 | Groups after D9 |
|---|---|---|---|---|---|---|---|
| 1134 West Farms | 912 | 912 | 912 | 539 | 196 | 539 this_building | unchanged |
| 220 W 17th | 105 | 105 | 105 | 79 | 15 | 10 this_building, 8 other, 61 unattributed | 10 this_building, 69 other |
| 1217 West Farms (tax lot) | 51 | 51 | 51 | 48 | 3 | 48 lot_level | unchanged |
| 350 Fifth Ave | 10 | 10 | 10 | 6 | 0 | 5 this_building, 1 unattributed | unchanged |
| 1845 ACP | 2653 | 2653 | 2653 | 1124 | 376 | 747 this_building, 377 other | unchanged |

In the recorded data, no complaint is `mixed`, no source row is duplicated, and no HPD
text is `other` or `no_text`. The synthetic tests cover those cases instead.

**D9 at 220 W 17th, checked in the database after reload:**
- 84 problems have `attribution_basis = hpd_building_id`.
- All 84 have `attribution = other_building_on_lot` and `original_attribution =
  unattributed_on_lot`.
- Their mapped BINs are 1081547 and 1081549. None is the requested BIN, 1081543.
- Every complaint group containing one of these problems is `other_building_on_lot`,
  with only that bucket, or `mixed`. None is `this_building`.

## Source lineage results

- Every problem has exactly one link to its own raw row. The commit-time check from
  step 3 enforces this.
- Each raw row's basis matches the problem's `digest_basis`.
- **Source versus normalized counts, from a synthetic fetch:**
  - 8 source rows, 7 records: one identical copy was collapsed.
  - Both conflicting copies of `problem_id` 6 are separate records, each with its own
    raw row and link.
  - The complaint they belong to is `unattributed_on_lot`.
  - The pager marks such a fetch `incomplete_pagination`, never `records_found`.
- **Snapshots:** the before-D9 and after-D9 snapshots of 220 W 17th share their 105 raw
  rows, which are stored once. Re-saving the same result writes nothing new.

## Database constraints added

**`records.complaint_group` CHECK constraints:**
- `mixed_means_several_buckets`: the attribution is `mixed` exactly when there is more
  than one bucket.
- `single_bucket_is_the_attribution`: otherwise the attribution names the single bucket.
- `buckets_are_known`: only the four problem buckets are allowed.
- `bucket_counts_add_up`: the bucket counts sum to `problem_count`.
- `duplicates_within_problems`: the duplicate count is at most `problem_count`.
- `payload_matches`: the typed columns equal the payload, the `problem_ids` length
  equals `problem_count`, and `evidence_kind = reported_allegation`.
- `payload_has_no_private_keys`.

**Other constraints:**
- Unique (`fetch_id`, `complaint_id`) and (`fetch_id`, `ordinal`).
- **`normalized_record`:**
  - `complaint_problem_identity`: only problems carry a `complaint_id`, it equals the
    payload, and `problem_id = source_record_id`.
  - Foreign key `problem_has_its_complaint` on (`fetch_id`, `complaint_id`).
- **`source_fetch`:**
  - `groups_only_for_complaints`: groups appear only on complaint fetches, never
    outnumber records, at least one on a complaint `records_found`, and the payload
    holds no complaint list.
- **Payload privacy CHECK, replaced.** `summary.withheld_field_counts` is keyed by the
  names `complaint_anonymous_flag` and `unique_key`. The private-key check now skips
  that one object and requires its values to be numbers.

**Row triggers:**
- Groups only under a complaints fetch whose status allows records.
- Building-level group attribution only at building scope.
- Problem records only under complaint fetches, and complaint fetches hold only problem
  records.
- Sealed snapshots: groups cannot be added to a committed fetch.

**At commit** (`complaint_groups_at_commit`):
- The group count matches the groups present.
- For every group, these must equal what its problem records say:
  - problem count, duplicate count and `problem_ids`;
  - per-bucket counts;
  - the derived attribution.

A group therefore cannot claim a building that its problems do not support. This was
tested with a forged `this_building` group whose problems belong to another building.

## Tests

`tests/db/test_complaint_repository.py` has 24 new tests:
- **Grants:** complaint-group grants, checked with real logins.
- **Round trip, synthetic:**
  - problems, groups, a mixed complaint, a duplicate-flagged problem, a `no_text`
    outcome and a redacted HPD text;
  - duplicate and conflicting source rows.
- **Round trip, every status:**
  - `no_records_found`;
  - `incomplete_pagination` (partial);
  - `source_unavailable`;
  - `unsupported_join`;
  - tax lot (`lot_level` problems and groups).
- **Round trip, recorded data:** all five addresses, before and after D9, including the
  84-row checks above.
- **Idempotency:** re-saving is idempotent, and D9 is a separate snapshot over shared
  raw rows.
- **Rollback, with real PostgreSQL failures:**
  - a group CHECK violation leaves zero rows in all seven tables;
  - a problem CHECK violation after the groups were inserted does the same;
  - the commit-time group check also leaves zero rows.
- **SQL rejections, as the writer:**
  - the mixed flag and buckets;
  - group checks;
  - private keys in a group payload;
  - withheld counts that must be numbers;
  - a problem without its group;
  - record kind against fetch type, both directions;
  - groups under a violations fetch or an empty fetch;
  - a missing group count;
  - a building group at tax-lot scope.
- **Privacy scan:**
  - every column of every table and view `app_reader` can read was scanned as text for
    the apartment, the description unit, the typed address and a synthetic 311
    `unique_key`;
  - every JSON value was walked for forbidden keys;
  - nothing was found;
  - the `unique_key` value exists only in `evidence.raw_row`;
  - logs are clean.

| Check | Result |
|---|---|
| Offline suite, no database | 490 passed, 130 skipped (23 live, 107 db) |
| Database required | 597 passed, 23 skipped (live only); 107 db tests |
| Live tests | 23 passed |
| Lint and format | Clean |
| Phase 1 benchmark | Unchanged: 14/14 correct, 0 wrong building, 6/6 ambiguity, 4/4 abstentions, 2/2 unresolved BINs |
| Alembic CLI | base → 0005 → base → 0005 clean |
| CI | Run 39731324756 for `01c1dfc` green on both jobs (offline; `postgres` with `REQUIRE_DB_TESTS=1` and Alembic CLI up, down, up) |

## Files changed

- `backend/migrations/versions/0005_complaint_groups.py` (new)
- `backend/src/nyc_apartment_agent/storage/repository.py`: generic save and load,
  complaint groups
- `backend/tests/db/test_complaint_repository.py` (new)
- `backend/tests/db/test_migrations.py`: head is 0005
- `docs/phase2_task4_postgres_design.md`: section 10.2b
- `docs/phase2_task4_step4_report.md` (this report)

## Remaining limitations

- **Recorded data lacks some cases.** It contains no mixed complaints, no duplicated
  source rows and no unrecognised HPD texts, so those paths are proven on synthetic
  rows only.
- **Collapsed copies are not counted.** As in step 3, the model does not record how many
  identical copies were collapsed.
- **Group-level fields are Pydantic-only.** `types`, `major_categories`, `outcomes`,
  `received_date` and the group's quality flags are not re-derived in SQL. Only
  attribution, counts and membership are. Pydantic validates the rest on save and load.
- **Not built yet:** buildings, registrations and contacts persistence (step 5), and the
  full-schema scan across all result types, the scheduled retention job and backup
  handling (step 6).
- **Commit authorship.** Commits still show the environment's author identity
  (`Claude <noreply@anthropic.com>`). Trailers are omitted as agreed.

## Interview concepts

- **Parent/child consistency at commit.** A deferred constraint trigger checks that a
  derived summary (group attribution and counts) equals its children. This is a check
  no single-row CHECK can express.
- **Derived data must not overclaim.** The group's attribution is a function of its
  problems, and the database recomputes it. A forged "this building" group is rejected.
- **Composite foreign keys for scoping.** (`fetch_id`, `complaint_id`) ties a problem to
  its complaint within the same snapshot, never across snapshots.
- **Privacy rules need exceptions.** "Never store the key `unique_key`" conflicts with
  a count keyed by field names. The exception is one named path, and its values are
  restricted to numbers.
- **Additive migrations.** New rules come in as new triggers and constraints rather than
  rewrites of reviewed objects, so the downgrade is mechanical.
