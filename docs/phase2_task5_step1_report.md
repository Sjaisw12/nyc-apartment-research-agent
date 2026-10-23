# Phase 2, Task 5, step 1: measurements, fixtures and amendments (for review)

Status: **submitted for the step-1 review gate.** No Task 5 adapter, production model or
migration was written. Step 2 (DOB complaints adapter) waits for separate approval.

Still open, unchanged and separate:

- HPD Online independent validation, Parts A and B: **pending** (Sanyam's results).
- DOB, OATH and 311 validation, Parts C, D and E: **future obligations** (design 11.1).

## 1. What was done

| Item | Where |
|---|---|
| Four amendments, and revised D19, D20, D21, D24, D27, D31 | design section 18 (`docs/phase2_task5_dob_311_design.md`) |
| Corrected ECB counts at the test addresses | design 3.2 (correction note) |
| Measurement script (read-only SoQL, aggregates only) | `backend/scripts/task5_measure.py` |
| Measurements, with every query, its truth header and time | `docs/task5/step1_measurements.json` |
| Fixture recorder and sanitizer | `backend/scripts/record_task5_fixtures.py` |
| Sanitized cassettes for the five addresses, plus their metadata | `backend/tests/fixtures/dob311/` |
| Guard test for the committed fixtures | `backend/tests/test_task5_fixture_privacy.py` |
| Proposed DOB disposition and ECB adjudication mappings | `docs/task5/proposed_mappings.md` and `.json`, built by `backend/scripts/task5_mappings.py` |
| Draft attribution ground truth | `docs/task5/attribution_ground_truth_DRAFT.md` |

## 2. Source versions

Measurements ran 2026-10-23 04:39:00 to 04:41:22 UTC; the committed fixtures were recorded
at 04:51:59 UTC.
Truth headers (`X-SODA2-Truth-Last-Modified`) seen:

| Dataset | Header(s) | Queries |
|---|---|---|
| `eabe-havv` DOB complaints | Thu, 22 Oct 2026 16:12:18 GMT | 11 |
| `6bgk-3dad` DOB ECB | Thu, 22 Oct 2026 17:04:42 GMT | 27 |
| `erm2-nwe9` 311 | **two:** Fri, 23 Oct 2026 01:38:29 GMT (22) and Thu, 22 Oct 2026 01:37:36 GMT (16) | 38 |
| `5zhs-2jue` Footprints | Mon, 19 Oct 2026 18:36:49 GMT | 10 |
| `kj4p-ruqc` HPD buildings | Wed, 12 Aug 2026 14:57:58 GMT | 5 |
| `6v9u-ndjg` disposition codes | Thu, 08 Mar 2018 18:27:57 GMT | 1 |

**Caveat:** 311 aggregates in this report mix the two 311 versions, query by query. The
query log records which version answered each query.

## 3. Measured tables

### 3.1 ECB hearing and certification status (all 1,841,577 rows)

| hearing_status | Rows | Proposed adjudication |
|---|---|---|
| IN VIOLATION | 683,127 | upheld |
| DISMISSED | 299,625 | dismissed |
| WRITTEN OFF | 246,116 | other |
| CURED/IN-VIO | 215,187 | upheld |
| DEFAULT | 165,027 | upheld_by_default (meaning not documented) |
| STIPULATION/IN-VIO | 92,246 | upheld |
| POP/IN-VIO | 57,993 | upheld (prefix meaning not documented) |
| ADMIT/IN-VIO | 38,205 | upheld |
| PENDING | 23,600 | pending |
| null | 20,451 | unknown |

| certification_status | Rows |
|---|---|
| CERTIFICATE ACCEPTED | 1,006,515 |
| N/A - DISMISSED | 278,124 |
| CURE ACCEPTED | 215,177 |
| NO COMPLIANCE RECORDED | 167,621 |
| null | 111,551 |
| COMPLIANCE-INSP/DOC | 58,221 |
| CERTIFICATE DISAPPROVED | 2,692 |
| CERTIFICATE PENDING | 985 |
| REINSPECTION SHOWS VIOLATION GOOD | 657 |
| REINSPECTION SHOWS STILL IN VIOLATION | 34 |

- `ecb_violation_status`: ACTIVE 281,197; RESOLVE 1,560,377; Unknown 3.
- Severity: CLASS - 1 496,420; CLASS - 2 621,813; CLASS - 3 55,391; Hazardous 205,452;
  Non-Hazardous 458,994; Unknown 3,507. The dictionary lists only the last three.
- Status disagreement (design 4.3): ACTIVE with DISMISSED: **0**; RESOLVE with PENDING:
  **472**.
- Neither the ECB dictionary nor the OATH dataset dictionary (`jz4z-kudi`, downloaded and
  checked) defines the hearing-status values. The mapping is therefore ours, based on the
  "IN-VIO" token, and DEFAULT is kept as its own value.

### 3.2 DOB disposition codes

- 160 distinct codes in use. **39 of them (25,956 rows) are not in the reference table
  `6v9u-ndjg`**, last updated in 2017 per its metadata (data version header 8 March
  2018). The largest: N1 (2,131), J6
  (1,874), AF (3,744), AB (6,283), AA (3,692), H7 (1,679), N2 (1,078), X1 (924).
- DOB's own PDF list (`www1.nyc.gov/assets/buildings/pdf/bis_complaint_disposition_codes.pdf`,
  named in the dataset description) **could not be reached** from this environment (the
  proxy refused the connection, and a fetch attempt failed on DNS). These 39 codes map to
  `unknown_code` until their definitions are obtained.
- Rows by proposed outcome: no_violation 1,230,994; violation_served 665,123; no_access
  511,082; other_recorded 286,086; order_event 213,649; unsubstantiated 82,367;
  violation_prepared_not_served 74,607; referred 39,271; unknown_code 25,956; none 9,443;
  assigned_or_pending 7,361.
- Status disagreement: ACTIVE with a final disposition: **1**; CLOSED with no disposition:
  **1**; ACTIVE with no disposition: 9,442 (expected: not yet dispositioned).
- **Every complaint whose disposition is an order code is CLOSED** (for example A3 full
  stop-work order served: 10,556 rows, all CLOSED; Y1 full vacate issued: 2,802, all
  CLOSED). The complaint status therefore says nothing about whether an order is in
  effect, which supports D25.
- A new outcome, `violation_prepared_not_served` (B1, B2: "prepared & attempt to serve
  will be made"), was split out of `violation_served`.

The full code-by-code table, with DOB's verbatim definitions and per-status counts, is
in `docs/task5/proposed_mappings.md`.

### 3.3 311 status (created since 2026-09-01)

| status | Rows | closed_date missing |
|---|---|---|
| Closed | 473,973 | 5 |
| Open | 40,453 | 33,874 |
| In Progress | 39,913 | 38,494 |
| Assigned | 1,871 | 601 |
| Pending | 165 | 142 |
| Unspecified | 49 | 6 |

"Canceled" (named in the dictionary) and "Started" did not occur in this window. The
proposed mapping is `open` for Open, In Progress, Assigned and Pending; `closed`;
`canceled`; `unknown` for Unspecified or anything else. 5,463 Open rows have a closed
date (flag `status_fields_disagree`).

### 3.4 ECB identifier formats

| Field | Lengths |
|---|---|
| `boro` | always 1 character; values 1 to 5, plus one row with "6" |
| `block` | 5 characters: 1,814,309; 4 characters: 1; null: 27,267; no non-numeric block found |
| `lot` | 4 characters: 1,494,206; 5 characters: 320,102; 2 characters: 1; null: 27,268 |

V1 queries the lot in both the 4-digit and 5-digit form and the block in its 5-digit
form.

### 3.5 DOB complaint-number duplicates

6 complaint numbers appear twice (12 rows) in 3,145,939. Of the 6, 4 are identical copies
and 2 differ (fields that differ: disposition and inspection dates, disposition code, BIN,
address fields, entry date).

### 3.6 Condo billing and unit lots

- 1845 ACP: Footprints gives `base_bbl` 1018220001 and `mappluto_bbl` 1018227502 for both
  BINs. HPD records both BINs on lot 7502.
- ECB uses both: BIN 1054974 has 17 rows on lot 7502 and 23 on lot 0001 (15 in the
  five-digit form, 8 in the four-digit form). BIN 1055002 has 12 rows on lot 7502, plus 28
  rows (1990 to 2009) on lot 0061, which no longer exists in Footprints or HPD.
- 311 uses the billing lot only at this address: 435 rows on 1018227502, none on
  1018220001. Other billing lots on the test blocks (1007667502 to 7504, 1008357501) are
  other condos, not tied to the test BINs by Footprints.

### 3.7 Truth-header behaviour and count discrepancies

- 10 identical citywide counts per dataset: DOB complaints, 1 header and 1 count; ECB, 1
  and 1; **311, 2 headers and 2 counts (22,879,856 and 22,892,475)**.
- 10 count-then-data pairs at 350 5th (311 since 2020): headers differed in 4 pairs;
  count = rows (504) in all 10.
- Fixture recording, first run (04:42Z, discarded, see section 4): the existing pager
  restarted twice on a 311 header change; all fetches ended complete, but different
  addresses were answered by different versions.
- Fixture recording, committed run (04:52Z): the 311 fetches for **1134 West Farms and
  1845 ACP ended `dataset_changed_during_retrieval` even after the pager's one restart**
  (count from the Thursday version, page from the Friday version). They are kept as
  recorded examples of the instability. Every excluded-row count query was answered by
  the Thursday version, while three of the main fetches were answered by the Friday one.

### 3.8 Per-address counts with the planned queries

| Address | DOB complaints | ECB | 311 kept | 311 excluded (HPD / DOB) |
|---|---|---|---|---|
| 1134 West Farms | 12 (2006185: 8, 2117584: 4) | 3 | 89 | 309 / 1 |
| 220 W 17th | 46 (1081543: 17, others 29) | 4 | 89 | 47 / 26 |
| 1217 West Farms | `unsupported_join` (tax lot) | 12 | 32 | 44 / 1 |
| 350 5th Ave | 141 | 241 (147 + 94) | 485 | 8 / 11 |
| 1845 ACP | 231 (1054974: 111, 1055002: 120) | 52 | 250 | 173 / 12 |

## 4. Fixture provenance

- Recorded with `backend/scripts/record_task5_fixtures.py` at 2026-10-23 04:51:59 UTC, through
  the existing pager (`fetch_all`, page size 1000) and `RecordingTransport`. The queries
  are the planned V1 queries (design 8.2 and 18.4), including the Footprints and HPD
  lookups that build the BIN set and lot family.
- 53 responses in five cassettes: `dob_complaints`, `dob_ecb`, `sr311`, `footprints`,
  `hpd_buildings`. Each cassette carries its own SHA-256. Each sanitized entry keeps the
  SHA-256 of the original body and per-row events (field names only).
- No resolver responses are included: the step-2 to step-4 tests will combine these with
  the existing resolver cassettes, or re-record with the same sanitizer if the adapter
  URLs differ (any difference reported).
- Rows: DOB complaints 430, ECB 312, 311 945.
- A first recording (04:42Z) put the pseudonyms into `unique_key`. The existing global
  fixture scan (`tests/unit/test_fixture_privacy.py`) requires every committed
  `unique_key` to be `[WITHHELD]`, and failed. That guard was kept as it is. The recorder
  now writes `unique_key: "[WITHHELD]"` and puts the pseudonym in a separate
  `fixture_row_ref`, and the fixtures were re-recorded. A later replay test can restore
  distinct keys in memory from `fixture_row_ref`.

## 5. Privacy review

| Check | Result |
|---|---|
| 311 `unique_key` in fixtures | `[WITHHELD]` (existing global scan passes); pseudonyms FX000001 to FX000945 in `fixture_row_ref`, numbered in fetch order and not derived from the key; tested |
| Socrata `:id` for 311 | never selected; no 311 URL names a key or row id; tested |
| 311 address as typed, streets, landmark, coordinates, location, channel | `[WITHHELD]` in fixtures; tested |
| 311 times | date kept, time removed in fixtures; tested |
| 311 resolution text | no unit or floor detail left (redactor re-run in the test) |
| ECB respondent name and address | `[WITHHELD]`; tested |
| ECB `violation_description` | `[WITHHELD]` in fixtures; proposed withheld in public output (design 18.2), because at least one recent row names a person |
| DOB complaints | no personal fields; unchanged |
| Measurement output | counts, coded vocabularies, and values seen at least 20 times only; no free text, keys, names or coordinates |
| Re-identification | a public record or fixture row can often be matched back to the public open-data row by lot, date and type. The fixtures and records contain nothing beyond what the open dataset publishes, minus identifiers, location detail and exact times |
| PostgreSQL roles and privacy rules | unchanged; no migration written |

Residual risk: free-text and picklist classification rests on 5,000-row samples and
September to October 2026 vocabularies. The allowlist rule (18.2) withholds anything not
seen often enough, so a new free-text value fails closed.

## 6. What remains unknown or inaccessible

1. **Definitions of the 39 DOB disposition codes** absent from `6v9u-ndjg`: the DOB PDF on
   nyc.gov is not reachable from here. They stay `unknown_code`. Sanyam could download
   the PDF from a browser and add it to the project.
2. **Meaning of ECB hearing statuses** (DEFAULT, POP, WRITTEN OFF, and others): not
   documented in either dictionary. Part D (the OATH cross-check) may help.
3. **Whether DOB NOW complaints all reach `eabe-havv`**: not verified; Part C will test it.
4. **Meaning of the 311 dictionary's "Public View" column**: not verified.
5. **311 time zone**: not verified; public output keeps the date only.
6. **Whether the two 311 versions come from two replicas**: inferred from the headers and
   counts, not confirmed by the publisher.
7. The 311 "updates" page and the DOB BIS and DOB NOW sites were not opened.

## 7. CI and commits

Filled in after the push (see the reply in the project thread).
