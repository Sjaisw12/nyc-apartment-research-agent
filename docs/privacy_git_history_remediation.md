# Fixture privacy issue in Git history: what happened and how to fix it

Status: **proposal only.** No history has been rewritten and nothing has been force-pushed. Every step below that changes shared history needs Sanyam's explicit approval.

## What happened

On 2026-10-21 at 06:14 UTC, Phase 1 commit `27cd1af` ("Phase 1 task 1: repo skeleton, HTTP client, GeoSearch/HPD smoke tests") added two live-response fixtures without redaction:

| File | Unit-level content |
|---|---|
| `backend/tests/fixtures/nyc_open_data/hpd_violations_2027540005_first5.json` | 5 HPD violation rows for 1134 West Farms Rd: `apartment` values (1H, H), `story` values, and descriptions such as "LOCATED AT APT 1H, 2nd STORY, 1st APARTMENT FROM WEST AT NORTH" |
| `backend/tests/fixtures/nyc_open_data/hpd_complaint_problems_2027540005_first5.json` | 5 HPD complaint rows: `apartment` values (1E, BLDG) |

There are no names, phone numbers or other personal fields. The rows are copies of public NYC Open Data records, which anyone can download today. The project rule (approved decision D2) is still not to re-publish unit-level detail, because a unit plus a complaint type (mice, mold, no heat) says something about one household.

Commit `06c1d28` (Phase 2 Task 1) redacted both files in the current tree. A test (`test_fixture_privacy.py`) now scans every committed fixture.

## Where the unredacted versions still exist (checked 2026-10-21)

The repository is **public**.

| Location | Contains the unredacted files? |
|---|---|
| `main` (`187ebb9`) | **No.** Only README, LICENSE and .gitignore. |
| `claude/project-thread-rcb2pd` (Phase 1, head `6d76ae1`) | **Yes, in the current tree** and in history |
| PR #1 (`refs/pull/1/head`, `refs/pull/1/merge`), open | **Yes.** It shows in the PR's "Files changed" view. |
| `claude/project-thread-5d0l0f` (Phase 2) | In history only (commits before `06c1d28`). The current tree is redacted. |
| Clones, forks, caches | Unknown. The repo has 0 forks. GitHub may cache commit views. |

## Proposed remediation, least invasive first

### Step 1: stop it reaching `main` (recommended now, no history rewrite)

Before PR #1 is merged, add one normal commit to `claude/project-thread-rcb2pd` that redacts the two fixtures. It would be the same change as in `06c1d28`: the files plus their `.meta.json`, keeping `original_body_sha256`. Run CI, then merge PR #1 with **squash merge**. A squash merge puts only the final, redacted tree on `main`, so `main`'s history never contains the unredacted blobs.

This touches the Phase 1 branch, which another thread owns, so it needs your OK and should be coordinated with that thread.

### Step 2: clean up branches after the merge (needs approval; branch deletion only)

After both PRs are merged (squash), delete the two `claude/project-thread-*` branches. The old commits then stop being reachable from any branch.

**Limit:** GitHub keeps `refs/pull/N/head` for every PR, including closed and merged ones, and users cannot delete these refs. The original commits stay reachable by SHA through the PR page until GitHub garbage-collects them, which it does not promise to do.

### Step 3: full purge (only if you want a stronger guarantee)

1. Rewrite the feature branches with `git filter-repo --path <file> --invert-paths` (or replace the blobs with the redacted versions), then force-push. **This rewrites shared history, so it needs explicit approval.**
2. Ask GitHub Support to remove cached views and the dereferenced commits from PR refs, following GitHub's "Removing sensitive data from a repository" guide. Only Support can purge PR refs.
3. Anyone with a clone keeps a copy. With 0 forks and a repo created on 2026-10-21, that risk is small.

## Recommendation

Do Step 1 before merging PR #1, and Step 2 after merging. Skip Step 3 unless you want it. The data is already public at the source, it is limited to 10 rows of one building, it contains no names, and a full purge would rewrite history on an open PR. Whichever option you choose, the final state should be recorded here.

## What prevents a repeat

- `backend/tests/unit/test_fixture_privacy.py` fails CI if any committed fixture carries an apartment or floor value, or unredacted unit text in a description.
- `scripts/smoke_sources.py` and `scripts/record_hpd_fixtures.py` redact rows before writing any file.
- Public records carry a keyed digest of the original row, not a plain SHA-256. A plain hash could be brute-forced back to the apartment and floor, because the hidden parts have only thousands of combinations. `original_body_sha256` in fixture metadata is a hash over whole pages of 5 to 250 rows. Recovering every hidden value in a page at once is not practical, so it is kept as the record that the fixtures were redacted. Future single-row fixtures should not carry it.

## Decision log

_No decision yet._
