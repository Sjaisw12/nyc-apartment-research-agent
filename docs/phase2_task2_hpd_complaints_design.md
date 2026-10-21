# Phase 2, Task 2: HPD complaints and problems (design for approval)

Status: **design only, no implementation.** Written 2026-10-21 on branch `claude/project-thread-5d0l0f` after Task 1 was conditionally accepted. Task 1 stays open until the HPD Online validation results arrive.

All numbers come from live queries against `data.cityofnewyork.us` on 2026-10-21 (07:20 to 07:30 and 22:30 to 22:45 UTC). Exact queries are in the appendix.

## 1. Objective

Attach HPD housing complaints to a resolved lot or building, with the same guarantees as Task 1: Phase 1 join rules, five retrieval statuses, four attribution buckets, provenance, and no unit detail in public output.

Complaints are different from violations in one important way. **A complaint is a tenant's report, not a finding.** In the report it belongs under "alleged", while an HPD violation is an official finding. Task 2 keeps that line visible in the data model so the later agent and the report cannot blur it.

## 2. Data-source review: `ygpa-z7cr`

| Item | Value |
|---|---|
| Name | Housing Maintenance Code Complaints and Problems |
| Rows | 16,398,305 **problem** rows covering 8,940,907 complaints |
| Updated | Daily. `X-SODA2-Truth-Last-Modified: Wed, 21 Oct 2026 14:10:13 GMT` |
| Range | `received_date` 2001-12-16 to 2026-10-21. None in the future. |
| Grain | One row per **problem**; one complaint has 1 or more problems |
| Unique key | `problem_id`: no duplicates citywide (checked 07:2x UTC) |
| Lot keys | `bbl` (number), `bin` (number), `borough` (**name**, e.g. `BRONX`) + `block` + `lot`, `building_id` |
| Status | `complaint_status` + `complaint_status_date`; `problem_status` + `problem_status_date`; `status_description` (free text) |
| Classification | `type` (EMERGENCY, IMMEDIATE EMERGENCY, HAZARDOUS, NON EMERGENCY, REFERRAL), `major_category`, `minor_category`, `problem_code` |
| Location | `unit_type` (APARTMENT, BUILDING, BUILDING-WIDE, PUBLIC AREA, PUBLIC PARTS), `space_type` (ENTIRE APARTMENT, BATHROOM...), `apartment` |
| Other | `problem_duplicate_flag` (Y/N), `complaint_anonymous_flag` (Y/N/null), `unique_key` (311 request number) |

## 3. Data-quality findings (live)

1. **Problems are not complaints.** For the test building there are 912 problem rows but 539 complaints. Every count must say which one it is.
2. **Rows with no BBL, but with borough, block and lot.** 37,466 rows citywide have no `bbl`, and all of them have `block`. On lot 1007660055 (220 W 17th), 84 of 105 rows have **no BBL and no BIN**, the same pattern as violations. So the lot query is `bbl = X OR (borough = 'NAME' AND block = b AND lot = l)`. Note the borough *name* here, not an id.
3. **Duplicate reports.** 2,633,889 problems citywide (196 of 912 at the test building) have `problem_duplicate_flag = Y`. The matching status text reads "More than one complaint was received for this building-wide condition. This complaint status is for the initial complaint." These are extra reports of the same condition, not separate conditions.
4. **Status combinations.** Citywide: CLOSE/CLOSE 16.3M, OPEN/OPEN 39,808, OPEN complaint with a CLOSE problem 17,274 (normal while other problems are pending), and **CLOSE complaint with an OPEN problem: 36** (a contradiction to flag).
5. **Outcome lives in free text.** `status_description` has a small set of HPD templates: violations issued, no violations issued, no access, occupant verified corrected, duplicate of a building-wide condition, violations previously issued, heat restored per tenant, and newer phrasings ("HPD inspected this condition..."). There is **no key linking a complaint to a violation**. So "violations were issued" can be quoted, but never matched to a specific violation.
6. **`unique_key` is a 311 request number, not a complaint key.** The test building has 752 distinct values across 912 problems and 539 complaints, and 160 rows have none. One value (70813572) matched an HPD row in 311 (`erm2-nwe9`). It is useful for 311 de-duplication in Task 5, not inside HPD.
7. **Date order.** At the test building, 7 problems have `problem_status_date` before `received_date`. They will be flagged, not fixed.
8. **Structured location fields exist.** `unit_type`/`space_type` give official location evidence (APARTMENT, BUILDING-WIDE, PUBLIC AREA), better than parsing text. `apartment` holds a unit id, or the sentinel `BLDG` (100 rows at the test building).
9. **Privacy.** `apartment` is filled on every test-building row. `complaint_anonymous_flag` describes the complainant. It identifies no one, but it is about a person, not the building.
10. **Volume per building.** Measured counts on the Task 1 addresses: West Farms 912 problems / 539 complaints; 220 W 17th 105 / 79; 1217 West Farms 51 / 48; Empire State Building 10 / 6; 1845 ACP condo lot 2,653 / 1,124 (2 open). All are well under the 10,000-row cap.

## 4. Architecture: reuse Task 1, add the complaint-specific parts

```
ResolutionResult -> join_policy.plan_lot_query (unchanged)
                 -> socrata_pager.fetch_all(order="problem_id")   (unchanged)
                 -> hpd_complaints.normalize (new) -> attribution (shared) -> redaction (shared)
                 -> group problems into complaints (new) -> summary (new)
                 -> SourceResult with the same five statuses
```

| File | Change |
|---|---|
| `records/hpd_complaints.py` | **New.** Lot query with borough name, normalizer, complaint grouping, outcome categories, summary. |
| `records/models.py` | Add `NormalizedComplaintProblem`, `ComplaintSummary`, `ComplaintOutcome`, `EvidenceKind` (`official_finding` / `reported_allegation`). Let `SourceResult.records` hold either record type. Task 1 models unchanged. |
| `records/redaction.py` | Add `status_description` as a scanned text field (the templates seen have no unit ids, but the scan is cheap). The `BLDG` sentinel is masked too: it is not unit detail, but keeping one rule is simpler. |
| `records/attribution.py` | Unchanged. BIN arrives as a number, so it is normalized to a 7-character string first. |
| `records/hpd_violations.py` | Optional small refactor: move shared steps (lot identity, redaction info, digest) into `records/common.py`, **with no behaviour change**. Every Task 1 test must pass unchanged, and the refactor will be its own commit. |
| `scripts/record_hpd_fixtures.py` | Also record complaint traffic for the same 5 addresses (same redaction + redaction events). |
| Tests | `test_hpd_complaints.py` (synthetic), cassette replay tests, live tests, privacy scan extended. |

### 4.1 Normalized complaint problem

Kept from the source: `problem_id`, `complaint_id`, `building_id`, `unique_key`, lot keys, `type`, `major_category`, `minor_category`, `problem_code`, `unit_type`, `space_type`, both statuses with dates, `status_description` (redacted), `problem_duplicate_flag`, and every date as its raw string.

Added by us:
- `evidence_kind = reported_allegation`. Violations get `official_finding`.
- `is_open` per problem and per complaint. `CLOSE` complaint with `OPEN` problem gives the `status_fields_disagree` flag.
- `outcome`: `violations_issued`, `violations_previously_issued`, `no_violation_found`, `no_access`, `corrected_per_occupant`, `duplicate_of_building_wide`, `other`, or `none` (still open). Set by matching the HPD template phrases; `other` when no rule matches. Coverage is reported (share of rows that land in `other`).
- `location_scope` from `unit_type`: APARTMENT gives `unit`; BUILDING or BUILDING-WIDE gives `building_wide`; PUBLIC AREA or PUBLIC PARTS gives `common_area`; anything else gives `unspecified`. Each comes with a basis string, as in Task 1.
- Same `attribution`, `lot_membership`, `quality_flags`, `redaction`, `original_digest`, `redacted_sha256`, provenance fields as Task 1.

### 4.2 Complaint grouping

Problems are grouped by `complaint_id` into complaints: received date, complaint status, number of problems, categories, and the worst `type`. If complaint-level fields differ inside one complaint, the complaint gets a `complaint_fields_disagree` flag. None were seen at the test building. A complaint whose problems span several attribution buckets is reported in the **most cautious** bucket (unattributed beats other building, which beats this building), so a mixed complaint never inflates "this building".

### 4.3 Summary per attribution bucket

Complaints and problems counted separately: open / closed, by `type`, by `major_category`, by outcome, duplicate problems, the last 12 / 36 months by `received_date`, and the share of complaints that HPD's text says led to violations. Wording rule: "N complaints were filed; HPD's records say violations were issued after inspection in M of them". Never "N violations were confirmed".

## 5. Tests planned

Recorded fixtures (redacted, with redaction events): the same 5 Task 1 addresses, about 3,700 problem rows in total, so the cassette grows by roughly 2.5 to 3.5 MB. Option D8 keeps it smaller.

Synthetic cases: one complaint with 3 problems (counts 1 complaint / 3 problems); duplicate flag; CLOSE complaint with OPEN problem; problem status date before received date; null BBL matched by borough name + block + lot; BBL vs block/lot conflict; BIN as a number; `BLDG` sentinel; unknown status text gives `other`; tax-lot result gives `lot_level` only; unsupported join makes zero calls; 5xx, 429, short page, dataset refresh mid-fetch, row cap; logs carry no row content.

Live (opt-in): schema header still has every field used; test building complete; missing-BBL rows on 220 W 17th retrieved; privacy metrics survive sanitizing.

## 6. Acceptance criteria

1. Offline tests reproduce the recorded counts per address: problems and complaints separately, per attribution bucket.
2. The 84 no-BBL rows on lot 1007660055 are retrieved and left `unattributed_on_lot`.
3. Tax-lot results yield only `lot_level`; unverified results make zero HTTP calls (as in Task 1).
4. Every problem has `evidence_kind = reported_allegation`, and no summary field calls a complaint a violation.
5. Outcome categories cover at least 95% of recorded closed problems (the rest are `other`, listed in the report, not hidden).
6. No committed fixture carries apartment values or unit text; redaction metrics reproduce from fixtures.
7. All Phase 1 and Task 1 tests keep passing unchanged; CI green; live tests pass.
8. The results report lists real counts, flags, conflicts and the remaining `other` outcomes.

## 7. Decisions needed

- **D5.** Leave `complaint_anonymous_flag` out of public output (kept in evidence only). It is about the complainant, not the building. **Recommended: yes.**
- **D6.** Classify outcomes from HPD's `status_description` templates, with `other` as the fallback and coverage reported. Never link a complaint to a specific violation, because no key exists. **Recommended: yes.**
- **D7.** Keep duplicate-flagged problems, count them separately as "repeat reports of a building-wide condition", and exclude them from "distinct conditions". Never drop them. **Recommended: yes.**
- **D8.** Fixture size: record all 5 addresses (about +3 MB), or drop the 2,653-row condo lot (about +1 MB, condo case covered by Task 1 only). **Recommended: keep all 5.** It is the only multi-page complaint case besides the test building.

## 8. Still tracked before public launch

Rate limiting, CORS, apartment/unit parsing in addresses, GitHub Actions upgrades, NYC Open Data terms-of-use review, optional `SOCRATA_APP_TOKEN`, the Git-history fixture remediation (see `privacy_git_history_remediation.md`), and the pending Task 1 HPD Online validation.

## Appendix: live queries (`ygpa-z7cr`, 2026-10-21)

| Query | Result |
|---|---|
| total / distinct complaints / min-max received | 16,398,305 / 8,940,907 / 2001-12-16 to 2026-10-21 |
| `problem_id` with count > 1 | none (07:2x UTC run) |
| `bbl IS NULL` | 37,466, all with `block` |
| status x status | CLOSE/CLOSE 16,337,187; OPEN/OPEN 39,808; OPEN/CLOSE 17,274; CLOSE/OPEN 36 |
| `problem_duplicate_flag` | N 13,760,416; Y 2,633,889 |
| `type` | EMERGENCY 10,501,295; NON EMERGENCY 5,045,927; IMMEDIATE EMERGENCY 566,682; HAZARDOUS 280,397; REFERRAL 4 |
| test building (bbl 2027540005): problems / complaints / BINs | 912 / 539 / 1 |
| test building `unit_type` | APARTMENT 664; BUILDING 133; BUILDING-WIDE 88; PUBLIC AREA 14; PUBLIC PARTS 13 |
| test building duplicates | 196 Y, all closed |
| test building `unique_key` | 752 distinct; 160 null; 70813572 also in 311 `erm2-nwe9` (HPD, HEAT/HOT WATER, 2026-10-19) |
| test building `problem_status_date < received_date` | 7 |
| lot 1007660055 by bbl/bin | BIN 1081543: 13; 1081544: 1; 1081546: 6; 1081548: 1; no BBL and no BIN: 84 |
| lot counts (problems / complaints) | 2027540005: 912/539; 1007660055: 105/79; 2029930031: 51/48; 1008350041: 10/6; 1018227502: 2,653/1,124 |
| 1018227502 status | OPEN/OPEN 2; CLOSE/CLOSE 2,651 |
