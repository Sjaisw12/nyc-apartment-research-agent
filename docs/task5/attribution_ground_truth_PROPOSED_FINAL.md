# Task 5 attribution ground truth (PROPOSED FINAL, not frozen)

Status: **proposed for Sanyam's review. Not frozen and not approved.** It replaces nothing
until approved. `attribution_ground_truth_DRAFT.md` is kept unchanged as the record of
what was proposed before any independent evidence existed.

Independent evidence used (Step 6, 2026-10-23):

- **Part D:** OATH Hearings Division Case Status (`jz4z-kudi`), data version Thu 22 Oct
  2026 04:46:54. Matching is by public summons number, and OATH's block and lot are
  compared with DOB's. OATH has no BIN, so it can confirm a lot but never a building.
  See `validation/part_d_oath_crosscheck.md`.
- **Part E:** an independent 311 query (its own Footprints lot lookup and grouped SoQL),
  data version Fri 23 Oct 2026 01:38:29. It is compared with a live adapter run and with
  the recording, and HPD-routed keys are matched to HPD complaints `ygpa-z7cr` (counts
  only). See `validation/part_e_311_aggregates.md`.
- **Part C (DOB BIS / DOB NOW):** **pending.** No DOB complaint attribution has
  independent evidence yet. Those rows keep their draft label.

Evidence labels:

- **Observed:** sources agree.
- **Observed + independent:** also supported by Part D or E.
- **Uncertain:** depends on a rule or a silent source.
- **Conflicting:** sources disagree.
- **Pending:** waiting on manual evidence.

No expected attribution changed. No disagreement traced to an implementation error.

## Expected attribution

| # | Address | Dataset | Rows | Expected attribution | Draft evidence | Proposed evidence | Independent evidence |
|---|---|---|---|---|---|---|---|
| 1 | 1134 WF | DOB complaints | 8 (BIN 2006185) | `this_building` | Observed | Observed; Part C pending | none yet |
| 2 | 1134 WF | DOB complaints | 4 (BIN 2117584) | `other_building_on_lot` (`bin_single_source`) | Uncertain | **Uncertain** (unchanged) | none; Footprints only, HPD silent; Part C pending |
| 3 | 1134 WF | ECB | 3 (BIN 2006185, lot 0005) | `this_building` | Observed | Observed + independent (lot) | 3/3 in OATH, block 02754 lot 00005 agrees |
| 4 | 1134 WF | 311 | 89 (BBL 2027540005) | `unattributed_on_lot`; recording `incomplete_pagination` | Observed | Observed + independent (count) | independent count 89 = live adapter 89 = recording 89; live fetch now complete; recording stays incomplete |
| 5 | 220 W 17 | DOB complaints | 17 (BIN 1081543) | `this_building` | Observed | Observed; Part C pending | none yet |
| 6 | 220 W 17 | DOB complaints | 29 (BINs 1081544-49) | `other_building_on_lot` | Observed | Observed; Part C pending | none yet |
| 7 | 220 W 17 | ECB | 1 (BIN 1081543, lot 0055) | `this_building` | Observed | Observed + independent (lot only) | 1/1 in OATH, lot agrees; BIN not checkable in OATH |
| 8 | 220 W 17 | ECB | 3 (BINs 1081547, 1081548) | `other_building_on_lot` | Observed | Observed + independent (lot only) | 3/3 in OATH, lot agrees |
| 9 | 220 W 17 | 311 | 89 (BBL 1007660055) | `unattributed_on_lot` | Observed | Observed + independent (count) | 89 = 89 = 89. HPD-routed 311 keys found in HPD complaints spread over BINs 1081543 (11), 1081546 (5) and 1081544 (1), plus 29 HPD rows with no BBL, which supports never attributing 311 to one building |
| 10 | 1217 WF | DOB complaints | n/a | `unsupported_join` | Observed | Observed | none needed |
| 11 | 1217 WF | ECB | 12 (BIN 2128717, lot 0031) | `lot_level` | Observed | Observed + independent (lot) | 12/12 in OATH, lot 00031 agrees |
| 12 | 1217 WF | 311 | 32 (BBL 2029930031) | `lot_level` | Observed | Observed + independent (count) | 32 = 32 = 32; HPD key match 44/44 |
| 13 | 1217 WF | DOB complaints (coverage) | 1 (retired BIN 2010717) | coverage note only | Uncertain | **Uncertain** (unchanged) | none |
| 14 | 350 5th | DOB complaints | 141 (BIN 1015862) | `this_building` | Observed | Observed; Part C pending | none yet |
| 15 | 350 5th | ECB | 147 (lot 00041) | `this_building` | Observed | Observed + independent (lot) for 68; **source unavailable** for 79 | 68 in OATH, lot agrees; 79 not in OATH, all with hearings before 2000 |
| 16 | 350 5th | ECB | 94 (lot 0041) | `this_building` | Observed | Observed + independent (lot) for 93; source unavailable for 1 | 93 in OATH, lot agrees; 1 not in OATH (pre-2000) |
| 17 | 350 5th | 311 | 485 (BBL 1008350041) | `unattributed_on_lot` | Observed | Observed + independent (count) | 485 = 485 = 485; HPD key match 8/8 |
| 18 | 1845 ACP | DOB complaints | 111 (BIN 1054974) | `this_building` | Observed | Observed; Part C pending | none yet |
| 19 | 1845 ACP | DOB complaints | 120 (BIN 1055002) | `other_building_on_lot` | Observed | Observed; Part C pending | none yet |
| 20 | 1845 ACP | ECB | 17 (BIN 1054974, lot 7502) | `this_building` | Observed | Observed + independent (lot family) | 17/17 in OATH: 10 on lot 7502, 7 on base lot 0001 (same condo family) |
| 21 | 1845 ACP | ECB | 23 (BIN 1054974, base lot 0001) | `this_building` (`condo_lot_family`) | Uncertain | **Uncertain** (unchanged; rule 18.3 still needs approval) | 14 in OATH, all on lot 0001 (agrees with DOB); 9 not in OATH (pre-2000) |
| 22 | 1845 ACP | ECB | 12 (BIN 1055002, lot 7502) | `other_building_on_lot` | Observed | Observed; **historical lot identity** noted | 12/12 in OATH: 9 on lot 7502, 3 on retired lot 0061 |
| 23 | 1845 ACP | ECB (not fetched) | 28 (BIN 1055002, lot 0061) | not retrieved | Conflicting | **Conflicting** (unchanged) | OATH also uses lot 0061 for BIN 1055002 summonses, so the retired lot is real history, not a typo |
| 24 | 1845 ACP | 311 | 250 (BBL 1018227502) | `unattributed_on_lot`; recording `incomplete_pagination` | Observed | Observed + independent (count) | 250 = 250 = 250; live fetch now complete; HPD key match 173/173, which HPD places on BIN 1054974 (138) and BIN 1055002 (35), so a lot-level 311 row can belong to either building |

## Excluded 311 counts (HPD- and DOB-routed)

| Address | Recorded (observed, not established) | Live adapter, now (established) | Independent query, now |
|---|---|---|---|
| 1134 WF | 309 / 1 | 309 / 1 | 309 / 1 |
| 220 W 17 | 47 / 26 | 47 / 29 | 47 / 29 |
| 1217 WF | 44 / 1 | 44 / 1 | 44 / 1 |
| 350 5th | 8 / 11 | 8 / 11 | 8 / 11 |
| 1845 ACP | 173 / 12 | 173 / 12 | 173 / 12 |

At 220 W 17 the DOB-routed count was 26 on the Thu 22 Oct version and is 29 on the Fri
23 Oct version, the version of the recorded main fetch. This is why the recording could
not establish the count. NULL-agency rows are 0 at all five addresses now. The recorded
values are not changed.

## Disagreements and classification

| Where | What | Classification | Effect on the table |
|---|---|---|---|
| ACP rows 20 and 21 | OATH lot 0001 where DOB says 7502, for BIN 1054974 | historical lot identity (condo base vs billing lot) | none; both lots are in the condo family |
| ACP row 22 | OATH lot 0061 for 3 summonses DOB records on lot 7502 | historical lot identity (lot 0061 retired) | none; supports row 23 staying Conflicting |
| 350 5th rows 15 and 16, ACP row 21 | 89 summonses not in OATH, all with hearings 1988 to 1999 | source unavailable (OATH coverage) | none; reported, not excluded |
| 220 W 17 row 9 | 29 HPD complaint rows for HPD-routed 311 keys have no BBL | source freshness or completeness (HPD side) | none |
| 1134 WF row 4 | 15 of 309 HPD-routed keys not found in HPD complaints | missing evidence (HPD side) | none |
| Outcome wording | OATH STIPULATED vs DOB STIPULATION/IN-VIO (33); ADMIT/IN-VIO with blank OATH result (2) | mapping, not attribution | none for attribution; listed in the Step 6 report |

## Change log (draft → proposed final)

1. Rows 3, 4, 7, 8, 9, 11, 12, 15, 16, 17, 20, 24: evidence raised to "Observed +
   independent" with the Part D or Part E finding. The attribution is unchanged.
2. Rows 15, 16, 21: the pre-2000 summonses missing from OATH are marked "source
   unavailable", not "disagree".
3. Row 22: historical lot 0061 noted. The attribution is unchanged.
4. Row 23: OATH's use of lot 0061 added as evidence. It stays Conflicting.
5. Rows 1, 5, 6, 14, 18, 19 (DOB complaints): marked Part C pending. No change.
6. Rows 2, 13, 21 stay Uncertain. No uncertain row was promoted.
7. Excluded-count table: live and independent values added beside the recorded ones,
   which are unchanged.

Nothing is frozen. Freezing needs Sanyam's approval after Part C evidence.
