# Phase 2, Task 1 completion report: HPD violations

Date: 2026-10-21. Branch: `claude/project-thread-5d0l0f`. Design and approval record: [phase2_task1_hpd_violations_design.md](phase2_task1_hpd_violations_design.md).

**Status: implemented and tested. Independent validation against HPD Online is PENDING** (checklist in [`data/eval/hpd_validation.md`](../data/eval/hpd_validation.md)). Task 1 should not be called complete until those results are recorded.

## 1. What was implemented

Given a Phase 1 `ResolutionResult`, `retrieve_hpd_violations()` returns a `SourceResult` with one of five statuses, normalized records, a summary and full provenance.

1. **Join policy** (`records/join_policy.py`). The only code that reads `permitted_join_keys`. No keys means `unsupported_join` and zero HTTP calls. HPD is always fetched by lot (`bbl` OR `boroid`+`block`+`lot`, D1), never by BIN. The BIN is used only after fetching, and only at building level. `require_bin_join()` is ready for BIN-only sources (DOB) and refuses tax-lot results.
2. **Verified pagination** (`sources/socrata_pager.py`). count(*) first, then pages ordered by the unique `violationid`. The fetch counts as complete only if every page succeeded, rows fetched equal the count, the IDs are unique, and the dataset version header (`X-SODA2-Truth-Last-Modified`) is present and the same on every response. If the version changes, the pass stops at once and restarts once, then gives up as incomplete. 10,000-row cap (D3).
3. **Normalizer** (`records/hpd_violations.py`). Keeps every source ID and raw value. Adds parsed dates (impossible ones are set to null and flagged), `is_open` from `violationstatus` with a flag when `currentstatus` clearly contradicts it, Class I `order_category` with `potentially_consequential` (correction 2), evidence-based `location_scope` including `unspecified` (correction 1), quality flags and hashes.
4. **Attribution** (`records/attribution.py`). Four buckets: `this_building`, `other_building_on_lot`, `unattributed_on_lot`, `lot_level` (correction 6). If BBL and boro-block-lot disagree, attribution is blocked (correction 5). A model validator makes building-level attribution on a tax-lot result impossible to construct.
5. **Redaction and evidence** (`records/redaction.py`, `records/evidence.py`). Apartment, floor and unit-position detail is removed from public records and fixtures, including inside `novdescription` (correction 3). The unredacted row goes to an evidence store: in memory, or `.evidence/`, which is git-ignored. Every record carries `original_sha256` and `redacted_sha256`.
6. **Fixtures and scripts.** `scripts/record_hpd_fixtures.py` records live resolver and HPD traffic for 5 addresses and redacts it before saving. `scripts/report_hpd_violations.py` replays it into `data/eval/hpd_task1_results.json`. The Phase 1 smoke script now redacts before saving too.

Phase 1 address-resolution rules and benchmark labels were not touched. The benchmark replay is unchanged: 14/14 correct, 0 wrong-building matches, 6/6 ambiguous handled, 4/4 abstentions, 2/2 unresolved BINs preserved.

## 2. Files

New:
- `backend/src/nyc_apartment_agent/records/{__init__,models,join_policy,attribution,redaction,evidence,hpd_violations}.py`
- `backend/src/nyc_apartment_agent/sources/socrata_pager.py`
- `backend/scripts/record_hpd_fixtures.py`, `backend/scripts/report_hpd_violations.py`
- `backend/tests/hpd_helpers.py` (synthetic builders)
- `backend/tests/unit/test_join_policy.py`, `test_socrata_pager.py`, `test_hpd_violations.py`, `test_redaction.py`, `test_hpd_cassette.py`, `test_fixture_privacy.py`
- `backend/tests/live/test_live_hpd.py`
- `backend/tests/fixtures/hpd/violations_cassette.json` (2.8 MB, redacted) + `.meta.json`
- `data/eval/hpd_task1_results.json`, `data/eval/hpd_validation.md`, this report

Modified:
- `config.py` (page size, row cap), `.env.example`, `.gitignore` (`.evidence/`)
- `cassette.py`: also keeps the `x-soda2-truth-last-modified`, `x-soda2-fields` and `x-socrata-requestid` headers
- `scripts/smoke_sources.py`: redacts rows before writing fixtures
- `tests/fixtures/nyc_open_data/hpd_*_first5.json` (+ meta): **Phase 1 fixtures had apartment numbers and "APT 1H, 2nd STORY" text. Now redacted**, and the meta files keep the original hash
- `docs/source_registry.md`, `docs/phase2_task1_hpd_violations_design.md` (approval record)

## 3. Tests and actual results

| Run | Result |
|---|---|
| `uv run pytest -q` (offline, full suite) | **245 passed, 9 skipped** (the skipped ones are the live tests). Phase 1 had 158; 87 are new. |
| `RUN_LIVE_TESTS=1 uv run pytest -q tests/live` | **9 passed** (5 Phase 1 + 4 new), against the live APIs on 2026-10-21 |
| `uv run ruff check .` / `ruff format --check .` | clean |
| `uv run python scripts/evaluate_resolver.py` | unchanged from Phase 1 (numbers above) |
| GitHub Actions CI on commit 06c1d28 | **success**: https://github.com/Sjaisw12/nyc-apartment-research-agent/actions/runs/39679527838 |
| Clean clone + `uv sync --locked` + full suite | 245 passed, 9 skipped |

What the tests show, against the approval conditions:

- **Complete pagination:** the test building fetches 666 rows over three pages (250, 250, 166) and matches count(*). The condo fetches 1,160 rows over five pages. Live, the test building came back complete with 666 rows.
- **Failure handling (synthetic server):** a 429 that recovers on retry still gives a complete fetch. A page that keeps failing keeps the partial rows and gives `incomplete_pagination`. Short pages, duplicate IDs across pages, a missing freshness header, and a cap overflow all give `incomplete_pagination`. One dataset update during a fetch restarts it and succeeds. Two updates end incomplete. A count failure gives `source_unavailable`, never "no records".
- **Missing-BBL rows retrieved:** on lot 1007660055, 87 rows with no BBL and no BIN are retrieved through boro-block-lot (recorded and live tests). All are `unattributed_on_lot`.
- **Lot-level results cannot make building claims:** for a tax-lot result, every row is `lot_level`, `resolved_bin` is null, and `building_specific_claims_allowed` is false. A forged record is rejected by the model validator. Unverified resolutions make zero HTTP calls.
- **Privacy:** a test scans every committed fixture, including bodies inside cassettes, for apartment or floor values and unredacted unit text: none found. Another test checks that logs never contain row contents.

## 4. Normalized results (recorded 2026-10-21, dataset version Wed, 21 Oct 2026 14:51:46 GMT)

| Address | Resolution | Status | Rows | Attribution | Open (A / B / C) | Class I |
|---|---|---|---|---|---|---|
| 1134 West Farms Rd, Bronx | building, BIN 2006185 | records_found | 666 | 666 this building | 112 open: 9 / 62 / 40, plus 1 class I | 1 AEP order (18617994), **open** |
| 220 W 17th St, Manhattan | building, BIN 1081543 | records_found | 174 | 24 this, 63 other (5 BINs), 87 unattributed | this building: 1 open (A). Unattributed: 39 open | registration non-compliance only |
| 1217 West Farms Rd, Bronx | **tax lot** (placeholder BIN) | records_found | 12 | 12 lot level | 0 open | none |
| 350 5th Ave, Manhattan | building | **no_records_found** | 0 | none | none | none |
| 1845 Adam Clayton Powell Jr Blvd | building, condo billing lot 1018227502 | records_found | 1,160 | 891 this, 269 other (1851 ACP) | this building: 73 open: 7 / 21 / 45 | 2 AEP + 1 underlying-conditions order, all closed |

**Quality flags.** `bbl_missing` 87 and `bin_missing` 87 (all on 220 W 17th). Live, `unit_detail_redacted` was set on 616 of 666 test-building records. The recorded fixtures are already redacted, so replay shows 0 for that flag.

**Conflicts.** 0 `lot_identifier_conflict`, 0 `status_fields_disagree`, 0 `duplicate_id_different_content` across these five buildings. Status contradictions do exist in the full dataset (114 clear ones citywide by the rules used here), but none fell on these buildings, so that path is tested with synthetic rows only.

**Location scope (test building).** 536 unit, 90 common area, 1 building-wide, 39 unspecified.

## 5. Data-quality issues discovered in this task

1. **Phase 1 fixtures already contained unit detail** (apartment "1H"/"H", "APT 1H, 2nd STORY"). They are now redacted. **The old versions are still in git history** on this branch and on the Phase 1 PR branch. Removing them would mean rewriting history, which I have not done without your approval.
2. **Rows with no BBL/BIN do carry an HPD `buildingid`** (805523: 61 rows, 30619: 26 rows on lot 1007660055). That is a possible way to attribute them through HPD's buildings dataset (Task 3). It would be a second-source join, so it needs its own conflict rules. Not done here.
3. **Condos:** both condos checked file violations under the billing lot with each building's own BIN, and GeoSearch returned the billing BBL. So the join works for these two. Two examples are not proof for all condos.
4. **The data moved during the day.** The version header went from Tue 20 Oct 14:55 GMT (morning) to Wed 21 Oct 14:51 GMT (afternoon). Daily refreshes are real, which is why the per-page freshness check matters.
5. **Free-text redaction is pattern-based.** It catches the HPD phrasings seen in 2,000+ real rows ("APT 1H", "APTS 3A, 4B", "2nd STORY", "2 STY", "1st APARTMENT FROM WEST AT NORTH"). A new phrasing could slip through. The fixture scan test is the safety net for committed data, but not for live output.

## 6. Remaining risks and limitations

- **HPD Online validation pending** (D4). Our status reading (`violationstatus`) has not yet been compared with what HPD shows the public.
- Status contradictions are flagged only for clear cases. 57,143 rows citywide are "Close" + "VIOLATION WILL BE REINSPECTED", and their meaning is undocumented. They are kept, not judged.
- The evidence store is a local directory for now. PostgreSQL (with retention and access rules) is a later Phase 2 task.
- No API endpoint exposes this yet. That was out of scope for Task 1.
- Still tracked before public launch: rate limiting, CORS, apartment/unit parsing in addresses, GitHub Actions upgrades (Node 20 deprecation), NYC Open Data terms-of-use review, optional `SOCRATA_APP_TOKEN`.

## 7. Concepts to understand for interviews

- **Separating "could we check" from "what we found".** Five statuses mean an outage can never read as a clean record. An empty list always comes with a status and a count of 0.
- **Offset pagination is only safe with a unique sort key and a stable snapshot.** Ordering by `violationid` gives the first. Comparing the dataset version header across every page detects when the second breaks. The fallback is restart once, then report incomplete.
- **Entity resolution as a permission system.** The resolver's confidence decides which joins are *allowed*. Fetching by lot and attributing by BIN afterwards means the weaker (tax-lot) path has no code that could make a building claim. The model validator is a second, structural guard (defense in depth).
- **Provenance by hashing.** Each public record carries hashes of the original and redacted rows, so anyone with evidence-store access can prove that a claim matches its source row without the public seeing unit details.
- **Data minimization.** We keep what we need for claims (class, status, dates, scope) and drop what identifies a household (unit, floor), including when it is buried in free text.
- **Recorded fixtures vs synthetic cases.** Real recordings pin real behaviour (counts, schemas, odd rows). Synthetic servers produce failures you cannot trigger on demand (a 500 on page 2, a refresh mid-fetch).

## 8. Recommended next task

After you review this report and fill in the HPD Online checklist: **Phase 2, Task 2, HPD complaints and problems (`ygpa-z7cr`)**, reusing the pager, join policy, redaction and evidence store. Complaint vs problem counting, the duplicate flag (2.6M problem rows marked duplicate), and borough-name keys are the new parts. I will not start it until you approve.
