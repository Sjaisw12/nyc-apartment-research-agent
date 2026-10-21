# Phase 2, Task 1: HPD violations integration (design for approval)

Status: **design only, no implementation code yet.** Written 2026-10-21 on branch `claude/project-thread-5d0l0f`, which starts from Phase 1 head `6d76ae1`.

Every number in this document comes from live queries against `data.cityofnewyork.us` run on 2026-10-21 between 07:19 and 07:30 UTC. The exact queries are listed in the appendix so they can be re-run.

## 1. Objective in plain language

Phase 1 tells us *which* lot or building an address is. Task 1 takes that answer and collects the building's **HPD housing-code violations** in a way we can defend:

- every row we show can be traced back to a specific official record and query,
- we never attach a record to a building unless the resolver allowed that join,
- "we found nothing" is never confused with "we could not check".

Task 1 covers **HPD violations only** (`wvxf-dwi5`). Complaints, the HPD buildings list and registrations come in later tasks (section 9), so each piece is built, tested and reviewed on its own.

## 2. Data-source review

### 2.1 HPD datasets considered for Phase 2

| Dataset | ID | Rows | Updated | Keys present | Notes |
|---|---|---|---|---|---|
| Housing Maintenance Code Violations | `wvxf-dwi5` | 11,328,583 | Daily (last 2026-10-20 14:55 UTC) | `violationid` (unique), `bbl` (text), `bin` (text), `boroid`+`block`+`lot`, `buildingid`, `registrationid` | **Task 1** |
| Housing Maintenance Code Complaints and Problems | `ygpa-z7cr` | 16,394,305 problem rows | Daily | `problem_id` (unique), `complaint_id`, `bbl` (number), `bin` (number), `borough` (name) + `block` + `lot`, `building_id` | Task 2. One complaint = many problem rows. |
| Buildings Subject to HPD Jurisdiction | `kj4p-ruqc` | ~380k | Monthly (rows last 2026-08-12) | `buildingid`, `bin`, `boroid`+`block`+`lot`; **no `bbl` column** | Task 3. Unit counts, lifecycle, record status. |
| Multiple Dwelling Registrations (+ contacts `feu5-w2e2`) | `tesw-yqqr` | | Monthly | `registrationid`, `buildingid`, `bin`, `boroid`+`block`+`lot` | Phase 2 later task (ownership). Contains personal names. |

Both daily datasets lag about one day: newest violation inspection date is 2026-10-19, newest complaint 2026-10-20.

### 2.2 Violations schema (from `X-SODA2-Fields` / `X-SODA2-Types` headers)

41 fields. The ones we use:

| Group | Fields | Type |
|---|---|---|
| Identity | `violationid`, `novid`, `ordernumber`, `buildingid`, `registrationid` | number / text |
| Location keys | `bbl`, `bin`, `boroid`, `block`, `lot` | text / number |
| Location detail | `apartment`, `story` | text (**sensitive, see 5.6**) |
| What | `class` (A, B, C, I), `novdescription`, `novtype`, `rentimpairing` (Y/N) | text |
| Dates | `inspectiondate`, `approveddate`, `novissueddate`, `originalcorrectbydate`, `newcorrectbydate`, `originalcertifybydate`, `newcertifybydate`, `certifieddate`, `currentstatusdate` | `floating_timestamp` (no time zone) |
| Status | `violationstatus` (Open/Close), `currentstatus` (23 values), `currentstatusid` | text / number |

Socrata leaves null fields out of the JSON row, so "field missing" must be read as null, not as a schema change. Schema drift is detected from the `X-SODA2-Fields` header instead.

### 2.3 Access, pagination and freshness

- No auth needed. Optional app token goes in `X-App-Token`. No rate-limit headers are returned (`x-ratelimit-*` absent), so we rely on 429 + backoff, which `http_client.py` already does.
- Default page is 1,000 rows. Paging needs `$limit` + `$offset` + a **unique** `$order` key. `violationid` is unique (a global `GROUP BY violationid HAVING count(*)>1` returned zero rows), so ordering by it gives stable pages.
- Freshness headers are present on every response: `Last-Modified`, `X-SODA2-Truth-Last-Modified`, `X-SODA2-Data-Out-Of-Date: false`, `ETag`. We store `X-SODA2-Truth-Last-Modified` as the "data as of" time and compare it across pages (5.3).

## 3. Data-quality findings (live)

These are measured, not assumed. Each one becomes a test.

1. **10,665 violations have no `bbl` and no `bin`** but do have `boroid`/`block`/`lot`. 3,424 of them are **Open**, and the newest is from 2026-10-18, so this is ongoing, not legacy. A query on `bbl` alone silently misses them.
2. **1,143 rows have `bbl = "0"`** (Manhattan 158, Bronx 319, Brooklyn 469, Queens 197). The samples are condo billing lots (lot 7501 to 7503) with a real BIN. `normalize_bbl` already rejects block 0; these rows are reachable only through `boroid`/`block`/`lot`.
3. **One BBL can hold many buildings.** Manhattan lot `1009720001` has violations on 35 different BINs; HPD's buildings list has 37 BINs there. For a tax-lot-level resolution, everything on that lot is lot-level evidence, not evidence about one building.
4. **Rows on a multi-building lot can lack a BIN.** Lot `1007660055` has violations on six BINs plus 87 rows with no BBL and no BIN (newest 2026-10-18). Even at building level, those 87 cannot be attributed to a specific building.
5. **Two status fields sometimes disagree.** `violationstatus` and `currentstatus` mostly line up, but not always: 64 rows are `Close` with `NOV SENT OUT`, 15 are `Open` with `VIOLATION CLOSED`, 22 are `Open` with `VIOLATION DISMISSED`, 57,143 are `Close` with `VIOLATION WILL BE REINSPECTED`. We do not know which field HPD Online uses, so we keep both and flag the disagreement instead of picking one silently.
6. **Impossible dates.** Minimum `inspectiondate` is `0217-07-05` (23 rows before 1960), and 117 rows have `currentstatusdate` after 2027 (max `9999-12-11`). Raw value kept, parsed value set to null, row flagged.
7. **Dates have no time zone** (`floating_timestamp`). We treat them as New York local dates, not UTC.
8. **Class I** (806,087 rows) is informational, not a hazard class. It must not be summed with A/B/C as "violations".
9. **Apartment numbers are common.** For the test building, 536 of 666 violations carry an `apartment` value. That is unit-level detail we should not echo into fixtures, logs or the public report by default.
10. **Mixed key types across HPD datasets.** `bbl` is text in violations and a number in complaints; HPD buildings has no `bbl` at all; complaints use a borough *name*, violations a borough *id*. The query layer formats keys per dataset.
11. **Condo lots.** 335,409 violations are on lots 7501+ (condo billing lots). GeoSearch/PAD can return the billing BBL for condos. Whether HPD files a condo's violations under the billing lot, the unit lots or both is **not yet checked**; Task 1 will measure it on one condo building and report, not guess.

## 4. How Phase 1's join rules apply

The resolver exposes `permitted_join_keys`:

| Resolver result | `permitted_join_keys` | What Task 1 may do |
|---|---|---|
| verified, building level | `["bbl", "bin"]` | Fetch by lot, then use the BIN to **sort** rows into "this building" and "other / unknown building on the same lot". |
| verified, tax-lot level | `["bbl"]` | Fetch by lot. Every row is labelled **lot-level**. The BIN on a row is shown as source data but is **never** used to claim the row is about the user's building. |
| ambiguous / no match / unavailable | `[]` | No query at all. Status `unsupported_join`. |

Design choices that make this hard to get wrong:

- **We never query violations by BIN.** All fetches are by lot (`bbl`, plus `boroid`/`block`/`lot`, which is the same identifier split into parts; see decision D1). BIN is only ever applied *after* fetching, and only when the resolver granted it. So a tax-lot result has no code path that can produce a BIN join.
- **The join policy is one small function** that takes a `ResolutionResult` and returns a query plan or raises `UnsupportedJoinError`. Every adapter must go through it. Tests check that a tax-lot result with a BIN present still produces zero building-attributed rows.
- **Placeholder BINs** (`n000000`, seen on 32,964 violation rows) are never treated as a building identity, even at building level.

## 5. Proposed architecture

```
ResolutionResult ──► join_policy.plan_hpd(result) ──► LotQuery(boroid, block, lot, bbl, building_bin|None)
                                                          │
                                   socrata_pager.fetch_all(dataset, where, order="violationid")
                                   (count → pages → completeness + snapshot checks)
                                                          │
                                         raw rows + RetrievalProvenance
                                                          │
                              hpd_violations.normalize(rows) ──► NormalizedViolation[] + QualityFlags
                                                          │
                              attribution.partition(rows, building_bin) ──► this_building / other_building / unattributed
                                                          │
                                              SourceResult (status, records, summary, provenance)
```

### 5.1 New / changed files

| File | Purpose |
|---|---|
| `backend/src/nyc_apartment_agent/records/models.py` | `RetrievalStatus`, `JoinScope`, `Attribution`, `QualityFlag`, `RetrievalProvenance`, `NormalizedViolation`, `SourceResult` (Pydantic). |
| `backend/src/nyc_apartment_agent/records/join_policy.py` | Turns a `ResolutionResult` into a lot query, or raises `UnsupportedJoinError`. |
| `backend/src/nyc_apartment_agent/sources/socrata_pager.py` | Generic bounded pagination with count check and snapshot check. Reused by later tasks. |
| `backend/src/nyc_apartment_agent/sources/hpd.py` | Add the lot query (`bbl` OR `boroid`/`block`/`lot`). Existing functions stay for the smoke test. |
| `backend/src/nyc_apartment_agent/records/hpd_violations.py` | Normalizer: parse dates, statuses, class, flags; keep source IDs and raw row hash. |
| `backend/src/nyc_apartment_agent/records/attribution.py` | Sort rows into this building / other building / unattributed, only when allowed. |
| `backend/scripts/record_hpd_fixtures.py` | Records live responses into redacted, hashed fixtures (same pattern as Phase 1). |
| `backend/tests/unit/test_join_policy.py`, `test_socrata_pager.py`, `test_hpd_violations.py`, `test_attribution.py` | Offline tests. |
| `backend/tests/live/test_live_hpd.py` | Opt-in live checks (`RUN_LIVE_TESTS=1`). |
| `docs/source_registry.md` | Add the verified violation schema, quirks and status mapping. |

No API endpoint, database, agent or frontend work in Task 1.

### 5.2 Retrieval status (what the user and the agent will see)

One status per source per building, never inferred from an empty list:

| Status | Meaning | Example trigger |
|---|---|---|
| `records_found` | Complete fetch, at least one row. | Normal case. |
| `no_records_found` | Complete fetch, count was 0. | A lot with no HPD history. |
| `source_unavailable` | Timeout, 5xx, 429 after retries, bad JSON. | `SourceError` from `http_client`. |
| `incomplete_pagination` | We got some rows but cannot prove we got all. | Fetched ≠ count, a page failed mid-way, data changed between pages, or the row cap was hit. Partial rows are kept but marked partial. |
| `unsupported_join` | The resolver did not permit the join. | Ambiguous address, or BIN requested at tax-lot level. |

**Conflicts** are not a fetch status, because a complete fetch can still contain contradictions. They are recorded as `QualityFlag`s on the row (e.g. `status_fields_disagree`, `duplicate_id_different_content`) and counted in the summary, so the report can say "2 records have conflicting status fields".

### 5.3 Pagination and completeness

1. `count(*)` with the same `$where`. Record `X-SODA2-Truth-Last-Modified`.
2. Pages of 1,000 ordered by `violationid`, `$offset` stepping by page size.
3. Complete only if: every page succeeded, rows fetched = count, all `violationid`s unique, and the truth-last-modified header is the same on the count call and the last page. If the dataset refreshed mid-fetch, retry the whole fetch once, then return `incomplete_pagination`.
4. Hard cap: 10,000 rows per building per source (the largest Manhattan lot seen has ~600). Above the cap: `incomplete_pagination` with the count, never a silent truncation. Cap and page size come from `config.py`.

### 5.4 Normalized violation record

Kept from the source unchanged: `violationid`, `novid`, `ordernumber`, `buildingid`, `registrationid`, `bbl`, `bin`, `boroid`, `block`, `lot`, `class`, `novdescription`, `novtype`, `violationstatus`, `currentstatus`, `currentstatusid`, `rentimpairing`, and every date as the raw string.

Added by us (clearly separate from source fields):

- parsed dates (`date` objects, New York local; null if implausible, with a flag),
- `is_open: bool | None`, from `violationstatus` (the field HPD documents as open/closed); `currentstatus` is kept beside it, and disagreement sets `status_fields_disagree`,
- `is_informational: bool` for class I,
- `location_scope`: `unit` if `apartment` is present, `building` otherwise (the unit number itself is not copied, see 5.6),
- `attribution`: `this_building`, `other_building_on_lot`, `unattributed_on_lot`, or `lot_level` (tax-lot result),
- `quality_flags: list[QualityFlag]`,
- `raw_sha256`: hash of the original row, so any normalized record can be checked against its raw source.

### 5.5 Duplicates and history

- `violationid` is unique in the dataset today. The two query paths (by `bbl` and by `boroid`/`block`/`lot`) overlap, so we de-duplicate on `violationid`; identical rows collapse, different content under the same ID is flagged as a conflict and both raw rows are kept.
- Nothing is dropped for being old or closed. The summary separates open vs closed, class A/B/C vs I, and recent (last 12 months, last 3 years) vs older, using the inspection date. "Open" counts carry the data-as-of timestamp.

### 5.6 Privacy and evidence

- **Raw evidence:** full raw rows are kept server-side with provenance (dataset, query URL, fetch time, truth-last-modified, row hash). In Task 1 this is an in-memory/JSON structure; Postgres storage is a later Phase 2 task.
- **Fixtures in GitHub:** `apartment` and `story` are removed before a fixture is written; the meta file lists the removed fields and the hash of the unredacted response, so the redaction is visible and honest.
- **Logs:** log counts, dataset IDs and BBLs only, never row contents.
- **Normalized output:** carries `location_scope` (unit vs building) but not the unit number. Whether the report should ever show unit numbers is a product decision for later (D2).

## 6. Tests

Recorded official responses (redacted as above) for:

- **1134 West Farms Rd, Bronx** (`2027540005`, building level): 666 rows, 112 open. Fetched with a small page size in the test so pagination is exercised over several pages.
- **Lot `1007660055`, Manhattan**: six BINs plus 87 rows with no BBL/BIN. Exercises the `boroid`/`block`/`lot` path and the "unattributed on lot" bucket.
- **One lot with zero violations**: `no_records_found`.
- **One condo billing lot** (to be picked during recording): measures finding 11.

Synthetic cases (built in the test, clearly named as synthetic): 429 then success; 500 on page 2; fetched ≠ count; truth-last-modified changes between pages; duplicate `violationid` with different content; status disagreement; year 0217 and 9999 dates; `bbl = "0"`; placeholder BIN; tax-lot result with BIN present; ambiguous result; missing optional fields.

Live opt-in tests re-run the counts and check that the schema header still contains every field we rely on.

## 7. Validation against an independent official source

HPD Online (`hpdonline.nyc.gov`) is the official public view of the same data, but it is **blocked from this cloud environment** (proxy 403, checked today), and it is a JavaScript app that a web fetcher cannot read. So independent validation needs you:

- I will produce a short checklist for 3 buildings (open count by class, and 5 specific violation IDs with dates and status).
- You check them on HPD Online and note matches/mismatches.
- I record the results as-is in `data/eval/hpd_validation.md`, including any mismatch. I will not mark validation done without your results.

## 8. Acceptance criteria for Task 1

1. For the two recorded buildings, offline tests reproduce the live counts exactly (666 / 112 open for `2027540005`; per-BIN and unattributed counts for `1007660055`).
2. A tax-lot resolution never yields a row with `attribution = this_building`, even when the row and the resolver both have the same BIN (tested).
3. Ambiguous / no-match / unavailable resolutions make **zero** HTTP calls and return `unsupported_join` (tested with a transport that fails on any call).
4. Each of the five statuses has at least one test that triggers it, and an empty list is never returned without `no_records_found` and a count of 0.
5. Rows with null `bbl` or `bbl = "0"` on the lot are retrieved (not lost).
6. Every normalized record has `violationid`, dataset ID, query URL, fetch time, data-as-of time and `raw_sha256`; re-hashing the stored raw row matches.
7. Implausible dates and status disagreements are flagged, never silently fixed.
8. No committed fixture contains `apartment` or `story` values (a test scans the fixture folder).
9. `ruff`, formatting and the full offline test suite pass locally and in GitHub Actions; live tests pass when run with `RUN_LIVE_TESTS=1`.
10. A short results report lists actual test output, data-quality findings, and anything that did not work.
11. HPD Online validation results recorded exactly as you report them (may complete after the code).

## 9. Proposed order for the rest of Phase 2

1. **Task 1 (this): HPD violations.**
2. Task 2: HPD complaints/problems (complaint vs problem counting, duplicate flag, borough-name keys).
3. Task 3: HPD buildings + registrations (building context, unit counts; registration contacts handled with the privacy rules above).
4. Task 4: PostgreSQL storage for raw evidence and normalized records, with migrations.
5. Task 5: DOB and 311 sources (BIN-only sources follow `permitted_join_keys` strictly).

## 10. Decisions I need from you

- **D1.** Treat `boroid`+`block`+`lot` as the same identifier as BBL (it is the BBL split into parts), so it is allowed whenever `bbl` is permitted. Without it we miss 10,665 violations, 3,424 of them open. **Recommended: yes.**
- **D2.** Keep apartment/story out of fixtures, logs and normalized output for now; decide later whether the report shows unit numbers. **Recommended: yes.**
- **D3.** Row cap of 10,000 per building per source, with `incomplete_pagination` above it. **Recommended: yes.**
- **D4.** Validation against HPD Online is done by you from my checklist, because this environment cannot reach it. **Recommended: yes.**

## 11. Tracked before public launch (not in Task 1)

Rate limiting on our API, CORS policy, apartment/unit parsing in addresses, GitHub Actions version upgrades, NYC Open Data terms-of-use review, and the optional `SOCRATA_APP_TOKEN`.

## Appendix: live queries run on 2026-10-21

All against `https://data.cityofnewyork.us/resource/<id>.json`, HTTP 200.

| Query (`wvxf-dwi5` unless noted) | Result |
|---|---|
| `$select=count(*)` | 11,328,583 |
| `$select=violationstatus,count(*)&$group=violationstatus` | Close 8,451,389; Open 2,877,194 |
| `$where=bbl IS NULL` count | 10,665 (same for `bin IS NULL`) |
| null `bbl`, grouped by status | Close 7,241 (max inspection 2026-09-19); Open 3,424 (max 2026-10-18) |
| `$where=bbl='0'` grouped by `boroid` | 1: 158, 2: 319, 3: 469, 4: 197 (1,143); samples are condo lots 7501–7503 with real BINs |
| `bin in (n000000)` grouped | 1000000: 7,435; 2000000: 7,848; 3000000: 12,144; 4000000: 4,708; 5000000: 829 |
| `class` grouped | A 2,586,727; B 5,316,660; C 2,619,109; I 806,087 |
| `violationid` with count > 1 | none |
| status × currentstatus | 40 combinations; disagreements listed in finding 5 |
| `rentimpairing` | N 10,549,581; Y 779,002 |
| min/max `inspectiondate` | 0217-07-05 / 2026-10-19 |
| max `currentstatusdate` | 9999-12-11 (117 rows after 2027-01-01) |
| `inspectiondate < 1960-01-01` | 23 |
| `boroid=1`, BBLs with >1 BIN | `1009720001` 35 BINs (626 rows), `1009780001` 21, `1012420009` 18, `1006030001` 18 |
| `lot >= 7501` | 335,409 |
| `bbl='2027540005'` by BIN/status | BIN 2006185 only: Open 112, Close 554; 536 rows have `apartment` |
| `boroid=1 AND block=766 AND lot=55` by bbl/bin | 6 BINs (9–24 rows each) + 87 rows with no BBL/BIN |
| `ygpa-z7cr` statuses | CLOSE/CLOSE 16,337,187; OPEN/OPEN 39,808; OPEN/CLOSE 17,274; CLOSE/OPEN 36 |
| `ygpa-z7cr` `bbl IS NULL` | 37,466 (all have `block`) |
| `ygpa-z7cr` for `2027540005` | 539 complaints, 912 problem rows, 1 BIN |
| `kj4p-ruqc` for 2/2754/5 | buildingid 122027, BIN 2006185, 44 class A units, Active, registrationid 203625 |
| `kj4p-ruqc` for 1/972/1 | 38 rows, 37 BINs |
| Headers on a violations query | `X-SODA2-Truth-Last-Modified: Tue, 20 Oct 2026 14:55:26 GMT`, `X-SODA2-Data-Out-Of-Date: false`, `ETag` present, no rate-limit headers |
| `https://hpdonline.nyc.gov` | blocked by this environment's proxy (403) |
