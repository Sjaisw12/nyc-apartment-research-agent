# Phase 2, Task 5, step 5: PostgreSQL integration (for review)

Status: **submitted for independent PostgreSQL integration acceptance.** Work stopped
here: no Step 6, LangGraph, frontend, deployment or other integration; no PR, merge,
history rewrite, branch rename or deletion; no deployment configuration changed.

Still open, unchanged and separate:

- HPD Online independent validation, Parts A and B: **pending** (Sanyam's results).
- DOB, OATH and 311 independent validation, Parts C, D and E: **pending**.
- Attribution ground truth: **draft**, not approved.
- 19 DOB disposition codes still lack official definitions (unchanged from step 2).

Branch `feat/phase-2-dob-311`. Code under review: commits `b3d74c9`, `9d71d5c`, `1341ff0`,
`c70fded` (NULL agency, earlier in this step) and `d8c7f1d`. This report and design
section 18.10 are committed on top of `d8c7f1d`.

## 1. Migration 0011

File: `backend/migrations/versions/0011_task5_records.py`. Reviewed first: every CHECK,
trigger, foreign key, JSONB privacy function and grant from 0001 to 0010.

**Additive.** No new table, column, role, grant or reader privilege. A test compares the
catalog (constraints, functions, triggers, relation grants, function grants) before and
after: relation grants are identical, and the only additions are the items below.

| Kind | Added or changed |
|---|---|
| Vocabulary CHECKs widened | `result_type` (+3), `record_kind` (+3), `evidence_kind` (+`enforcement_action`), `lot_membership` (+`via_bin_mapping`), `attribution_basis` (+`bin_lot_mapping`) |
| Privacy function replaced | `records.forbidden_keys_absent`: 0007 keys plus ECB respondent keys, `violation_description`, `hearing_time`, 311 location keys (address, streets, coordinates, landmark, city, zip, channel, taxi location), `dobrundate`, `:id` |
| `normalized_record` CHECKs | `task5_kind_dataset`, `task5_evidence_kind`, `task5_attribution`, `task5_no_contact_strings`, `task5_orders_never_in_effect`, `dob_record_shape`, `ecb_record_shape`, `sr311_record_identity` |
| `source_fetch` CHECKs | `task5_type_dataset`, `task5_provenance_matches`, `dob_result_shape`, `ecb_result_shape`, `task5_building_claims`, `sr311_result_rules` |
| `raw_row` CHECK | `sr311_raw_row_token` |
| Triggers | `kind_task5` (record kind matches the fetch's dataset), `task5_page_guard` (page from the fetch's dataset; 311 URLs carry no `:id` and no `unique_key` filter), deferred `task5_at_commit` |
| Replaced | `check_fetch_complete` (DOB and ECB source rows compared on `source_row_id` and `original_digest`), `row_lineage_shape` (DOB and ECB), `unsupported_join_made_no_requests` (see 1.1) |

The upgrade refuses to run if any stored payload already holds a newly forbidden key.

**Reversible.** The downgrade first refuses with "Task 5 rows exist; remove them before
downgrading below 0011" when any Task 5 fetch, record or raw row exists (a raw row alone
is enough), and changes nothing when it refuses. Otherwise it restores every 0010
definition exactly. Verified on disposable databases: base to 0010 to 0011 to 0010 to
0011, with catalog equality at each 0010 and each 0011; HPD snapshots saved under 0010
load unchanged after the upgrade and after the downgrade.

### 1.1 Two constraint conflicts, each shown failing first

1. **DOB tax-lot unsupported join.** Saving 1217 West Farms (DOB `unsupported_join` at
   tax-lot scope) failed on `unsupported_join_made_no_requests`, which required
   `join_scope` and `resolved_bbl` to be null. Smallest change: for that case only, the
   result may keep `join_scope = tax_lot` and its resolved BBL; it still needs page count
   0, record count 0 and no BIN.
2. **DOB link identity.** Saving DOB complaints failed because raw rows were stored under
   `complaint_number` while the record link names the Socrata `:id`. The database rule
   was kept; the adapter now stores the raw row under the `:id` (`b3d74c9`). The old form
   is a refusal test (`test_the_original_dob_link_conflict_is_refused`).

Also: the ECB-only records_found branch in `check_fetch_complete` was removed, so ECB
follows the stricter default rule (identical copies already made the ECB result
incomplete in the adapter).

## 2. Invariants: where each one is enforced

| Invariant | PostgreSQL | Python |
|---|---|---|
| Result type, dataset and record kind agree | CHECK + trigger | models |
| No private key anywhere in a payload, at any depth | `forbidden_keys_absent` (recursive) | models, redactor |
| No email or phone string in Task 5 payloads | `task5_no_contact_strings` | screens, redactor |
| 311 `k311_` token format; `request_ref` = `source_record_id`; raw row keyed by token | CHECKs | `k311_token` |
| 311: no `:id`, no `unique_key` filter, no 7-to-9-digit number, dates only | `sr311_record_identity`, `sr311_result_rules`, page guard | models |
| 311 never building-attributed; no BIN; no building claims | CHECKs | models |
| NULL agency found ⇒ incomplete and not established; unknown ⇒ warning | `sr311_result_rules` | adapter (`c70fded`) |
| Excluded counts established only when complete and on the accepted version | `sr311_result_rules` | adapter |
| Copies sum to the source count; ECB financials match the record count | `task5_at_commit` (deferred) | models |
| DOB and ECB lineage: every source row linked to its raw row by `:id` and digest | `check_fetch_complete`, link triggers | repository |
| Orders are never shown as in effect | `task5_orders_never_in_effect` | models |
| The token is the right HMAC of the key | **Python only** (needs the secret key) | `k311_token` |
| A text value is on the allowlist | **Python only** (the database checks patterns, not the list) | `public_value` |
| Attribution logic (which row is lot-level or conflicting) | shape only | adapter |
| Pagination, retries, snapshot stability | consistency only | pager |

## 3. Save, load and lineage

`save_research_snapshot` writes one run per call in a single transaction; load functions
`load_dob_complaint_result`, `load_ecb_violation_result` and `load_service_request_result`
rebuild the typed results.

| Test (`tests/db/test_task5_repository.py`, 11) | Shows |
|---|---|
| Five recorded addresses round trip exactly | every DOB, ECB and 311 result equal after reload; no plain 311 key in the records schema |
| Every link points at the raw row with the same source id | link and `evidence_identity` agree |
| Resave is idempotent; separate runs stay separate | no duplicate rows |
| Rollback on a Python failure, a commit-time failure, a row CHECK failure | nothing stored |
| Recorded snapshots survive expiry unchanged | records, digests, tokens, counts and incompleteness identical after raw rows expire |
| Resave and restore after expiry | a repeat sighting does not restore; a later copy does |
| Identical copies keep their lineage | DOB 2 links; ECB and 311 incomplete with shared digests |
| NULL-agency rows stored as incomplete | observed HPD and DOB counts kept, not established |
| Unknown NULL coverage stored with its warning | warning survives the round trip |

Recorded results stored and reloaded unchanged (step-4 targets, not altered):

| Address | 311 retained | 311 status | DOB | ECB |
|---|---|---|---|---|
| 1134 West Farms Rd | 89 | incomplete | 12, records_found | 3 |
| 220 W 17th St | 89 | complete | 46, records_found | 4 |
| 1217 West Farms Rd | 32 | complete, lot_level | unsupported_join | 12 |
| 350 5th Ave | 485 | complete | 141, records_found | 241 |
| 1845 ACP Blvd | 250 | incomplete | 231, records_found | 52 |

HPD and DOB exclusion counts remain not established at all five addresses. No fixture
or source snapshot was changed.

## 4. 311 privacy evidence

- **Direct SQL** (`tests/db/test_task5_constraints.py`, 5 tests): an authorized
  `ingest_writer` inserts forged rows; each is refused with SQLSTATE 23514 by the rule
  meant to catch it (checked case by case). 35 forged 311 cases (plain key, wrong token,
  `:id`, `unique_key` filter, location keys, timestamps, building claims, NULL-agency
  and excluded-count contradictions, and more), 17 ECB, 15 DOB, plus dataset/type
  mismatches. A Pydantic model is never relied on.
- **Sentinels** (`tests/db/test_task5_privacy_audit.py`, 5 tests): DOB unit and extract
  time, ECB respondent fields and inspector text, 311 keys and location fields. None is
  readable by `app_reader`, `ingest_writer`, `evidence_retention` or `evidence_restorer`
  (every visible column scanned); they appear only in raw evidence (positive control);
  `evidence_identity` shows tokens only; the digest key is stored nowhere; a failed
  write's error and DEBUG logs carry no sentinel, and nothing is stored.

## 5. Allowlist decision A: safe expansion, reviewed

**Measurement** (`backend/scripts/task5_measure_311_history.py`, read-only, aggregates
only): grouped counts per field per calendar year, 2020 to 2026, agency not HPD or DOB.
42 bounded queries answered, all on one data version (`Fri, 23 Oct 2026 01:38:29 GMT`),
each under its `$limit`; one timed-out query (complaint_type, 2026) was logged and asked
again. A field on a mixed version, or with a partial answer, would not be used. 17,687,496
rows. The raw output stayed outside the repository and was not committed.

**Review** (`backend/scripts/task5_review_311_history.py`): a candidate is a value seen at
least 20 times and not on the step-4 list. Gate 1 is an automatic screen that fails
closed (unit, floor, phone, email, honorific, street address, 4+ digit number, date,
time, `#`, URL, bracketed aside). Gate 2 is a manual reading of every remaining
candidate. Committed result: `docs/task5/sr311_history_review.json` with admitted values
verbatim, rejected values as SHA-256 hashes with a reason code, and the query log.

| Field | Candidates | Auto-rejected | Rejected by review | Admitted |
|---|---|---|---|---|
| agency | 6 | 0 | 0 | 6 |
| complaint_type | 35 | 0 | 0 | 35 |
| descriptor | 303 | 0 | 0 | 303 |
| descriptor_2 | 241 | 0 | 0 | 241 |
| location_type | 8 | 0 | 0 | 8 |
| resolution_description | 559 | 96 | 5 | 458 |

- The picklist fields are city categories and codes.
- `resolution_description`: admitted texts are generic agency outcomes. They name no
  person, address, business, vehicle or date, and tell no story about the reporter.
- Auto-rejected: 84 contain an agency phone number, 10 a URL, 1 a long number and 1 a
  date. These stay withheld; no exception was made, even for public agency numbers.
- Rejected by review: 4 outreach-outcome texts (whether an individual accepted or
  refused housing or mental-health services) and 1 naming a private vendor.

**Reference file.** `task5_build_311_reference.py` now has `base()`, which rebuilds the
step-4 file byte for byte (SHA-256 `6b2a6055...d532`, pinned). `build()` adds only
reviewed admissions. New reference SHA-256 `2f513ee0...33cc`, review SHA-256
`fe5a5516...6d71`, both pinned. The adapter still runs the unit and floor redactor and
the contact patterns on every value, even when it is listed.

**Before and after, the same 945 recorded rows** (recordings unchanged; both pinned in
`test_sr311_cassette.py`):

| Field | Withheld, step 4 | Withheld, after A |
|---|---|---|
| resolution_description | 601 | 54 |
| descriptor | 101 | 0 |
| descriptor_2 | 58 | 0 |
| complaint_type | 56 | 0 |
| agency | 5 | 0 |
| Rows flagged `uncommon_value_withheld` | 659 | 54 |

Of the 54: 43 templates with a phone number, 10 with a URL, 1 rejected by review. The
same test shows that only display text changed: tokens, original digests, statuses,
dates and counts are identical, and `redacted_sha256` (a hash of the public view)
changes on exactly the 633 rows whose text changed.

Privacy tests (`test_sr311_reference.py`, 11): every admitted value passes every screen
and is in the packaged reference; rejected values appear only as hashes; the review file
holds no phone, email or URL outside the admitted list; known sensitive templates stay
withheld.

### 5.1 Correction after the 20:00Z acceptance: person-centered outreach

Every admitted `resolution_description` was audited again, and so was the step-4 base
list, against one rule. Text that reveals an individual's whereabouts, outreach directed
at them, a social-service contact or outcome, or an arrest is withheld. Generic routing
and condition outcomes stay public, for example "DHS visited the location but could not
find the condition", "referred to DHS", "no Encampment was found" and "not within DHS
jurisdiction". Rejected and withdrawn values are recorded only as SHA-256 hashes with a
reason code.

| Where | Reason code | Values | Examples (paraphrased) |
|---|---|---|---|
| History admissions | `person_centered_outreach` | 3 | DHS could not find the person you reported; DHS sent a mobile outreach team; DHS referred the request to an outreach provider |
| History admissions | `individual_service_outcome` | 4 | Department for the Aging contacted you and provided assistance, sent you documents, or tried to reach you |
| History admissions | `third_party_arrest` | 2 | police made an arrest |
| Step-4 base list | `person_centered_outreach` | 4 | outreach team could not find the individual; outreach team attempted to engage individuals; DHS will send outreach workers |
| Step-4 base list | `individual_service_outcome` | 2 | the individual accepted, or did not accept, assistance from the outreach team |
| Step-4 base list | `third_party_arrest` | 1 | police made an arrest |

The step-4 base list held these texts because they came in through the dictionary and
step-1 frequency rule. They are withdrawn in `step4_withdrawn`. `base()` still rebuilds
the accepted step-4 file byte for byte, and the published reference removes them.

Updated counts:

| Item | Before correction | After correction |
|---|---|---|
| `resolution_description` rejected by review | 5 | 14 |
| `resolution_description` admitted | 458 | 449 |
| Step-4 values withdrawn | 0 | 7 |
| `resolution_description` allowlist size | 704 | 688 |

On the same 945 recorded rows, withheld values go from 54 to **65**:

| Reason | Rows |
|---|---|
| Templates with a phone number | 43 |
| Templates with a URL | 10 |
| Withdrawn step-4 outreach texts | 7 |
| Withdrawn step-4 service-outcome texts | 3 |
| Rejected history outreach text | 1 |
| Rejected history service-outcome text | 1 |

Ten of these rows showed an outreach or service-outcome text publicly under the accepted
step-4 list. The step-4 before-count (659) is unchanged.

Records, tokens, original digests, dates, attribution and counts are unchanged, as are
the recordings, migration 0011 and the roles. `redacted_sha256` changes on exactly the
642 rows whose text changed. New hashes: reference `b5c07538...b7ee`, review
`4586e185...8bba`. The full suite with PostgreSQL gives 1167 passed and 33 skipped.

## 6. Role matrix (from the catalog; 0011 changed none of it)

| Role | Rights |
|---|---|
| `app_reader` | SELECT on records tables and `evidence_availability` |
| `ingest_writer` | INSERT and SELECT on records tables; SELECT `evidence_availability`, `evidence_identity`; INSERT `raw_row`; EXECUTE CHECK helpers |
| `evidence_admin` | SELECT `raw_row` |
| `evidence_retention` | SELECT (`original_digest`, `retain_until`, `expired_at`); UPDATE (`original_row`, `original_sha256`, `expired_at`) |
| `evidence_restorer` | EXECUTE `restore_expired_raw_row` (11 arguments) |

14 direct-SQL bypass attempts across the five roles are refused (reader writes, reads
of raw rows, writer updates and deletes, retention rewrites of held evidence, restorer
reads and forged restores, admin writes and deletes).

**Limits, stated plainly.**

- Backups and WAL archives hold raw rows until expiry plus the backup retention period
  (runbook section on deletion bounds).
- `log_error_verbosity = terse` for the retention role is an operator setting, not part
  of the migration.
- The owner or superuser can read everything.
- Nothing is deployed and no deployment configuration was changed.

## 7. Acceptance matrix (at `d8c7f1d`)

| Run | Result |
|---|---|
| Full suite with PostgreSQL 16 (`REQUIRE_DB_TESTS=1`) | 1166 passed, 33 skipped (live) |
| Offline (no database) | 945 passed, 254 skipped |
| Live (`RUN_LIVE_TESTS=1 -m live`) | 33 passed |
| Resolver benchmark | 14/14 correct auto-matches, 0 wrong building, ambiguity 6/6, abstentions 4/4, BIN preserved 2/2 |
| ruff check and format | clean |
| `task5_review_311_history.py check` | review accounts for every candidate |
| CI | storage at `1341ff0`: [run 40009132352](https://github.com/Sjaisw12/nyc-apartment-research-agent/actions/runs/40009132352), green; `d8c7f1d` and the report commit: see the thread |

Recorded fixture counts were not changed and no new source snapshot was made.

## 8. Limitations and pending

- The database checks the token's format, not its HMAC, and checks text patterns, not
  allowlist membership (section 2).
- The 2020-to-present measurement is a snapshot on one data version. New agency wording
  is withheld until a later reviewed measurement admits it.
- Mis-encoded characters in some agency templates are kept verbatim as published.
- The NULL-agency count is not in the recordings, so in replay it is always not
  established.
- Validations A and B (HPD) and C, D and E (DOB, OATH, 311) are pending. The attribution
  ground truth is still a draft.
