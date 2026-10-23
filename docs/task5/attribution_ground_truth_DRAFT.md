# Task 5 attribution ground truth (DRAFT, not frozen)

Status: **draft for Sanyam's review. Not frozen.** It becomes the step-2 to step-4
reference only after approval, and after that it is never edited to improve results.

Built 2026-10-23 from the step-1 fixtures (`backend/tests/fixtures/dob311/`, recorded
04:52Z) and the measurements (`docs/task5/step1_measurements.json`, 04:39Z). Expected
values follow the rules in design sections 6 and 18.3. Agency lookups (Parts C and D)
have not been done, so nothing here is verified against DOB BIS, DOB NOW or OATH yet.

Evidence labels:

- **Observed:** the sources named agree, and the expectation follows directly.
- **Uncertain:** the expectation depends on a rule not yet approved, or on a source
  that is silent.
- **Conflicting:** two sources disagree about where the BIN or row belongs.

## Lot and BIN evidence per address

| Address | Resolved BBL / BIN | Scope | Footprints BINs (lot) | HPD BINs | Lot family | Notes |
|---|---|---|---|---|---|---|
| 1134 West Farms Rd | 2027540005 / 2006185 | building | 2006185, 2117584 (both `base_bbl` = `mappluto_bbl` = 2027540005) | 2006185 | 2027540005 | 2117584 is in Footprints only (HPD silent) |
| 220 W 17th St | 1007660055 / 1081543 | building | 1081542 to 1081549 | same 8 | 1007660055 | all sources agree |
| 1217 West Farms Rd | 2029930031 / none | tax lot | 2128717 | 2128717 | 2029930031 | retired BIN 2010717 appears in BIS violations and one DOB complaint |
| 350 5th Ave | 1008350041 / 1015862 | building | 1015862 | 1015862 | 1008350041 | ECB lot recorded in two formats |
| 1845 ACP Blvd | 1018227502 / 1054974 | building | 1054974, 1055002 (`base_bbl` 1018220001, `mappluto_bbl` 1018227502) | 1054974, 1055002 (lot 7502) | 1018227502, 1018220001 | condo: billing lot 7502, base lot 0001 |

## Expected attribution

| # | Address | Dataset | Rows | Row identifiers | Expected attribution | Basis / membership | Flags | Evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | 1134 WF | DOB complaints | 8 | BIN 2006185 | `this_building` | row_identifiers / via_bin_mapping | | Observed |
| 2 | 1134 WF | DOB complaints | 4 | BIN 2117584 | `other_building_on_lot` | bin_lot_mapping / via_bin_mapping | `bin_single_source` | Uncertain (Footprints only; HPD silent) |
| 3 | 1134 WF | ECB | 3 | BIN 2006185, lot 0005 | `this_building` | row_identifiers / confirmed | | Observed |
| 4 | 1134 WF | 311 | 89 | BBL 2027540005 | `unattributed_on_lot`; fetch `incomplete_pagination` in the recording | row_identifiers / confirmed | | Observed |
| 5 | 220 W 17 | DOB complaints | 17 | BIN 1081543 | `this_building` | row_identifiers / via_bin_mapping | | Observed |
| 6 | 220 W 17 | DOB complaints | 29 | BINs 1081544 to 1081549 (1081547: 19, 1081549: 4, 1081545: 3, three others: 1 each) | `other_building_on_lot` | bin_lot_mapping / via_bin_mapping | | Observed |
| 7 | 220 W 17 | ECB | 1 | BIN 1081543, lot 0055 | `this_building` | row_identifiers / confirmed | | Observed |
| 8 | 220 W 17 | ECB | 3 | BINs 1081547 (2), 1081548 (1), lot 0055 | `other_building_on_lot` | row_identifiers / confirmed | | Observed |
| 9 | 220 W 17 | 311 | 89 | BBL 1007660055 | `unattributed_on_lot` | row_identifiers / confirmed | | Observed |
| 10 | 1217 WF | DOB complaints | n/a | | `unsupported_join` (no request) | | | Observed (Phase 1 rule) |
| 11 | 1217 WF | ECB | 12 | BIN 2128717, lot 0031 | `lot_level` | row_identifiers / confirmed | | Observed |
| 12 | 1217 WF | 311 | 32 | BBL 2029930031 | `lot_level` | row_identifiers / confirmed | | Observed |
| 13 | 1217 WF | DOB complaints (coverage) | 1 (not searched) | retired BIN 2010717 | coverage note only | | | Uncertain (BIN in no current source) |
| 14 | 350 5th | DOB complaints | 141 | BIN 1015862 | `this_building` | row_identifiers / via_bin_mapping | | Observed |
| 15 | 350 5th | ECB | 147 | BIN 1015862, lot 00041 | `this_building` | row_identifiers / confirmed | | Observed |
| 16 | 350 5th | ECB | 94 | BIN 1015862, lot 0041 | `this_building` | row_identifiers / confirmed | | Observed |
| 17 | 350 5th | 311 | 485 | BBL 1008350041 | `unattributed_on_lot` | row_identifiers / confirmed | | Observed |
| 18 | 1845 ACP | DOB complaints | 111 | BIN 1054974 | `this_building` | row_identifiers / via_bin_mapping | | Observed |
| 19 | 1845 ACP | DOB complaints | 120 | BIN 1055002 (HPD: 1851 ACP Blvd) | `other_building_on_lot` | bin_lot_mapping / via_bin_mapping | | Observed |
| 20 | 1845 ACP | ECB | 17 | BIN 1054974, lot 7502 | `this_building` | row_identifiers / confirmed | | Observed |
| 21 | 1845 ACP | ECB | 23 | BIN 1054974, lot 00001 (15) and 0001 (8) | `this_building` | row_identifiers / confirmed | `condo_lot_family` | Uncertain (depends on the condo lot-family rule, 18.3) |
| 22 | 1845 ACP | ECB | 12 | BIN 1055002, lot 7502 | `other_building_on_lot` | row_identifiers / confirmed | | Observed |
| 23 | 1845 ACP | ECB (not fetched) | 28 | BIN 1055002, lot 0061 / 00061, issued 1990 to 2009 | not retrieved (BIN is not R; lot outside the family) | | | Conflicting (row lot 0061 vs Footprints and HPD; lot 0061 no longer exists in either) |
| 24 | 1845 ACP | 311 | 250 | BBL 1018227502 | `unattributed_on_lot`; fetch `incomplete_pagination` in the recording | row_identifiers / confirmed | | Observed; no 311 rows on base lot 1018220001 |

## Excluded 311 rows (source counts, not retrieved)

From the committed recording (04:52Z).

| Address | HPD | DOB | Count-query header | Main 311 fetch | Established under 18.4? |
|---|---|---|---|---|---|
| 1134 WF | 309 | 1 | Thu 22 Oct 01:37:36 | incomplete (Thu count, Fri page) | no |
| 220 W 17 | 47 | 26 | Thu 22 Oct 01:37:36 | complete, Fri 23 Oct 01:38:29 | no (headers differ) |
| 1217 WF | 44 | 1 | Thu 22 Oct 01:37:36 | complete, Fri 23 Oct 01:38:29 | no (headers differ) |
| 350 5th | 8 | 11 | Thu 22 Oct 01:37:36 | complete, Fri 23 Oct 01:38:29 | no (headers differ) |
| 1845 ACP | 173 | 12 | Thu 22 Oct 01:37:36 | incomplete (Thu count, Fri page) | no |

In this recording no address would get established excluded counts. The adapter will
need the retry rule in 18.4, and the report must show these counts as "not established
for this snapshot".

## Cases with no real example (synthetic rows needed)

- two sources placing one BIN on different lots, for R and for another BIN;
- an ECB row on the lot whose BIN Footprints places on another lot;
- placeholder or missing BIN on an ECB row on the lot;
- an ECB row with a null lot and BIN = R.
