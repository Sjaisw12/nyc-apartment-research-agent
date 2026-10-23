# Phase 2, Task 5, step 3: DOB ECB violations adapter (for review)

Status: **submitted for the step-3 gate.** Only DOB ECB Violations (`6bgk-3dad`) is
implemented, after corrections A and B to step 2. No migration 0011, 311, LangGraph,
frontend, PR, merge, deployment, history rewrite or branch deletion. Step 4 (311) waits
for separate approval.

Still open, unchanged and separate:

- HPD Online independent validation, Parts A and B: **pending** (Sanyam's results).
- DOB, OATH and 311 validation, Parts C, D and E: **pending** (design 11.1). No ECB
  summons has been checked against OATH or DOB BIS. The adjudication grouping is the
  project's and is unverified.

## 1. Corrections A and B to step 2 (commit d38ce53)

| Correction | Change | Test evidence |
|---|---|---|
| **A.** Identical duplicate rows keep independent evidence | Every source row is normalized, so each copy's original row goes to the evidence store with its own keyed digest, `:id`, query URL and fetch time; the collapsed complaint lists every copy in `source_rows` (`DobSourceRow`). The result model checks that every fetched row is listed exactly once. Nothing private is exposed; Step 5 can store one lineage link per row | `test_identical_duplicates_keep_independent_verifiable_evidence_for_every_row` reads the evidence store itself: two items, both verify, distinct `:id`s, page lineage present, and no raw row or extract time in the public record. Before the fix the store held 1 of 2 |
| **B.** No requested-building claims when the resolved BIN conflicts | `summarize(..., resolved_bin_conflict=True)` sets `building_specific_claims_allowed = false` with `building_claims_denied_reason`; the result model refuses the combination | `test_conflicting_resolved_bin_denies_requested_building_claims_in_the_summary`; `test_valid_building_results_still_allow_building_claims` keeps valid results unchanged |

Both pass in the gate below (3 of 3 selected, and in every suite run).

## 2. What was built

| Item | Where |
|---|---|
| Typed models: summons, infraction, source row, bucket, financials, row BIN, summary, result | `backend/src/nyc_apartment_agent/records/ecb_models.py` |
| Adapter | `backend/src/nyc_apartment_agent/records/ecb_violations.py` |
| New shared values: `enforcement_action`; 7 flags (additions only) | `records/models.py` |
| Reference data: adjudication mapping with notes, vocabularies, infraction allowlist | `records/data/ecb_reference.json`, built by `backend/scripts/task5_build_ecb_reference.py` |
| Section/law allowlist measurement (live, grouped counts only) | `backend/scripts/task5_measure_ecb_reference.py` -> `docs/task5/ecb_reference_measurements.json` |
| Tests | `tests/unit/test_ecb_violations.py` (113), `test_ecb_violations_cassette.py` (11), `test_ecb_reference.py` (4), `tests/db/test_ecb_evidence.py` (2), `tests/live/test_live_ecb_violations.py` (3) |
| Design notes for what was built | design section 18.8 |

## 3. Acceptance targets (recorded fixtures, replayed offline)

The test replays the committed recordings unchanged: resolver and HPD buildings from
`tests/fixtures/hpd/buildings_cassette.json`, Footprints and ECB from
`tests/fixtures/dob311/` (2026-10-23 04:51Z, data version "Thu, 22 Oct 2026 17:04:42
GMT"). The adapter rebuilds the five recorded `$where` clauses exactly; any unrecorded
request fails the test. No count was changed.

| Address | Target | Result | Attribution |
|---|---|---|---|
| 1134 West Farms Rd | 3 | 3, records_found | 3 `this_building` (BIN 2006185, lot 0005) |
| 220 W 17th St | 4 | 4, records_found | 1 `this_building` (1081543); 3 `other_building_on_lot` (1081547 ×2, 1081548) |
| 1217 West Farms Rd | 12 at lot scope | 12, records_found | 12 `lot_level`; query by lot only, no BIN clause |
| 350 5th Ave | 241 = 147 + 94 | 241, records_found | 241 `this_building`; 147 on lot `00041`, 94 on `0041`, each once |
| 1845 ACP Blvd | 52, 23 on the base lot | 52, records_found | 40 `this_building` (17 on 7502, 23 on base lot 0001 flagged `condo_lot_family`); 12 `other_building_on_lot` (1055002 on 7502, never reassigned) |

These match the draft ground truth rows 3, 7, 8, 11, 15, 16, 20, 21 and 22. No recorded
BIN needed a Footprints lookup by BIN, so `row_bins_not_on_lot` is empty at all five.
There was no source or fixture discrepancy to explain.

Across the 312 recorded summonses: correction status 307 resolved, 5 open; adjudication
253 upheld, 49 dismissed, 4 upheld_by_default (DEFAULT), 4 other (WRITTEN OFF),
2 pending; 115 with an undocumented `CLASS - n` severity; no status disagreement; one
`date_order`; nothing withheld beyond the always-withheld fields.

Requested-building financials as recorded (Decimal, not reconciled): 1134 West Farms
penalty 25000, paid 0, balance 25000; 220 W 17th 150 / 150 / 0; 350 5th 75720 / 79227 /
-3060 (6 negative balances, kept as recorded); 1845 ACP 35510 / 29452 / 6070 for the
40 requested-building summonses only. None at 1217 West Farms (tax lot).

## 4. Rules implemented

- **Query.** Each lot in the lot family in both lot formats, 5-digit block, OR'd; at
  building scope ` OR bin='R'`; ordered by `isn_dob_bis_extract`. Tax lot: lot only;
  the model refuses a BIN clause. Values are checked as BBLs or BINs before quoting.
- **Lot family.** Building scope: the DOB complaint BIN evidence (Footprints by lot, HPD,
  Footprints by BIN). Tax lot: Footprints by lot only. A failed Footprints lot lookup
  makes the result incomplete (`lot_family_incomplete`).
- **Attribution.** The decision table in 18.8: resolved BIN, other corroborated BIN
  (with `bin_single_source` when one source), retired or unknown BIN, BIN on another lot,
  two-source conflict (on R and on another BIN), missing or placeholder BIN, row lot
  outside the family, missing lot with R, condo base lot, tax lot.
- **Retired and unknown BINs.** Looked up in Footprints by BIN (one query, in provenance)
  at building scope only; listed in `row_bins_not_on_lot`; never promoted and never
  added to the DOB complaint search. The DOB coverage note stays.
- **Duplicates.** One query; `matched_by` keeps which clause matched. Collapse only
  identical copies of the same extract id; never collapse distinct summonses.
- **Statuses.** Correction, adjudication and certification are separate fields with raw
  values. Adjudication uses the approved mapping and carries a note; DEFAULT, POP/IN-VIO
  and WRITTEN OFF say their meaning is not documented. Disagreements flagged, not
  reconciled. A statement on every result says a summons is a charge, not a finding.
- **Money.** Decimal; unparseable amounts flagged and excluded from totals; financials
  only for `this_building` and only when building claims are allowed.
- **Privacy.** Respondent fields, `violation_description` and `hearing_time` never
  public. Picklists shown only if on the measured list; section/law text only for an
  allowlisted pair; identifiers format-checked. Sentinel names, units, floors, emails,
  phone numbers and personal details placed in every text field never reach records,
  result JSON, request URLs or logs, and are present in `original_row` only. Raw rows go
  to the evidence store with keyed digests; in PostgreSQL they get the 180-day retention
  and expire to tombstones (2 database tests).
- **Provenance.** ECB fetch with pages, truth header, count and restarts (up to 2, per
  18.4); Footprints and HPD lookups; lot family; lot formats queried; coverage notes.
  Outages give `source_unavailable`; partial pages, short pages, changed versions and row
  caps give `incomplete_pagination` with rows kept and marked; a missing required field
  gives `schema_changed`.

## 5. Gate results

| Check | Result |
|---|---|
| Offline suite (no database) | 801 passed, 224 skipped |
| Full suite with PostgreSQL 16 (`REQUIRE_DB_TESTS=1`) | 996 passed, 29 skipped (the 29 are live tests) |
| PostgreSQL tests alone (`-m db`) | 205 passed |
| Live tests (`RUN_LIVE_TESTS=1 -m live`, all datasets) | 29 passed |
| Fixture replay and privacy scans | pass (in the suites) |
| HPD and DOB complaint regressions | pass, unchanged (in the suites) |
| Lint and format (ruff) | clean |
| Phase 1 benchmark | 14/14 correct, 0 wrong building, 6/6, 4/4, 2/2 (unchanged) |

## 6. Limitations

1. **No storage yet.** ECB records are not saved to PostgreSQL (migration 0011, step 5).
   Only their raw evidence can be.
2. **Adjudication is unverified.** The grouping is ours; DEFAULT, POP and WRITTEN OFF are
   undocumented. Validation part D (OATH `jz4z-kudi` cross-check) is pending.
3. **Retired BIN example.** The measured retired BIN at 1217 West Farms (2010717) does not
   appear on any recorded ECB row; the retired, other-lot and conflict cases are tested
   on synthetic rows only. At tax-lot scope unknown BINs are flagged but not looked up.
4. **Historical lots.** ACP BIN 1055002's 28 rows on lot 0061 are not fetched, by design
   (not R, lot outside the family). They remain invisible to this query.
5. **Block format.** Only the 5-digit block is queried, as measured; the one recorded
   4-digit block row in the dataset would be missed if it were on a test lot.
6. **Section/law allowlist** is a snapshot (2026-10-23). A new code or a changed text is
   withheld until the list is rebuilt. Five "FLOOR NUMBERING SIGNS" texts are withheld
   because the floor pattern matches; this over-withholds and is accepted.
7. **Negative balances** are shown as recorded; their meaning is not documented.
8. **Freshness** from catalog metadata is not attached (the recordings have no catalog
   response); `data_as_of` from the truth header is.
9. Commit author is the environment's identity (`Claude <noreply@anthropic.com>`).
