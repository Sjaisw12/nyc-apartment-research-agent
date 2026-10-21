# Phase 2, Task 2 closure report: HPD complaints and problems

Date: 2026-10-21. Branch: `claude/project-thread-5d0l0f`. First submission: [phase2_task2_report.md](phase2_task2_report.md). Design and approval record: [phase2_task2_hpd_complaints_design.md](phase2_task2_hpd_complaints_design.md).

## 1. Status

- **Task 2:** conditionally accepted by Sanyam on 2026-10-21 (23:28 UTC). The two required corrections, the checklist extension and the validation runs are complete, as reported below. Final closure is his decision.
- **Approved without changes:** the complaint/problem separation, the allegation vs finding distinction, conservative lot and building attribution, duplicate reporting, and the outcome-classification evaluation.
- **Open, tracked separately:**
  - Task 2's independent HPD Online check, which is new Part B of [`data/eval/hpd_validation.md`](../data/eval/hpd_validation.md). PENDING.
  - Task 1's HPD Online check (Part A). PENDING, unchanged.
- **Phase 2 is not complete.** Task 3 exists only as a design for approval. No Task 3 code has been written.

## 2. Correction 1: conflicting duplicate problem IDs now fail closed for every copy

**The bug:** when two rows shared a `problem_id` with different contents, `normalize_fetch()` flagged and downgraded only the *later* copy. The first copy kept `this_building` or `other_building_on_lot`, so a record that might be wrong could still count for the building. The original test only checked the later copy, so it missed this.

**The fix** (`records/hpd_complaints.py`), the same approach as Task 1's `hpd_violations.py`:
- Rows are tracked per `problem_id` by content hash.
- Identical copies collapse into the first copy and are flagged `duplicate_id_same_content`. This also applies when the identical copy comes after a conflicting one.
- After all rows are normalized, **every** copy of an ID with more than one distinct content is flagged `duplicate_id_different_content`, and any building-level attribution becomes `unattributed_on_lot`.
- Grouping and summaries run afterwards, so they use the corrected attribution.
- At tax-lot scope, copies stay `lot_level`, because there was no building claim to remove, but they are still flagged.

**Regression tests** (`tests/unit/test_hpd_complaints.py`):

| Test | What it proves |
|---|---|
| `test_conflicting_duplicate_ids_flag_and_downgrade_every_copy` (this-building and other-building cases) | Both conflicting copies are flagged; neither keeps `this_building` or `other_building_on_lot`; an unrelated row is untouched |
| `test_conflicting_duplicates_flow_into_grouping_and_summary` | A complaint whose only problem conflicts becomes `unattributed_on_lot`. A complaint with one clean and one conflicting problem becomes `mixed`. Summary counts and `conflict_counts` reflect the corrected attribution |
| `test_conflicting_duplicates_at_tax_lot_scope_stay_lot_level_and_flagged` | Tax-lot behaviour |

On the old code these tests fail (4 failures); with the fix they pass.

**Effect on recorded counts: none.** No conflicting duplicate IDs exist in the recorded data for the five addresses. `data/eval/hpd_task2_results.json` was regenerated and is byte-identical.

## 3. Correction 2: original evidence vs sanitized-fixture integrity

**What the review found:**
- `original_digest` is an HMAC of the row as the normalizer receives it.
- In **live retrieval** that row is the unredacted source row, so the digest ties the public record to original evidence.
- In **offline replay** the row comes from the committed fixture, which is already sanitized. The digest then covers the sanitized row. It is not, and cannot be, the original-row digest, because the original values are not in the fixture.
- The first report and one Task 1 test comment ("Every public record traces to stored raw evidence") did not make that distinction.

**What changed:**
- New `DigestBasis` with two values:
  - `original_row`: the input row carried no redaction or withheld markers.
  - `sanitized_row`: the input row was already sanitized.
- Every violation and complaint record now carries `digest_basis`, and every evidence item carries `basis`. Both are required fields, so they can never be missing by accident.
- `redaction.is_sanitized(row)` detects sanitized input from its markers (`[REDACTED]`, `[WITHHELD]`, the text-redaction markers).
- `evidence.verify()` keeps its meaning: the stored copy is intact. New `verifies_original_row()` is true only for intact evidence captured from an unredacted live row.
- `records/evidence.py` documents both cases. Fixture integrity comes from the cassette's content hash. Each page's `original_body_sha256` records that redaction happened, but it cannot be re-checked offline.
- A row with nothing to sanitize (no apartment, no withheld fields) is identical before and after sanitizing, so it is correctly reported as `original_row`.

**Tests:**
- Complaints, synthetic:
  - A live-style row gives `original_row`, and `verifies_original_row` is true.
  - The same row, sanitized and replayed, gives `sanitized_row`. Its digest differs from the live digest, `verify` is true but `verifies_original_row` is false, and the redacted-row hash is equal.
  - A row with nothing to hide gives `original_row`.
- Complaints, cassette replay: every record is `sanitized_row`, and the stored item does not verify as original evidence.
- Violations (Task 1): the existing evidence test also asserts `original_row` for live-style input. The cassette test now asserts that each stored item's basis matches the record's, and that sanitized rows never verify as original evidence. A new test shows that every row sanitized at recording time replays as `sanitized_row`.
- Live (opt-in): live rows on 1217 West Farms are all `original_row` and verify against the evidence store.

The privacy design is unchanged. Unredacted rows go only to the controlled evidence store; public records carry the keyed digest, never a plain hash.

**Effect on counts: none.** Both results files regenerate unchanged, because summaries do not contain per-record digests.

## 4. Checklist extension (Part B: complaints)

[`data/eval/hpd_validation.md`](../data/eval/hpd_validation.md) now has two clearly separated parts, each with its own status:
- **Part A (Task 1, violations):** unchanged and PENDING.
- **Part B (Task 2, complaints):** new and PENDING.

Part B covers 1134 West Farms Road, 220 West 17th Street and 1845 Adam Clayton Powell Jr Blvd. For each building it lists:
- the source snapshot (`ygpa-z7cr` version Wed, 21 Oct 2026 14:10:13 GMT, recorded 2026-10-21);
- complaint counts, with problem counts in brackets, all time and open;
- complaints received in 2025 and 2026;
- 4 selected complaint IDs with received date, problem count, status, categories and our attribution. At 220 W 17th this includes two open complaints we leave unattributed, to see where HPD Online files them.

It explains that complaints and problems differ, and that HPD Online may present them differently (by complaint or problem, over a limited period, or grouped by building). Differences go into a reconciliation table with the same categories as Part A, rather than being assumed to be errors. No results were filled in.

## 5. Privacy decision (unchanged)

- The Phase 1 redaction commit (`caa8e39`) is accepted as Step 1 containment, and that is recorded in [privacy_git_history_remediation.md](privacy_git_history_remediation.md).
- The historical exposure stays documented: old commits on the PR #1 branch and `refs/pull/1/head` still contain the unredacted versions.
- Nothing was rewritten, force-pushed, squash-merged or deleted. Each of those still needs separate approval.

## 6. Validation results (run 2026-10-21, ~23:50 UTC)

| Check | Result |
|---|---|
| Offline suite (`uv run pytest`) | **340 passed**, 16 skipped (live), 0 failed. Was 333; +4 duplicate-ID tests, +3 complaint evidence tests, +1 violations cassette evidence test, and the old single duplicate test replaced |
| Live suite (`RUN_LIVE_TESTS=1`) | **16 passed** (was 15; +1 live evidence test) |
| `ruff format --check`, `ruff check` | clean |
| Phase 1 benchmark replay | unchanged: 14/14 correct, 0 wrong-building matches, 6/6 ambiguous handled, 4/4 abstentions, 2/2 unresolved BINs preserved |
| GitHub Actions CI | see section 9 |

**Count changes:** none. All Task 1 and Task 2 counts, buckets, flags and coverage figures are as in the first reports.

**Remaining discrepancies:** none known between our counts and the source `count(*)`. Agreement with HPD Online is untested until Parts A and B of the checklist come back.

## 7. Files changed in this round

- `backend/src/nyc_apartment_agent/records/hpd_complaints.py`: duplicate handling; digest basis
- `backend/src/nyc_apartment_agent/records/hpd_violations.py`: digest basis (no behaviour change otherwise)
- `backend/src/nyc_apartment_agent/records/models.py`: `DigestBasis`; `digest_basis` on violations; `basis` on evidence
- `backend/src/nyc_apartment_agent/records/complaint_models.py`: `digest_basis` on complaint problems
- `backend/src/nyc_apartment_agent/records/redaction.py`: `is_sanitized()`
- `backend/src/nyc_apartment_agent/records/evidence.py`: documentation; `verifies_original_row()`
- Tests: `test_hpd_complaints.py`, `test_hpd_complaints_cassette.py`, `test_hpd_violations.py` (one assertion added), `test_hpd_cassette.py` (assertions and one test added), `tests/live/test_live_hpd_complaints.py`
- `data/eval/hpd_validation.md` (Part B), `docs/phase2_task2_report.md` (two marked corrections), this report

**Compatibility note:** evidence items saved by earlier code have no `basis` field, so they would now fail to load rather than be read as original evidence. No such local store exists in this environment (`.evidence/` is absent), and nothing is in git.

## 8. Next

1. Sanyam's review of these corrections, and the final Task 2 closure decision.
2. Parts A and B of the HPD Online checklist, when convenient. Each part is reconciled and closed on its own.
3. Task 3 design (HPD buildings and registrations) is prepared for approval: [phase2_task3_hpd_buildings_registrations_design.md](phase2_task3_hpd_buildings_registrations_design.md). No implementation until the corrections are reviewed and the design is approved.

## 9. CI

_Recorded after push._
