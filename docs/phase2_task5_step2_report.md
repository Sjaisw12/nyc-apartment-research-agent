# Phase 2, Task 5, step 2: DOB complaints adapter (for review)

Status: **submitted for the step-2 gate.** Only DOB Complaints Received (`eabe-havv`) is
implemented. No ECB, 311, migration 0011, frontend or deployment work was done. Step 3
(ECB) waits for review and approval.

Still open, unchanged and separate:

- HPD Online independent validation, Parts A and B: **pending** (Sanyam's results).
- DOB, OATH and 311 validation, Parts C, D and E: **pending** (design 11.1). Nothing here
  has been checked against DOB BIS, DOB NOW or OATH.

## 1. Mandatory first correction (commit 366dc8b)

- `proposed_mappings.md` said "No order event means an order is currently in effect",
  and the mapping test asserted that sentence. It now says: "An order event never means
  that an order is currently in effect, and the absence of an order event does not mean
  that no order exists." The test asserts this and asserts the old sentence is absent.
- L1 ("Partial Stop Work Order") and K4 ("Cranes and Derricks – Stop Work Order (SWO) –
  No Associated Address") have no verb in DOB's text. Their labels changed from
  `stop_work_partial_issued` and `stop_work_issued_crane_no_address` to
  `stop_work_partial_recorded` and `stop_work_crane_no_address_recorded`. A new test
  requires that a label contains "issued" only when DOB's text says served, issued or
  previously issued.
- All counts unchanged (summary compared before and after).

## 2. What was built

| Item | Where |
|---|---|
| Typed models: complaint, order event, BIN evidence, summary, result | `backend/src/nyc_apartment_agent/records/dob_models.py` |
| Adapter | `backend/src/nyc_apartment_agent/records/dob_complaints.py` |
| New shared enum values (`bin_lot_mapping`, `via_bin_mapping`, 5 flags) | `records/models.py` (additions only) |
| Reference data (disposition mapping and category list) | `records/data/dob_complaints_reference.json`, built by `backend/scripts/task5_build_dob_reference.py` |
| DOB category list, extracted verbatim | `docs/task5/dob_complaint_categories_2018.json`, by `backend/scripts/task5_extract_dob_categories.py` |
| Tests | `tests/unit/test_dob_complaints.py` (121), `test_dob_complaints_cassette.py` (11), `test_dob_reference.py` (3), `tests/live/test_live_dob_complaints.py` (3) |
| Design notes for what was built | design section 18.7 |

## 3. Acceptance scenarios (recorded fixtures, replayed offline)

The test replays the committed recordings unchanged: resolver and HPD buildings from
`tests/fixtures/hpd/buildings_cassette.json` (2026-10-22), Footprints and DOB complaints
from `tests/fixtures/dob311/` (2026-10-23 04:51Z). The adapter builds exactly the
recorded URLs; any unrecorded request fails the test.

| Address | Result | This building | Other buildings on the lot |
|---|---|---|---|
| 1134 West Farms Rd | 12, records_found | 8 (BIN 2006185) | 4 (BIN 2117584, Footprints only, `bin_single_source`) |
| 220 W 17th St | 46, records_found | 17 (BIN 1081543) | 29: 1081547 19, 1081549 4, 1081545 3, 1081544/1081546/1081548 1 each |
| 1217 West Farms Rd | unsupported_join, no request of any kind | | |
| 350 5th Ave | 141, records_found | 141 (BIN 1015862), including 24 recorded as "338 5 AVENUE" | |
| 1845 ACP Blvd | 231, records_found | 111 (BIN 1054974) | 120 (BIN 1055002); lot family 1018227502 and base lot 1018220001 |

These match the draft attribution ground truth (rows 1, 2, 5, 6, 10, 14, 18, 19). Across
the 430 recorded complaints: 241 `no_violation`, 3 with no disposition, 2 `unknown_code`
(N1, no description), 22 order events (L2 14, L1 6, Y2 1, Y3 1), 4 ACTIVE.

## 4. Rules implemented

- **Scope.** Building scope only. Tax-lot scope returns `unsupported_join` before any
  request (no Footprints, HPD or DOB call). Unresolved addresses likewise.
- **BIN search set.** Resolved BIN, Footprints BINs on the lot, current HPD BINs on the
  lot. Placeholders and malformed BINs never searched. Footprints-only or HPD-only BIN:
  `bin_single_source`. HPD-only BIN that Footprints places on another lot:
  `bin_lot_conflict`, not searched. Resolved BIN with conflicting evidence: searched, but
  every row `unattributed_on_lot`, `conflicting`, `bin_lot_conflict`. Cap of 25 BINs
  (`bin_set_too_large`). If Footprints or HPD cannot be checked, only the resolved BIN is
  searched and the result is incomplete.
- **Attribution.** Resolved BIN `this_building` (basis `row_identifiers`); other lot BIN
  `other_building_on_lot` (basis `bin_lot_mapping`); lot membership `via_bin_mapping`.
  The models reject building attribution with a conflict flag or a differing duplicate,
  and reject the resolved BIN counted as another building.
- **Outcomes.** From the approved mapping, with DOB's text verbatim and its source
  (2021 PDF or 2017 table). The 19 unknown codes, and any code not in the mapping, stay
  `unknown_code` with no description and flag `disposition_code_unknown`.
- **Order events.** Kind, DOB's own action verb, DOB's text, date, complaint number,
  `still_in_effect = "not_shown_by_source"`, and fixed wording that never says the order
  is in effect. Listed in date order; issues and rescissions are never netted.
- **Status.** `active`/`closed`/`unknown` kept apart from the outcome. ACTIVE with a final
  outcome, or CLOSED with none, is flagged `status_fields_disagree` and counted; nothing is
  reconciled. (The 897 recorded citywide cases are not in the five-address fixtures; the
  synthetic tests cover X1 and X2.)
- **Dates.** DOB's `MM/DD/YYYY`; unparseable and implausible dates flagged with the raw
  text kept; a disposition or inspection before the received date flagged `date_order`.
- **Duplicates.** Identical copies collapse and keep every source row id; differing
  copies are all kept, flagged, and lose building attribution.
- **Privacy and evidence.** `dobrundate` is not public. The recorded address passes the
  unit and floor redactor. Keyed digests; the original row goes to the evidence store with
  its query URL, fetch time and data version. Logs carry no row contents.
- **Provenance.** The DOB fetch provenance, plus provenance for each Footprints and HPD
  lookup, the BIN evidence per BIN, the lot family and a coverage note.

## 5. Gate results

| Check | Result |
|---|---|
| Offline suite (no database) | 670 passed, 219 skipped |
| Full suite with PostgreSQL 16 (`REQUIRE_DB_TESTS=1`) | 863 passed, 26 skipped (the 26 are live tests) |
| PostgreSQL tests alone (`-m db`) | 203 passed |
| Live smoke (`RUN_LIVE_TESTS=1 -m live`, all datasets) | 26 passed |
| Fixture privacy scans (global and Task 5) | pass (in the suites above) |
| HPD regression tests (Tasks 1 to 4) | pass, unchanged (in the suites above) |
| Lint and format (ruff) | clean |
| Phase 1 benchmark | 14/14 correct, 0 wrong building, 6/6, 4/4, 2/2 (unchanged) |
| CI | see the project thread reply |

## 6. Limitations

1. **No storage yet.** DOB records are not saved to PostgreSQL. The new values
   (`bin_lot_mapping`, `via_bin_mapping`) are outside the 0004 CHECK constraints, so saving
   needs migration 0011 (step 5).
2. **Retired BINs** (for example 2010717 at 1217 West Farms) are not searched. Detecting
   them from ECB rows belongs to step 3; for now the result carries a fixed coverage note.
3. **Category descriptions.** DOB's attached list dates from 2018 and lacks 100 codes in
   use (15.2% of rows). Those show the raw code only.
4. **Freshness** from catalog metadata is not attached (the recordings have no catalog
   response); `data_as_of` from the truth header is.
5. **Mixed-time fixtures.** HPD building records come from the 2026-10-22 recording and
   DOB complaints from 2026-10-23. HPD's data was last updated 2026-08-12, so both see the
   same HPD rows.
6. The resolved-BIN conflict and HPD-only BIN cases have no real example at the five
   addresses; they are tested on synthetic rows.
7. Commit author is the environment's identity (`Claude <noreply@anthropic.com>`).
