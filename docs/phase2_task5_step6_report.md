# Phase 2, Task 5, step 6: independent agency validation (for review)

Status: **submitted for independent review.** Task 5 is **not** fully independently
validated. DOB manual validation (Part C), the five manual OATH lookups (part of Part
D) and HPD validations A and B are pending. The attribution ground truth is proposed,
not frozen. No adapter, model, migration (no 0012), role, fixture count, LangGraph,
frontend or deployment change. No PR, merge, history rewrite, branch rename or deletion.

Branch `feat/phase-2-dob-311`. Before this step: the Step 5 privacy correction, `48acda9`
(see `docs/phase2_task5_step5_report.md` section 5.1).

## 1. Outcome by category

| Check | 1. Automated cross-check PASSED | 2. FAILED or DISAGREED | 3. Manual PENDING | 4. SOURCE UNAVAILABLE / INCONCLUSIVE |
|---|---|---|---|---|
| C: DOB BIS / DOB NOW | | | 91 complaints to look up (4 active, 71 closed sample, 22 order events) | BIS and DOB NOW are unreachable from the cloud environment |
| D: OATH, automated | 223 of 312 summonses found; 153 outcomes identical; 32 same outcome, different wording; 213 of 223 on the same block and lot | 33 STIPULATION/IN-VIO vs STIPULATED; 2 ADMIT/IN-VIO vs blank; 2 paid amounts differ by $1; 10 on a different lot (historical lot identity) | | 89 not in OATH, all with hearings 1988 to 1999; 3 DEFAULT/DEFAULTED outcomes (meaning not documented) |
| D: OATH, manual | | | 5 summonses (one per address) | |
| E: 311 aggregates | independent count = live adapter at 5/5 addresses, 0 agency × month cells differ; lot families agree; HPD/DOB exclusions and NULL agency (0) agree | | | |
| E: HPD key match | 294/309, 17/47, 44/44, 8/8, 173/173 found on the lot | 15 HPD-routed keys at 1134 WF and 1 at 220 W 17 not in HPD complaints | | 29 at 220 W 17 found in HPD complaints only on rows with no BBL |
| Ground truth | 12 rows gain independent support | none traced to an implementation error | freeze needs Sanyam's approval after Part C | rows 2, 13, 21 Uncertain; row 23 Conflicting |
| HPD A and B | | | **pending** (unchanged checklist `/mnt/project-files/phase2/hpd_validation.md`) | HPD Online unreachable from the cloud |

## 2. Part C: DOB BIS / DOB NOW checklist (manual, PENDING)

Script `backend/scripts/task5_validation_part_c.py` replays the committed recording and
writes `docs/task5/validation/part_c_dob_checklist.md` and `.json`. A test rebuilds both
byte for byte.

- **Rows:** every ACTIVE complaint (4); up to 10 CLOSED per BIN (71 across 12 BINs),
  chosen by the lowest SHA-256 of a fixed salt plus the complaint number, independent of
  any outcome; and every stop-work or vacate order event (22). Some rows are listed for
  more than one reason.
- **Each row:** complaint number, BIN, our status, disposition code, received and
  disposition dates, the source's data version, the expected attribution, and blank
  columns for BIS's status, disposition, date, "at this BIN?" and match.
- **220 W 17th and 1845 ACP:** these list `this_building` and `other_building_on_lot`
  BINs separately, so the building attribution can be checked against BIS.
- **Order events:** each shows DOB's official text and our label, with a blank for BIS's
  wording and whether BIS shows the order as in effect. We never claim it is.
- **1217 West Farms:** DOB complaints were not requested there (tax-lot scope), so it is
  listed for coverage only.

Part C is **not passed** and will not be reported as passed until lookup evidence is
supplied and checked.

## 3. Part D: OATH cross-check

Script `backend/scripts/task5_validation_part_d.py`. Read-only against `jz4z-kudi` with a
fixed `$select` that never requests respondent names, addresses, location street,
house or floor, or free-text details (a test checks the committed file).

- **Version:** the first pass came back from two replicas (Sat 17 Oct and Thu 22 Oct).
  Each batch was asked again until all 16 batches answered on Thu 22 Oct 2026 04:46:54
  (35 queries logged). The DOB side is the 2026-10-23 recording.
- **Matching:** OATH writes DOB summonses with a leading zero (10 characters). Every
  summons is reported; none is excluded.

| Address | Summonses | Matched | Not found |
|---|---|---|---|
| 1134 West Farms | 3 | 3 | 0 |
| 220 W 17th | 4 | 4 | 0 |
| 1217 West Farms | 12 | 12 | 0 |
| 350 5th Ave | 241 | 161 | 80 |
| 1845 ACP | 52 | 43 | 9 |
| **All** | **312** | **223** | **89** |

All 89 unmatched summonses had hearings from 1988 to 1999, and every matched one is from
2000 or later. This points to the OATH dataset's coverage period: source unavailable or
inconclusive, not a failure. One of the five manual lookups (350 5th, `038059001P`, 1997)
tests this directly.

**Outcome comparison (matched).** DOB's `hearing_status` is DOB's copy; OATH's
`hearing_result` is the tribunal's record.

| DOB | OATH | Summonses | Verdict |
|---|---|---|---|
| IN VIOLATION | IN VIOLATION | 113 | agree |
| DISMISSED | DISMISSED | 38 | agree |
| STIPULATION/IN-VIO | STIPULATED | 33 | **disagree** (OATH does not say "in violation") |
| CURED/IN-VIO | IN VIOLATION | 29 | same group, different wording |
| DEFAULT | DEFAULTED / DEFAULT | 3 / 1 | meaning not documented |
| PENDING | ADJOURNED | 2 | same group (future hearings) |
| ADMIT/IN-VIO | (blank) / ADMIT IN-VIO | 2 / 1 | **disagree** / same group |
| WRITTEN OFF | WRITTEN OFF | 1 | agree, meaning not documented |

DEFAULT, DEFAULTED and WRITTEN OFF are never read as an established legal conclusion.
The 33 stipulations show that grouping DOB's STIPULATION/IN-VIO as "upheld" goes beyond
what OATH records. This is noted as a mapping question for review, not changed here.

**Other fields.** Penalty imposed and hearing date agree on every matched summons. Two
paid amounts differ by $1 (6097 vs 6098; 624 vs 625). Ten ACP summonses are on another
lot in OATH: 7 for BIN 1054974 on base lot 0001 instead of 7502 (same condo family), and
3 for BIN 1055002 on retired lot 0061. This is historical lot identity, not an attribution
error. A match with no difference in any field: 144 of 223.

The five summonses for manual lookup (OATH ticket finder or DOB BIS) are in
`part_d_oath_crosscheck.md`. **Pending.**

## 4. Part E: independent 311 aggregates

Script `backend/scripts/task5_validation_part_e.py`. It uses plain HTTP and grouped SoQL
(`agency, date_trunc_ym(created_date)`), with its own Footprints lot-family lookup. It
does not use the adapter, pager or query builders for its numbers. Each grouped answer is
asked until two consecutive answers agree (compared without regard to row order), and
again after a live adapter run.

| Address | Independent (now) | Live adapter (now) | Recording | Cells that differ (adapter / recording) | HPD/DOB recorded → now | NULL agency |
|---|---|---|---|---|---|---|
| 1134 West Farms | 89 | 89, complete | 89, incomplete | 0 / 0 | 309/1 → 309/1 | 0 |
| 220 W 17th | 89 | 89, complete | 89, complete | 0 / 0 | 47/26 → 47/29 | 0 |
| 1217 West Farms | 32 | 32, complete | 32, complete | 0 / 0 | 44/1 → 44/1 | 0 |
| 350 5th Ave | 485 | 485, complete | 485, complete | 0 / 0 | 8/11 → 8/11 | 0 |
| 1845 ACP | 250 | 250, complete | 250, incomplete | 0 / 0 | 173/12 → 173/12 | 0 |

- **Timing and versions.** Every answer now is on the 311 version Fri 23 Oct 2026
  01:38:29, the same version as the recorded main fetches. So the retained rows agree with
  the recording cell for cell. The recording's two incomplete results (count on Thu, data
  on Fri) stay incomplete: a new live query is independent validation, not a replacement.
  At 220 W 17th the DOB-routed count was 26 on the older version and 29 on the newer one,
  which confirms why the recording could not establish it.
- **HPD cross-match.** Keys are held in memory only, and outputs are counts. A test
  checks that no 8-to-9-digit number and no key filter appears in the committed files.

| Address | HPD-routed 311 | Found on lot | Found, HPD row has no BBL | Not found | HPD BINs of the matches |
|---|---|---|---|---|---|
| 1134 West Farms | 309 | 294 | 0 | 15 | 2006185: 294 |
| 220 W 17th | 47 | 17 | 29 | 1 | 1081543: 11, 1081546: 5, 1081544: 1 |
| 1217 West Farms | 44 | 44 | 0 | 0 | 2128717: 44 |
| 350 5th Ave | 8 | 8 | 0 | 0 | 1015862: 8 |
| 1845 ACP | 173 | 173 | 0 | 0 | 1054974: 138, 1055002: 35 |

The match rate is reported, not asserted. At 220 W 17th and 1845 ACP, HPD puts the same
lot's 311 requests on different buildings, which supports never attributing a 311 row
to one building.

## 5. Attribution ground truth

`docs/task5/attribution_ground_truth_PROPOSED_FINAL.md` is a proposal with a change log.
The draft is kept unchanged.

- **No expected attribution changed.** 12 rows gain independent support: the OATH lot
  for ECB and the independent count for 311.
- **DOB complaint rows** wait for Part C.
- **Uncertain and conflicting rows stay that way:** 2 (BIN only in Footprints), 13
  (retired BIN), 21 (condo base lot, rule 18.3) and 23 (lot 0061, Conflicting, which
  OATH now corroborates as real history).
- **Disagreements, classified:**
  - historical lot identity: ACP, 10 summonses;
  - source unavailable: 89 pre-2000 summonses;
  - HPD-side completeness: 29 rows with no BBL, 16 keys not found;
  - mapping: stipulation wording.

  None is an implementation error. Nothing is frozen.

## 6. Tests and CI

| Run | Result |
|---|---|
| Full suite with PostgreSQL 16 (`REQUIRE_DB_TESTS=1`) | 1177 passed, 33 skipped (live) |
| PostgreSQL-marked tests | 231 passed |
| Offline | 956 passed, 254 skipped |
| Live (`RUN_LIVE_TESTS=1 -m live`) | 33 passed |
| Resolver benchmark | 14/14 correct, 0 wrong building, ambiguity 6/6, abstentions 4/4, BIN preserved 2/2 |
| ruff check and format | clean |
| New: `tests/unit/test_task5_validation.py` | 10 passed (Part C rebuilt byte for byte; D and E completeness, privacy, exact re-render; matching rules) |
| CI | see the thread for the run at this commit |

## 7. Defects found and how they were handled

- **Privacy (substantive, fixed before Step 6):** step-4 and history 311 templates
  revealed outreach, service outcomes or arrests for individuals. They are withheld, and
  10 of the 945 recorded rows had shown one publicly (Step 5 report 5.1, `48acda9`).
- **Validation tooling:** grouped Socrata answers arrive in any row order; OATH answered
  from two replicas. Both are handled in the scripts and recorded in the query logs.
- **No adapter defect** was found by Parts D or E. The STIPULATION/IN-VIO grouping is
  raised as a mapping question, not changed.

## 8. Limitations and pending

- Part C (DOB), the five manual OATH lookups, and HPD A and B are pending. No result is
  invented.
- 19 DOB disposition codes still lack official definitions. DEFAULT, WRITTEN OFF and
  POP-style OATH outcomes have no documented meaning here.
- OATH has no BIN, so it confirms a lot, never a building.
- OATH coverage appears to start around 2000. This is inferred from the data, not from
  an official statement.
- Live results are a snapshot (OATH about 20:12 UTC, 311 about 20:20 UTC on 2026-10-23; times are in the query logs). They do not replace
  the recordings.
- The attribution ground truth is proposed, not approved or frozen.
