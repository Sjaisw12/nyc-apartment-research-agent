# Source registry

Sources this code calls today, verified live from this repo's code on 2026-10-21. The fuller
survey of all twelve planned sources lives in the project notes
(`phase1/data_sources_verified.md`); it will move here as each adapter is built.

| Source | Endpoint | Auth | Keys we use | Freshness signal | Verified |
|---|---|---|---|---|---|
| NYC Planning GeoSearch v2 | `https://geosearch.planninglabs.nyc/v2/search?text=&size=` | none | `properties.addendum.pad.bbl`, `.bin`, `.version` (PAD release, `26c` today) | PAD version per result | 200 OK, ~0.05–0.5 s |
| HPD Housing Maintenance Code Violations | `https://data.cityofnewyork.us/resource/wvxf-dwi5.json` | none; optional `X-App-Token` | `bbl` OR `boroid`+`block`+`lot` (Phase 2); `bin` only to sort rows after fetching | `Last-Modified` / `X-SODA2-Truth-Last-Modified` header (Tue, 20 Oct 2026 14:55 GMT) | 200 OK; 666 rows for test BBL |
| HPD Complaints and Problems | `https://data.cityofnewyork.us/resource/ygpa-z7cr.json` | none; optional `X-App-Token` | `bbl` OR `borough` (name) + `block` + `lot` (Phase 2); `bin` only to sort rows after fetching | `X-SODA2-Truth-Last-Modified` header (Wed, 21 Oct 2026 14:10 GMT) | 200 OK; 912 problem rows / 539 complaints for test BBL |

Used only by the evaluation-evidence script (not by the app): PLUTO `64uk-42ks`,
Building Footprints `5zhs-2jue`, Buildings Subject to HPD Jurisdiction `kj4p-ruqc`.

## Response headers observed

- Socrata returns `X-SODA2-Fields`, `X-SODA2-Types`, `X-SODA2-Truth-Last-Modified`,
  `X-SODA2-Data-Out-Of-Date`, `Last-Modified`, `ETag`, `X-Socrata-RequestId`.
- **No rate-limit headers** (`X-RateLimit-*`, `Retry-After`) on either source in normal use.
  We therefore cannot read a quota; the client relies on 429 + bounded backoff.
- GeoSearch returns `etag`, `cache-control`, and no freshness or rate-limit headers.

## Behaviour that shaped the code

1. **GeoSearch always returns something.** `99999 Broadway` and `Broadway, Manhattan` both
   return five real Broadway buildings with `match_type: fallback`, `confidence: 0.8`, the same
   values a correct match gets. Confidence cannot be used to detect a wrong house number; the
   resolver must compare house number, street and borough itself.
2. **Borough in the text is not a filter.** `1 Centre St Manhattan` also returns Bronx
   results; `1134 West Farms Rd, Brooklyn` returns the Bronx building as top result.
3. **Place names can outrank addresses.** `1 Center St` returns "JAMAICA 1 PREK CENTER" first.
4. **Aliases duplicate candidates** (`100 B'WAY` / `100 BROADWAY`, `CENTER` / `CENTRE`) with the
   same BBL/BIN. Dedupe by (BBL, BIN).
5. **One lot can hold several buildings** (e.g. 7 BINs on `4005280050`, 8 on `3044520001`).
   BBL alone is not a building.
6. **Condos:** `55 Austin Place` resolves to billing lot `5005797501`, while Building Footprints
   places its BIN on base lot `5005790002`. Different datasets may key the same building
   differently.
7. **Socrata omits null fields** in JSON rows, so schema checks look across rows, not per row.
8. Placeholder BINs: see [bin_2000000_investigation.md](bin_2000000_investigation.md).

## HPD violations (`wvxf-dwi5`), Phase 2 Task 1

Measured live on 2026-10-21. Full detail: [phase2_task1_hpd_violations_design.md](phase2_task1_hpd_violations_design.md).

- 41 fields; dates are `floating_timestamp` (no time zone, treated as New York local).
- `violationid` is unique across 11.3M rows, so it is the paging order key.
- 10,665 rows have no `bbl` and no `bin` (3,424 open, still occurring); 1,143 have `bbl = "0"`. All have `boroid`/`block`/`lot`, so the lot is queried by both forms.
- `violationstatus` is Open/Close. `currentstatus` has 23 values; a few rows contradict the two (e.g. Open + VIOLATION CLOSED). Clear contradictions are flagged, nothing is silently picked.
- Impossible dates exist (`inspectiondate` 0217-07-05, `currentstatusdate` 9999-12-11).
- Class I = information/orders (registration failures, but also vacate orders, relocation, AEP, underlying conditions). Order numbers mapped in `records/hpd_violations.py`.
- `apartment` and `story` are populated on most rows, and `novdescription` repeats them ("APT 1H, 2nd STORY"). Both are redacted from public output.
- Condos (2 checked): HPD files violations under the billing lot (75xx) with each building's own BIN.

## HPD complaints and problems (`ygpa-z7cr`), Phase 2 Task 2

Measured live on 2026-10-21. Full detail: [phase2_task2_hpd_complaints_design.md](phase2_task2_hpd_complaints_design.md) and [phase2_task2_report.md](phase2_task2_report.md).

- One row per **problem**; `complaint_id` groups problems. 16.4M problems, 8.9M complaints. `problem_id` is unique and is the paging order key.
- The lot's borough is a **name** (`BRONX`), not an id. 37,466 rows have no `bbl`, so the lot is queried by both forms.
- HPD's outcome is free text (`status_description`); 24 templates on the 5 test lots. No key links a complaint to a violation.
- `unique_key` is a 311 request number (withheld from public output; kept in evidence for Task 5). `complaint_anonymous_flag` is withheld. `apartment` is redacted.
- Some rows have a status date before the received date; all observed cases are "received late December, closed early January of the same year", which looks like a year-entry error. Flagged, never corrected.

## Usage conditions (to finish before deployment)

- **Socrata / NYC Open Data:** app token optional; without one, requests "may be subject to
  throttling" (Socrata docs). The NYC Open Data Terms of Use page could not be opened from this
  environment (`opendata.cityofnewyork.us` blocked). Review it before launch. Dataset metadata
  lists no license field for these datasets.
- **GeoSearch:** the docs ask clients to throttle autocomplete requests. No license or terms
  text was found on its docs page, and `/attribution` returned a "page not found" page.
  Confirm attribution requirements with NYC Planning Labs before launch.
- We link back to source records and do not rehost personal data. HPD rows include apartment
  numbers; registration contacts (later phase) include individual names.

## How to re-verify

```bash
cd backend
uv run python scripts/smoke_sources.py         # live, ~9 requests, rewrites fixtures
RUN_LIVE_TESTS=1 uv run pytest tests/live -v  # live assertions
```
