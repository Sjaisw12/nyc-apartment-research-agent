# Phase 2, Task 2 completion report: HPD complaints and problems

Date: 2026-10-21. Branch: `claude/project-thread-5d0l0f`. Design and approval record: [phase2_task2_hpd_complaints_design.md](phase2_task2_hpd_complaints_design.md) (section 0).

**Status: implemented and tested, awaiting Sanyam's review.** Phase 2 is **not** complete: Tasks 3 to 5 have not started.

**Task 1 is a separate item.** It stays *conditionally accepted*, and its independent HPD Online validation is still **PENDING** ([`data/eval/hpd_validation.md`](../data/eval/hpd_validation.md)). Nothing in this report changes Task 1's validation status. Task 2 has had no independent check against HPD Online either; every number below comes from the NYC Open Data API.

## 1. What was implemented

`retrieve_hpd_complaints(resolution, client)` returns a `ComplaintSourceResult`: one of the five statuses, normalized problem rows, complaint groups, a summary and full provenance. Task 1's join policy, verified pagination, lot-identity check, BIN attribution, redaction, keyed digest and evidence store are reused unchanged.

New in Task 2:

1. **Problems and complaints are separate.** Each row is a problem. `group_complaints()` builds one `ComplaintGroup` per `complaint_id`. Every summary reports `total_problems` and `total_complaints` and has separate per-bucket tables (`problems_by_attribution`, `complaints_by_attribution`).
2. **Allegation, not finding.** Every problem and complaint has `evidence_kind = reported_allegation`. Violations (Task 1) are `official_finding`.
3. **HPD's disposition** (`hpd_disposition`) is HPD's own account of its follow-up, classified from `status_description` templates. `identifies_specific_violation` is always `false`, and no field links a complaint to a violation ID, because the data has no such key.
4. **Mixed complaints.** A complaint whose problems fall in different attribution buckets is `mixed`. It is never counted as this building. A validator rejects a `mixed` label that does not span more than one bucket.
5. **Lot query by borough name.** The query is `bbl = X OR (borough = 'NAME' AND block = b AND lot = l)`. This dataset stores the borough as a name, and 37,466 rows have no `bbl`.
6. **Withheld fields.** `complaint_anonymous_flag` and `unique_key` (the 311 request number) never appear in public records, summaries, reports, fixtures or logs. Public records carry only the field *names* in `withheld_fields` and a `has_311_reference` boolean. The values are kept in the evidence store for audit and for the later 311 reconciliation.
7. **Data-quality flags.**
   - `status_fields_disagree`: a CLOSE complaint with an OPEN problem. The data dictionary says a complaint is closed only when all of its problems are.
   - `date_order`: a status date earlier than the received date.
   - `complaint_fields_disagree`: problems of one complaint disagree on the received date or the complaint status.
   - `duplicate_id_different_content`: a repeated problem ID with different content. It also blocks attribution.

Phase 1 address resolution and benchmark labels were not touched. The benchmark replay is unchanged: 14/14 correct, 0 wrong-building matches, 6/6 ambiguous handled, 4/4 abstentions, 2/2 unresolved BINs preserved.

## 2. Files changed

New:
- `backend/src/nyc_apartment_agent/records/complaint_models.py`: outcome enum, `HpdDisposition`, problem and complaint models, summaries, validator
- `backend/src/nyc_apartment_agent/records/hpd_complaints.py`: query, normalizer, disposition classifier, grouping, summary, retrieval
- `backend/scripts/record_hpd_complaint_fixtures.py`: records and sanitizes the complaints cassette
- `backend/scripts/report_hpd_complaints.py`: replays the cassette into `data/eval/hpd_task2_results.json`
- `backend/tests/unit/test_hpd_complaints.py`: 64 synthetic tests
- `backend/tests/unit/test_hpd_complaints_cassette.py`: 17 replay tests
- `backend/tests/live/test_live_hpd_complaints.py`: 5 live tests (opt-in)
- `backend/tests/fixtures/hpd/complaints_cassette.json` (5.8 MB, sanitized) and `.meta.json`
- `data/eval/hpd_task2_results.json`, this report

Changed:
- `records/models.py`:
  - `EvidenceKind` added; violations default to `official_finding`.
  - New flags `date_order` and `complaint_fields_disagree`.
  - The source-result invariants were moved into a shared `check_source_invariants()`, so both result types enforce the same rules.
- `records/redaction.py`:
  - `TEXT_FIELDS` now includes `status_description`.
  - New `WITHHELD_FIELDS` and `withhold_fields()`.
- `tests/hpd_helpers.py`: a synthetic `complaint_row()` builder.
- `tests/unit/test_fixture_privacy.py`: the scan now also fails on any non-withheld anonymity flag or 311 number, and checks that the complaints cassette keeps original hashes and redaction events.
- Docs:
  - `docs/source_registry.md`: complaints section.
  - `docs/phase2_task2_hpd_complaints_design.md`: approval record.
  - `docs/privacy_git_history_remediation.md`: decision log.

Unchanged: the Task 1 violations cassette, all Task 1 and Phase 1 tests, and `data/eval/hpd_validation.md`.

## 3. Test results (run 2026-10-21, ~23:15 UTC)

| Suite | Result |
|---|---|
| Offline (`uv run pytest`) | **333 passed**, 15 skipped (live), 0 failed. Before Task 2: 251. New: 64 synthetic + 17 replay + 1 privacy-scan case |
| Live (`RUN_LIVE_TESTS=1 uv run pytest tests/live`) | **15 passed** (10 Phase 1/Task 1 + 5 Task 2) |
| `ruff format` / `ruff check` | clean |
| Resolver benchmark replay | unchanged (see section 1) |

CI on the pushed commit is listed in the PR/commit checks. Section 10 records the run.

## 4. Counts: problems and complaints, separately

Recorded live 2026-10-21. Dataset version `X-SODA2-Truth-Last-Modified: Wed, 21 Oct 2026 14:10:13 GMT`. Every total equals the source's `count(*)` and the independent probes in the design doc.

| Address | Resolution | Problems | Complaints | Pages |
|---|---|---|---|---|
| 1134 West Farms Rd (test building) | building | 912 | 539 | 500 + 412 |
| 220 W 17th St (8-building lot) | building | 105 | 79 | 105 |
| 1217 West Farms Rd | tax lot | 51 | 48 | 51 |
| 350 5th Ave (Empire State Building) | building | 10 | 6 | 10 |
| 1845 Adam Clayton Powell Jr Blvd (condo, billing lot 7502) | building | 2,653 | 1,124 | 5 × 500 + 153 |
| **Total** | | **3,731** | **1,796** | |

Per attribution bucket (problems / complaints; open problems / open complaints):

| Address | this_building | other_building_on_lot | unattributed_on_lot | lot_level | mixed |
|---|---|---|---|---|---|
| West Farms 1134 | 912 / 539 (0 / 0 open) | – | – | – | 0 |
| 220 W 17th | 13 / 10 (7 / 5 open) | 8 / 8 (1 / 1 open) | 84 / 61 (14 / 13 open) | – | 0 |
| 1217 West Farms | – | – | – | 51 / 48 (0 / 0 open) | 0 |
| Empire State Bldg | 9 / 5 (0 / 0 open) | – | 1 / 1 (0 / 0 open) | – | 0 |
| 1845 ACP | 1,717 / 747 (2 / 1 open) | 936 / 377 (0 / 0 open) | – | – | 0 |

Notes:
- The Empire State Building lot has **0 violations but 10 complaint problems** (9 attributed to the building). A building with no violations is not a building with no reports.
- Earlier notes gave "2 open" for the condo lot. That is 2 open **problems**, which belong to 1 open **complaint**.
- HPD-reported "violations issued" (complaints with at least one problem whose disposition says so): West Farms 96 of 539; 220 W 17th 1 of 10 for this building; ACP 192 of 747 for this building. These are HPD's statements about its follow-up. They are **not** matched to violation records.

## 5. Attribution and duplicate handling

- **Attribution.** The same four buckets as Task 1. A complaint inherits its problems' bucket, or becomes `mixed` when they differ (0 mixed complaints in the recorded data; synthetic tests cover the case).
  - **220 W 17th:** 84 problem rows (61 complaints) have neither BBL nor BIN. They were found only through borough name + block + lot and stay `unattributed_on_lot`. They are never assigned to the requested building.
  - **Tax-lot resolution (1217 West Farms):** only `lot_level`, no resolved BIN, `building_specific_claims_allowed = false`. Unverified resolutions make zero HTTP calls.
  - **BBL vs borough/block/lot disagreement:** this blocks attribution, as in Task 1. A borough name that does not match counts as a disagreement too, including when block and lot are unusable (0/0). This occurred once: an Empire State Building row has the Manhattan BBL and the building's own BIN, but borough BROOKLYN with block 0 and lot 0. It is blocked (`unattributed_on_lot`) rather than trusted on BBL and BIN alone. This check was added during this task, after the first replay had counted that row as this building.
- **Duplicate reports** (`problem_duplicate_flag = Y`):
  - Kept and counted, never dropped: West Farms 196 of 912 problems; 220 W 17th 15; 1217 West Farms 3; Empire State Building 0; ACP 376.
  - The remaining rows are counted as `not_flagged_duplicate`. They are **not** called distinct physical conditions, because the data does not support that claim.
  - The disposition text of a duplicate row describes the *initial* complaint. The classifier records it as `duplicate_of_building_wide` and keeps the initial complaint's outcome separately (`initial_complaint_outcome`).
  - `hpd_reported_violations_issued` counts only non-duplicate dispositions, so a single initial outcome is never counted several times.
- **Repeated problem IDs:** an identical copy collapses to one row. The same ID with different content is flagged and moved to `unattributed_on_lot`. Neither case occurred live; synthetic tests cover both.

## 6. Classification coverage and unknown outcomes

Rules are phrase matches on HPD's own template text, applied most specific first. Anything else is `other` (sanitized text kept); an empty field is `no_text`.

| Sample | Rows with text | Classified | `other` | Coverage |
|---|---|---|---|---|
| The 5 recorded lots (**in-sample**: the rules were written from these texts) | 3,731 | 3,731 | 0 | 100% |
| **Out-of-sample** live check: Queens Mar 2024, Brooklyn Nov 2019, Staten Island Jan–Feb 2010, Manhattan Sep 2026 | 8,045 | 8,044 | 1 | 99.99% |

- The single `other` text begins "...inspected the following conditions. **A Section 8 Failure was issued.**" It is left as `other`. No rule was added for it, in line with "do not broaden matching rules".
- The 100% figure is in-sample and optimistic. The out-of-sample figure is the honest estimate, though it covers only four borough/time windows.
- **Precision was checked by hand on all 24 templates** in the fixtures, beyond simply getting a match. Two cases needed rule ordering:
  - "unable to access the rooms… **No violations were issued**" is `no_access`, not `no_violation_found`, because there was no inspection.
  - "not able to gain access to your apartment… however… **a violation was issued**" is `violations_issued`.
- What the outcomes mean, and what they do not mean:
  - `no_violation_found` means HPD did not issue a violation. It does not prove that the condition never existed.
  - `corrected_per_contact` and `restored_per_contact` are what an occupant or a phone contact told HPD. They are not HPD observations.
- Distribution on the 5 lots: no violation 1,382 · duplicate of building-wide 590 · violations issued 815 · no access 511 · corrected per contact 148 · restored per contact 157 · previously issued 77 · not completed 32 · open, owner notified 19.

## 7. Privacy and data-quality findings

**Privacy**
- Only one field in `ygpa-z7cr` holds unit detail: `apartment` (unit id, or the sentinel `BLDG`). It is masked on all 3,731 recorded rows. There is no `story` field.
- `status_description` was scanned and contained no unit or floor text on these lots (0 text replacements). It is still redacted on every run, in case new templates include it.
- Withheld in fixtures and public output: `complaint_anonymous_flag` (present on 3,353 rows) and `unique_key` (present on 2,857 rows). Both are kept only in the evidence store, keyed by the HMAC `original_digest`.
- The cassette keeps each page's original body hash and per-row events (field names, kinds, counts; never values). Privacy metrics reproduce exactly from the sanitized fixture (tested).
- Logs carry only the dataset id, the BBL and the error type. A test shows that no row content reaches them, including on source errors.
- Fields such as house number, street, latitude, longitude, census tract and NTA describe the building, not a person. They stay in the fixture and are not copied into public records.

**Data quality**
1. **Status date before received date:**
   - Rows affected: 9 at West Farms (7 by problem status date, 9 including complaint status date) and 24 at the condo lot.
   - **Every case follows one pattern:** received in late December, status dated early January *of the same year* (e.g. received 2013-12-30, closed 2013-01-03).
   - This looks like a year-entry error in the source. The rows are flagged `date_order`, and the raw dates are kept, not corrected.
2. **Status contradictions:** 36 CLOSE-complaint/OPEN-problem rows exist citywide; none on these lots. OPEN complaint with CLOSE problem (17,274 citywide) is normal and not flagged.
3. **Missing BBL and BIN:** 84 rows on lot 1007660055; they are handled as in section 5.
4. **Contradictory lot parts:** 1 Empire State Building row has block 0 and lot 0, and the stated borough (BROOKLYN) contradicts its Manhattan BBL. It is flagged `lot_parts_missing` and `lot_identifier_conflict`, and its attribution is blocked.
5. **Free-text outcomes:** HPD's wording changed over time ("The Department of Housing Preservation…" vs "HPD inspected this condition…"). New wording would show up as `other` and lower the coverage figure, so the coverage report doubles as a drift monitor.

## 8. Remaining risks

- **Template drift:** HPD may add new wording. New wording becomes `other` and is visible in coverage, but outcome counts would be understated until a rule is reviewed and added.
- **The out-of-sample check is small** (8,045 rows, four windows). A larger, stratified sample would give a firmer estimate. The citywide group-by query timed out earlier.
- **No independent validation** of complaint counts against HPD Online yet. This can be added to the validation checklist if Sanyam wants it. Task 1's checklist remains pending.
- **Snapshot drift:** the counts are from the 14:10 GMT dataset version. Live tests check invariants, not exact numbers.
- **Cassette size:** the cassette adds 5.8 MB to the repo (D8 chose all five addresses). The total for both HPD cassettes is about 9 MB.
- **Withheld 311 key:** reconciliation with 311 (Task 5) needs controlled access to the evidence store. That access is not designed yet.
- **The date-order cause is inferred,** not confirmed with HPD.
- **Carried forward:** rate limiting, CORS, apartment/unit parsing, GitHub Actions upgrades, NYC Open Data terms of use, optional `SOCRATA_APP_TOKEN`, the git-history fixture remediation (Step 1 done; Steps 2 and 3 not approved), and the Task 1 HPD Online validation.

## 9. Interview concepts

- **Grain mismatch:** the dataset's row is a problem, while users think in complaints. Counting rows as complaints would inflate counts by 1.7× at the test building and 2.1× across the five lots. The fix is explicit grouping and two separate counts.
- **Allegation vs finding:** a complaint is a report, and a violation is an official finding. A type-level `evidence_kind` stops later code (the agent, the report) from blurring them. HPD's "violations were issued" is quoted as HPD's statement, never joined to a violation ID that no key supports.
- **Fail-closed attribution:** `mixed` complaints and model validators make the unsafe state (building claims at tax-lot scope, or a complaint half on another building) impossible to construct.
- **In-sample vs out-of-sample evaluation:** 100% coverage on the data the rules came from proves little. A held-out sample across boroughs and years gives an honest estimate, and leaving the one miss as `other` avoids overfitting the rules to reach a target.
- **Precision, not just coverage:** a rule can match the wrong outcome. Rule order ("no access" before "no violations were issued") was checked against every template by hand.
- **Data minimization:** a field can be harmless to the building but sensitive about a person (the anonymity flag), or a join key that becomes a tracking handle (the 311 number). Withholding values while keeping a boolean and the evidence-store copy separates public use from controlled audit.
- **Reproducible privacy metrics:** one marker per replacement, and events saved with the fixture, so a reviewer can confirm the redaction without the raw data.
- **Data-quality forensics:** the date-order rows share one calendar pattern, which points to a cause (a year-entry slip) rather than random noise. Flag it, explain it, never "fix" source data silently.

## 10. Next recommended action

1. **Sanyam reviews this report** and the Task 2 code on branch `claude/project-thread-5d0l0f`. Task 3 (HPD buildings and registrations) does not start until he approves.
2. **Task 1 closure** still needs his three HPD Online checks ([`data/eval/hpd_validation.md`](../data/eval/hpd_validation.md)). When the results arrive, they are reconciled there, the affected tests are rerun, and the Task 1 closure report follows.
3. Optional: add a short complaints section to the HPD Online checklist (problem and complaint counts for the test building), so Task 2 also gets an independent check.

CI for the pushed commit: _recorded after push._
