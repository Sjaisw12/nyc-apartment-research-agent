# Phase 2, Task 5, step 4: 311 Service Requests adapter (for review)

Status: **submitted for the step-4 gate.** Only 311 Service Requests from 2020 to Present
(`erm2-nwe9`) is implemented. No migration 0011 or production persistence, no LangGraph,
frontend or deployment, no PR, merge, history rewrite or branch deletion, and no ECB or
DOB refactor. Step 5 (PostgreSQL integration) waits for separate approval.

Still open, unchanged and separate:

- HPD Online independent validation, Parts A and B: **pending** (Sanyam's results).
- DOB, OATH and 311 validation, Parts C, D and E: **pending** (design 11.1).
- Attribution ground truth: **draft**, not approved.

## 1. What was built

| Item | Where |
|---|---|
| Typed models: request, count check, excluded counts, attempt, bucket, summary, result | `backend/src/nyc_apartment_agent/records/sr311_models.py` |
| Adapter | `backend/src/nyc_apartment_agent/records/sr311.py` |
| `k311_` token (one function) | `records/redaction.py`: `k311_token`, `K311_DOMAIN`, `K311_PATTERN` |
| Pager extension (minimal): `retry_on`, per-attempt records, failed-retry handling | `sources/socrata_pager.py` |
| Dictionary extract (stdlib xlsx reader) | `backend/scripts/task5_extract_311_dictionary.py` -> `docs/task5/sr311_dictionary_values.json` |
| Allowlists and status mapping | `backend/scripts/task5_build_311_reference.py` -> `records/data/sr311_reference.json` |
| Tests | `tests/unit/test_sr311.py` (99), `test_sr311_cassette.py` (10), `test_k311_token.py` (18), `test_sr311_reference.py` (6), 4 new in `test_socrata_pager.py`, `tests/db/test_sr311_evidence.py` (2), `tests/live/test_live_sr311.py` (4) |
| Design notes for what was built | design section 18.9 |

## 2. Recorded fixtures, replayed offline

The test replays the committed recordings unchanged on disk: resolver responses from
`tests/fixtures/hpd/buildings_cassette.json`, Footprints and 311 from
`tests/fixtures/dob311/` (2026-10-23 04:51Z). The committed rows have `unique_key` =
`[WITHHELD]`; the test gives each row a distinct synthetic decimal key in memory, built
from its `fixture_row_ref`, and drops the ref. Those keys and their tokens are test
values only and are never treated as production evidence (every digest has basis
`sanitized_row`). The adapter rebuilds the five recorded `$where` clauses exactly.

| Address | Target | Retained | Status | Count / data headers | Attempts |
|---|---|---|---|---|---|
| 1134 West Farms Rd | 89 | 89 | `incomplete_pagination` | Thu / Fri | 3, all rejected |
| 220 W 17th St | 89 | 89 | `records_found` | Fri / Fri | 1, accepted |
| 1217 West Farms Rd | 32 | 32 | `records_found` (tax lot, `lot_level`) | Fri / Fri | 1, accepted |
| 350 5th Ave | 485 | 485 | `records_found` | Fri / Fri | 1, accepted |
| 1845 ACP Blvd | 250 | 250 | `incomplete_pagination` | Thu / Fri | 3, all rejected |

- The 1134 West Farms and ACP incompleteness is preserved, with reasons
  `dataset_changed_during_retrieval` and `source_snapshot_unstable`. The fixture was not
  altered. Replay answers each retry with the same recorded pair, so all three attempts
  are rejected; every attempt's headers are in the result.
- **HPD/DOB exclusion counts: not established at all five.** The grouped counts were
  recorded on Thu 22 Oct 01:37:36. At 1134 and ACP the main fetch is incomplete; at the
  other three the main fetch is on Fri 23 Oct 01:38:29 ("data version differs"). The
  observed values are kept as `counts_observed` (309/1, 47/26, 44/1, 8/11, 173/12 for
  HPD/DOB) and never presented as established.
- The NULL-agency count was added after the recording, so in replay it fails; the test
  asserts that this is the only unrecorded request at each address.
- Records: 925 Closed, 19 In Progress, 1 Pending; 2 Closed rows with no closed date
  (flagged `status_fields_disagree`); all dates date-only; 945 distinct tokens.
- ACP queries both lots of the condo family; rows on the base lot carry
  `condo_lot_family`. Every record is `unattributed_on_lot`, except `lot_level` at 1217.

## 3. Live observation (2026-10-23 09:58Z, not a fixture)

All five returned `records_found` with one accepted attempt on Fri 23 Oct 01:38:29
(89, 89, 32, 485, 250), the HPD/DOB counts established (309/1, 47/29, 44/1, 8/11,
173/12) and the NULL-agency count established at 0. **This is one observation**, not a
property: the source served one version this time. 220 W 17th's DOB count moved from
26 to 29 since the recording.

## 4. What is established and what is only observed

Established by tests (synthetic and replay):

- The exact query; no key value, `:id`, BIN or address in any request, provenance or log.
- A complete result needs a count and data on the same header, rows equal to the count,
  and distinct keys; otherwise up to two retries, then `source_snapshot_unstable` with
  rows kept; versions are never merged; every attempt is recorded.
- Excluded counts are established only in the complete, same-version case; every other
  case says why.
- Token contract (format, stability, rotation, key separation, domain separation from
  row digests, refusal of missing or malformed keys without echoing them).
- Never a building attribution; no address fallback.
- Sentinels (name, unit, floor, phone, email, coordinates, personal detail) in every
  source field reach only `original_row`: not the result JSON, request URLs, provenance,
  logs or evidence identity. In PostgreSQL the raw row's id is the token, it verifies,
  and it expires to a tombstone (2 database tests).

Only observed (not established):

- That a matching header means the same replica or the newest data (design 18.4).
- That NULL-agency rows stay absent (0 citywide since 2026-09-01, 0 at the five lots today).
- That the allowlists cover current values: they are a dated snapshot.

## 5. Finding for review: the allowlists withhold most older wording

The allowlists come from the dictionary tab plus values seen at least 20 times among
rows created since 2026-09-01 (the step-1 window, design 18.2). Older rows often use
wording that is no longer current. On the 945 recorded rows, 659 have at least one
withheld value:

| Field | Rows withheld |
|---|---|
| `resolution_description` | 601 |
| `descriptor` | 101 |
| `descriptor_2` | 58 |
| `complaint_type` | 56 (39 are "Request Large Bulky Item Collection") |
| `agency` | 5 (DCA 3, OSE 2) |

This fails closed, as required, but makes many records less informative. **Decision
requested:** rebuild the allowlists with the frequency rule over the whole 2020 window
(grouped counts only, same privacy filters), or keep the recent window. The adapter does
not change until you decide; the replay test pins these numbers so any change is visible.

## 6. Gate results

| Check | Result |
|---|---|
| Offline suite (no database) | 938 passed, 230 skipped |
| Full suite with PostgreSQL 16 (`REQUIRE_DB_TESTS=1`) | 1135 passed, 33 skipped (the 33 are live tests) |
| PostgreSQL tests alone (`-m db`) | 207 passed |
| Live tests (`RUN_LIVE_TESTS=1 -m live`, all datasets) | 33 passed |
| 311 fixture replay and privacy scans | pass (10 replay tests; global fixture scan unchanged) |
| Token and instability unit tests | pass (18 token, 4 pager, D27 cases in `test_sr311.py`) |
| HPD, DOB complaint and ECB regressions | pass, unchanged |
| Lint and format (ruff) | clean |
| Phase 1 benchmark | 14/14 correct, 0 wrong building, 6/6, 4/4, 2/2 (unchanged) |

The one warning in each run is the existing Starlette test-client deprecation.

## 7. Limitations

1. **No storage yet.** 311 records are not saved to PostgreSQL (migration 0011, step 5).
   Only their raw evidence can be, under the token.
2. **Over-withholding** (section 5).
3. **Replay cannot show a successful retry** for 1134 and ACP: each retry gets the same
   recorded pair. Successful retries are tested on synthetic data.
4. **NULL-agency count** is unrecorded and so not established in replay.
5. **Allowlist window** spans two data versions (vocabulary queries in step 1), and its
   counts differ slightly from the 18.2 table (for example `descriptor` 263 rare values
   in 1,733 rows here, 264 in 1,714 there).
6. **Coverage**: rows without a BBL (12.0% citywide), NYCHA and DOC requests, and any
   unpublished complaint types are not found; "none found" never means "none made".
7. **Freshness** from catalog metadata is not attached; `data_as_of` from the header is.
8. Commit author is the environment's identity (`Claude <noreply@anthropic.com>`).
