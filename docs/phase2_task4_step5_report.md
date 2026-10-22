# Phase 2, Task 4, Step 5 report: HPD buildings, registrations and contacts in PostgreSQL

| Commit | Content |
|---|---|
| `f5fc070` | Migration 0006: several source rows per normalized record |
| `c26b4f0` | Migration 0007, repository and tests: buildings, registrations, contacts, cross-lot coverage, D9 basis |

Both are on `claude/project-thread-5d0l0f`, the existing Phase 2 branch. Nothing has been
merged, deployed, or opened as a PR. Step 6 and DOB/311 integration have not started.
The Task 1 and Task 2 HPD Online validations remain **PENDING**.

## 1. The review item: one record is not one source row

Registrations and contacts have no unique business key. Identical rows under different
Socrata `:id`s become **one** normalized record whose `source_rows` lists every `:id`
(itself first). Three counts are therefore kept apart, in the database and in this
report:

| Count | Column | Meaning |
|---|---|---|
| Fetched source rows | `rows_fetched`, `source_count` | what Socrata returned |
| Normalized records | `record_count` | rows in `normalized_record` |
| Stored links | `source_row_count` | rows in `record_source_row`, one per source row |

**Review of the step 3 rules.** The step 3 UNIQUE (`fetch_id`, `original_digest`) on
`record_source_row` was **not** compatible:
- **The case that breaks it.** In an incomplete fetch, the pager can return the same
  `:id` on two pages. The fetch is then marked incomplete, both copies collapse into one
  record, and that record lists the row twice. The UNIQUE would refuse to store it as
  fetched.
- **The other step 3 rule.** The commit-time rule "links equal `source_count`, or fewer
  when duplicates were collapsed" was written for one link per record.

**Migration 0006** drops that UNIQUE, adds an index on (`fetch_id`), and replaces the
commit-time check.

Kept from step 3:
- stored counts equal the rows;
- each record's first link is its own row;
- positions have no gaps;
- the raw-row basis equals the record's digest basis.

New:
- **`source_rows` equal the links.** A record's payload `source_rows` must equal its
  links in position order, by (`source_row_id`, `original_digest`). A row cannot be
  dropped, added or reordered between the model and the database.
- **Single-row records.** A record without `source_rows` (violations, complaint problems,
  building records) has exactly one link, with no `source_row_id`.
- **Link source id.** A link's source id is its own `source_row_id` when it has one.
- **Complete fetches** (`records_found`):
  - no raw row is linked twice;
  - with row-level lineage, links equal `source_count` and every `:id` is distinct;
  - otherwise the step 3 rule still applies.

**Link identity** (added by 0007). At commit, each link's source id must be the id its raw
row was stored under, in the record's dataset. A `:id` therefore cannot be paired with
another row's digest.

## 2. Actual counts

### Recorded data: all five addresses

Saved per address as one snapshot (buildings, registrations and contacts, one
transaction), reloaded, and compared with `==`.

Each count is fetched rows / normalized records / stored links.

| Address | Buildings | Registrations | Contacts | Coverage rows (association) | Requested-building registration status |
|---|---|---|---|---|---|
| 1134 West Farms | 1/1/1 | 1/1/1 | 5/5/5 | 1 (requested_building_only) | on_file_end_date_passed_after_data_update |
| 220 W 17th | 8/8/8 | 8/8/8 | 32/32/32 | 8 (1 requested_building_only, 7 other_buildings_only) | on_file_end_date_passed_after_data_update |
| 1217 West Farms (tax lot) | 1/1/1 | 1/1/1 | 4/4/4 | 1 (lot_level) | none: tax lot |
| 350 Fifth Ave | 1/1/1 | `no_records_found` 0/0/0 | `no_records_found` 0/0/0 | 0 | none_on_file |
| 1845 ACP | 2/2/2 | 2/2/2 | 10/10/10 | 2 (1 requested_building_only, 1 other_buildings_only) | on_file_end_date_passed_after_data_update |

- **Status.** Every result is `records_found` except the two marked.
- **Contacts naming a person.** 3, 24, 3, 0 and 8 contacts respectively, with names
  withheld. In the database, no contact naming a person has a public city or zip.
- **Corporate names** stay public.
- **What the recordings do not contain.** They have no identical copies, no shared
  registrations and no incomplete coverage. Every off-lot count is 0. Those cases are
  proven on synthetic rows instead.

### Synthetic snapshot: collapsed rows

| Result | Fetched rows | Records | Links | Notes |
|---|---|---|---|---|
| Registrations | 3 | 2 | 3 | `r-1`/`r-2` identical and collapsed (`duplicate_id_same_content`); `r-3` shares the registration (flagged `shared_registration`, count 2) |
| Contacts | 5 | 3 | 5 | `c-01`/`c-02`/`c-03` collapsed; 1 person (name withheld); 1 agent |
| Buildings | 2 | 2 | 2 | one plain link each |

Raw rows stored: 10, one per source row, including each collapsed copy.

**Incomplete fetch** (the case that needed 0006): `:id r-1` returned on two pages.
- Counts: 3 fetched rows, 2 records, 3 links.
- The record lists `r-1` twice.
- The fetch is stored and reloads equal.

## 3. Round-trip results

Every loaded result equals the saved one (`==` on the Pydantic models). Every link was
compared with its record's `source_rows`, in order.

| Result | Covered |
|---|---|
| Buildings | `records_found`; `unsupported_join`; tax lot (`not_applicable_tax_lot`) |
| Registrations | `records_found`; `no_records_found`; `incomplete_pagination`; `source_unavailable`; `unsupported_join`; tax lot |
| Contacts | `records_found`; `no_records_found`; `source_unavailable`, both kinds (contacts down; registrations down) |
| Contacts, `incomplete_pagination` | from an incomplete registration list, with records; before any registration row arrived (no summary, no count claimed) |
| Contacts, coverage unavailable | sharing unknown |
| Contacts at tax lot | `lot_level` |

**Registration limitations survive the round trip:**
- freshness;
- the status-basis dates;
- "none on file";
- no requested-building status on a partial fetch.

The database refuses any other status label, such as "expired".

**Shared registrations, as stored in `registration_coverage`:**

| Case | Association | Shared | On lot | Covered citywide | Off lot |
|---|---|---|---|---|---|
| Two buildings on this lot | shared_including_requested_building | true | 1, 2 | 1, 2 | 0 |
| Another building on another lot | shared_including_requested_building | true | 1 | 1, 999001 | 1 |
| Checked, only this building | requested_building_only | false | 1 | 1 | 0 |
| Coverage lookup failed | requested_building_sharing_unknown | NULL | 1 | NULL | NULL |

**D9 before and after, with its basis.** At 220 W 17th the violations result is saved
with its building records.
- The loaded result equals the saved one, and `buildings_fetch_id` reloads the building
  records it used.
- 87 rows are upgraded. Each keeps `original_attribution = unattributed_on_lot`.
- Every mapped BIN is the BIN of exactly one stored, current building record.
- **Totals over the five addresses** (the step 3 and 4 tests, now saved with their
  building records):
  - violations: 87 upgraded;
  - complaints: 84 upgraded, all at 220 W 17th, none counted for the requested building.
- The "before" snapshots carry no report and no link.

## 4. Attribution

- **Building-level versus tax-lot.**
  - At tax-lot scope, building records, registrations, contacts and coverage rows are
    all `lot_level`.
  - Building attributions (on records and on coverage rows) are refused at tax-lot
    scope by trigger.
  - A contact's association follows its attribution by CHECK, so `lot_level` contacts
    cannot claim the requested building.
- **Contacts have no location of their own.** They are attributed through their
  registration's buildings. This is stored as a new basis value,
  `attribution_basis = registration_coverage`. They have no lot membership, and no
  `resolved_bbl` in the payload (the column is the fetch's lot).
- **Building records and registrations** are attributed by `row_identifiers`. No admin
  record has a location scope, original attribution or mapped BIN.

## 5. Administrative-record privacy

**Refused keys.** `firstname`, `middleinitial`, `lastname`, `title`,
`businesshousenumber`, `businessstreetname`, `businessapartment`, `businesscity`,
`businessstate` and `businesszip` are now refused anywhere in any public JSONB value.
The step 4 keys stay refused. The only exception is the field-name counts under
`summary.withheld_field_counts`, whose values must be numbers.

**Contacts:**
- A contact naming a person must publish no city, state or zip (CHECK).
- The role must be HPD's registered type, mapped one to one; unknown types stay `other`
  with the raw value kept. An agent cannot be stored as owner.

**Scan.** Logged in as `app_reader`, every row of every readable table and view was
scanned as text, and every JSON value was walked.
- No invented name, title, street, suite or person's city appeared.
- No private key appeared.
- Corporate names and roles are present.
- The withheld originals exist only in `evidence.raw_row` (`evidence_admin`).
- Logs are clean.

**`records.evidence_identity`** (digest, dataset, source id) is readable by
`ingest_writer` only. For future 311 rows the source id is the request number, which is
not public by default.

## 6. Database constraints added

**`normalized_record` CHECKs:**
- `admin_record_shape`;
- `row_lineage_shape`;
- `flags_match_payload`;
- `building_current_follows_lifecycle`;
- `registration_sharing_flag`;
- `contact_person_has_no_address`;
- `contact_role_is_hpd_type`;
- `contact_association_follows_coverage`;
- the attribution-basis enumeration now includes `registration_coverage`.

**`records.registration_coverage`:**
- unique (`fetch_id`, `registration_id`) and (`fetch_id`, `ordinal`);
- CHECKs `association_follows_coverage`, `shared_follows_coverage`,
  `coverage_includes_lot`, `off_lot_count_follows_coverage`, `persons_within_contacts`,
  `payload_matches` (with key presence, so a missing key cannot pass as NULL) and
  `payload_has_no_private_keys`;
- a trigger allowing rows only under a contacts fetch, building attribution only at
  building scope, and sealed snapshots;
- grants: `app_reader` SELECT; `ingest_writer` SELECT and INSERT; nobody UPDATE or
  DELETE.

**`source_fetch`:**
- columns `coverage_count` and `buildings_fetch_id`, with foreign key
  `buildings_fetch_same_run`;
- CHECKs `coverage_only_for_contacts`, `registration_status_labels`,
  `building_id_report_matches` and `building_id_needs_its_buildings`.

**Row triggers:**
- `kind_admin`: admin result types hold only their kind, and vice versa;
- `building_id_source`: the linked building fetch is what the D9 report says it used,
  and is complete and for the same building when the report was applied;
- `building_id_evidence`: each BuildingID-upgraded record maps to exactly one current,
  confirmed-lot building record with that ID and the mapped BIN, and `this_building`
  exactly when that BIN is the requested one.

**At commit:**

| Trigger | What it checks |
|---|---|
| `admin_records_at_commit` | Registrations: rows of one registration agree on their building count, which is at least the number of distinct buildings. Contacts: coverage count; every contact agrees with its registration's coverage row; contact and person counts; coverage status against which rows have coverage; `requested_building` equals the this-building rows |
| `building_id_report_at_commit` | The D9 report's before, after and upgraded counts (and complaints' group-after counts) equal the stored records |
| `link_identity_at_commit` | Each link's source id is its raw row's |
| `complete_at_commit` | Replaced by 0006 (section 1) |

**All writes in one transaction.** `save_research_snapshot` writes:
- the run;
- the building records first;
- then registrations, contacts, and violations or complaints, with D9 results linked;
- every raw row, record, link and coverage row.

It commits once.

## 7. Tests

`tests/db/test_admin_repository.py` has 37 new tests:
- **Grants**, with real logins, for the coverage table and the identity view.
- **Round trips:** the synthetic snapshot with its counts; the incomplete fetch with a
  repeated row; every status; unsupported joins and tax lot; the four shared-registration
  cases; all five recorded addresses.
- **Rollback, with real PostgreSQL failures.** The same 6 tables plus `evidence.raw_row`
  hold **zero rows** afterwards, including the building records and raw rows written
  earlier in the same transaction:
  - an incomplete registration fetch claiming a status (`registration_status_labels`);
  - conflicting identifiers, each `:id` paired with the other row's digest (link
    identity at commit);
  - an agent relabelled owner (`contact_role_is_hpd_type`);
  - a shared registration's contact claiming requested-building-only (coverage at
    commit);
  - a shared registration row claiming one building (`registration_sharing_flag`);
  - failed coverage stored as `complete` (coverage status at commit);
  - coverage invented for one building (`off_lot_count_follows_coverage`);
  - a D9 result saved without its building records (`building_id_needs_its_buildings`).

  I checked which constraint each case hits by printing the unscrubbed database error
  in a local debugging run. The saved errors carry only the SQLSTATE.
- **SQL rejections, as the writer:**
  - `source_rows` against links;
  - link identity;
  - each contact check;
  - private keys;
  - coverage checks;
  - kinds in both directions;
  - coverage under a non-contacts fetch;
  - missing coverage at commit;
  - building record checks;
  - a status label `expired`;
  - D9: a stale report, wrong report counts, a forged building ID and a building fetch
    from another run.
- **Idempotency:** re-saving returns the same snapshot, and raw rows are shared across
  runs.

**Changes to existing tests.**
- The step 3 and step 4 D9 tests now save their building records with the D9 result,
  which the database requires.
- One record-check case now expects the earlier BuildingID evidence trigger. The
  `building_id_basis` CHECK is still there behind it.
- 0006 added 5 lineage tests to `test_repository.py`.

| Check | Result |
|---|---|
| Offline suite, no database | 490 passed, 172 skipped (23 live, 149 db) |
| Database required (`REQUIRE_DB_TESTS=1`) | 639 passed, 23 skipped (live only); 149 db tests |
| Live tests (`RUN_LIVE_TESTS=1`) | 23 passed |
| Lint and format | Clean |
| Phase 1 benchmark | Unchanged: 14/14 correct, 0 wrong building, 6/6 ambiguity, 4/4 abstentions, 2/2 unresolved BINs |
| Alembic CLI | base → 0007 → base → 0007 clean; no leftover test databases or roles |
| Secret scan of the diff | Only invented test sentinels |
| CI | See the reply in the thread |

## 8. Files changed

- `backend/migrations/versions/0006_multi_row_lineage.py` (new)
- `backend/migrations/versions/0007_admin_records.py` (new)
- `backend/src/nyc_apartment_agent/storage/repository.py`:
  - three result kinds;
  - links per source row;
  - coverage rows;
  - `buildings_fetch_id`;
  - `save_research_snapshot`;
  - loaders.
- `backend/tests/db/test_admin_repository.py` (new)
- `backend/tests/db/test_repository.py`, `test_complaint_repository.py`: lineage tests;
  D9 now saved with its building records
- `backend/tests/db/test_migrations.py`: head is 0007
- `docs/phase2_task4_postgres_design.md`: sections 3.5 and 10.2c
- `docs/phase2_task4_step5_report.md` (this report)

## 9. Limitations

- **Recorded data lacks some cases.** It has no collapsed copies, no shared
  registrations and no incomplete coverage. Those paths are proven on synthetic rows.
- **Pydantic-only fields.** Summary fields are validated by Pydantic on save and load,
  not re-derived in SQL:
  - the registration summary's per-building status and its dates;
  - roles and corporate-name lists per registration;
  - withheld-field counts;
  - freshness.

  The registration status labels and the partial-fetch rule are in SQL.
- **The registration building count** is checked as "at least the distinct normalized
  building IDs". Exact equality could refuse valid data whose raw building IDs differ
  only in spacing.
- **The off-lot count** assumes the covered and on-lot lists hold distinct IDs, as the
  code builds them.
- **D9 is not stored before the building records.** A D9 result that was applied can
  only be stored with its building records. Callers must save buildings first;
  `save_research_snapshot` does this.
- **Existing rows are not re-checked against the new key list.** The extended
  private-key function and the new CHECKs apply to new rows. The tests run from an
  empty database; no deployed database holds data yet.
- **Not built yet (step 6):**
  - the full-schema scan across all result types;
  - the scheduled retention job;
  - backup handling;
  - `log_error_verbosity` for the server log.
- **Commit authorship.** Commits still show the environment's author identity
  (`Claude <noreply@anthropic.com>`). Trailers are omitted as agreed.

## 10. Interview concepts

- **Normalized records versus source rows.** Collapsing duplicates must not lose
  lineage. A one-to-many link table keeps every source row citable, and three separate
  counts (fetched, normalized, linked) make that visible.
- **Constraints must match the data's real shape.** A UNIQUE that looked safe for one
  row per record was wrong for collapsed records in partial fetches. The rule moved to
  where it holds: commit time, complete fetches only.
- **Equality of a JSON list and child rows.** Comparing `jsonb_agg(... ORDER BY
  position)` with the payload makes the database prove the model and the link table say
  the same thing.
- **Derived semantics in SQL.** Shared, requested-only and unknown are functions of the
  coverage, so the database derives and checks them. A stored association can never
  claim more than the coverage supports.
- **NULL means "not checked".** `covered_building_ids = NULL` is different from "only
  this building". CHECKs written with `IS NOT DISTINCT FROM` and key-presence tests keep
  NULL from silently passing.
- **Evidence for an inference.** A BuildingID upgrade references the stored building
  records it used, through a same-run composite foreign key. A trigger proves that each
  mapped BIN exists there.
- **Least privilege for identifiers.** The identity view is readable only by the role
  that needs it, because one dataset's harmless source ID is another dataset's private
  key.
