# HPD Online validation checklist (Phase 2, Tasks 1 and 2)

Two separate validations live in this file. **Part A (Task 1, violations)** and **Part B (Task 2, complaints)** have separate statuses; one passing does not close the other.

# Part A: violations (Task 1)

**Status: PENDING.** Nothing here is validated until Sanyam fills in the "HPD Online" columns. This file records his results exactly as reported, including mismatches.

Why manual: `hpdonline.nyc.gov` is blocked from the cloud environment (proxy 403, checked 2026-10-21) and is a JavaScript app.

Our numbers come from NYC Open Data `wvxf-dwi5`, dataset version `Wed, 21 Oct 2026 14:51:46 GMT`, recorded 2026-10-21 (see `backend/tests/fixtures/hpd/violations_cassette.meta.json`). The data updates daily, so small differences after that date are expected. Please write down the date you checked.

How to check: open https://hpdonline.nyc.gov, search the address, open the building's **Violations** tab. For counts, filter to open violations if the page allows it.

For every mismatch, please also note (if HPD Online shows it) the page's "data as of" or last-updated date, so we can tell a timing difference from a real error. When results come in, each mismatch is classified in the reconciliation table at the end. Nothing is dropped or "fixed" to make it match.

## Building 1: 1134 West Farms Road, Bronx (BBL 2027540005, BIN 2006185)

Resolution: building level. All 666 rows are on this BIN.

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Open violations, total (A+B+C+I) | 112 | | |
| Open class A | 9 | | |
| Open class B | 62 | | |
| Open class C | 40 | | |
| Open class I | 1 (Alternative Enforcement Program, violation 18617994) | | |
| All violations, total (open + closed), if shown | 666 | | |
| AEP **order record** in the violations list? | Class I order record 18617994 (§27-2153, "selected to participate in AEP"), status Open / INFO NOV SENT OUT, inspected 2026-02-02. This is an order record only. It does **not** by itself confirm current enrollment. | | |
| AEP **enrollment** shown by HPD Online (separate from the order record) | Not established by Task 1. For comparison only: HPD's separate AEP dataset (`hcir-3275`, last updated 2026-10-01) lists building 122027 as "AEP Active", start 2026-02-02, Round 19. Task 1 does not query that dataset. | | |

| Violation ID | Class | Inspected | Our status / current status | HPD Online status | Match? |
|---|---|---|---|---|---|
| 19311113 | C | 2026-10-19 | Open / NOV SENT OUT | | |
| 19311114 | B | 2026-10-19 | Open / NOV SENT OUT | | |
| 19311116 | C | 2026-10-19 | Open / NOTICE OF ISSUANCE SENT TO TENANT | | |
| 19041332 | C | 2026-06-26 | Close / VIOLATION CLOSED | | |
| 18617994 | I | 2026-02-02 | Open / INFO NOV SENT OUT | | |

## Building 2: 220 West 17th Street, Manhattan (BBL 1007660055, BIN 1081543)

Resolution: building level. The lot holds 8 buildings (218 to 224 West 17th, front and rear). Of 174 rows on the lot: 24 on this BIN, 63 on 5 other BINs, and 87 with **no BBL and no BIN** (HPD building IDs 805523: 61 rows, 30619: 26 rows). We do not attribute those 87 to any building. This check tells us how HPD Online treats them.

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Open violations shown for 220 West 17th Street | 1 on BIN 1081543 (class A) | | |
| Does HPD Online list 220 W 17th as one building, or front/rear separately? | (question) | | |
| Search "224 West 17th Street": open violations shown | 87 unattributed rows exist on the lot, 39 open; which building HPD shows them under is unknown to us | | |

| Violation ID | Class | Inspected | Our status / current status | Our attribution | Which address does HPD Online show it under? | Match? |
|---|---|---|---|---|---|---|
| 19183224 | A | 2026-09-02 | Open / NOV SENT OUT | this building | | |
| 18256506 | B | 2025-09-26 | Close / VIOLATION DISMISSED | this building | | |
| 19308156 | C | 2026-10-18 | Open / NOV SENT OUT | unattributed on lot (no BBL/BIN) | | |
| 19308157 | C | 2026-10-18 | Open / NOV SENT OUT | unattributed on lot (no BBL/BIN) | | |
| 19304640 | C | 2026-10-16 | Open / NOV SENT OUT | other building on lot (BIN 1081548) | | |

## Building 3: 1845 Adam Clayton Powell Jr Blvd, Manhattan (condo billing BBL 1018227502, BIN 1054974)

Resolution: building level on the condo billing lot. The lot holds two buildings: 891 rows on BIN 1054974 (1845 ACP Blvd) and 269 on BIN 1055002 (1851 ACP Blvd).

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Open violations, total, for 1845 | 73 | | |
| Open class A | 7 | | |
| Open class B | 21 | | |
| Open class C | 45 | | |
| Open class I | 0 (2 AEP order records and 1 underlying-conditions order record exist, all closed or dismissed) | | |
| AEP enrollment shown by HPD Online | Not established by Task 1. For comparison only: `hcir-3275` lists 1845 as "AEP Discharged" twice (2018-06-18 and 2022-05-26) | | |
| Does HPD Online show 1845 and 1851 as separate buildings? | We treat them as separate (different BINs) | | |

| Violation ID | Class | Inspected | Our status / current status | HPD Online status | Match? |
|---|---|---|---|---|---|
| 19258242 | C | 2026-09-29 | Open / NOTICE OF ISSUANCE SENT TO TENANT | | |
| 19258243 | A | 2026-09-29 | Open / NOV SENT OUT | | |
| 19258245 | C | 2026-09-29 | Open / NOV SENT OUT | | |
| 19034717 | C | 2026-06-24 | Close / VIOLATION CLOSED | | |
| 14810382 | I | 2022-01-31 | Close / VIOLATION DISMISSED | | |

## Reconciliation (filled in after results arrive)

Each mismatch gets one category:

- **snapshot**: the data changed between our snapshot (2026-10-21 14:51 GMT) and the date checked, e.g. a new inspection or a status change. Re-fetch live to confirm.
- **attribution**: HPD Online files a record under another building or address than we do (likely on the multi-building lot).
- **status interpretation**: we read open/closed differently from HPD Online (e.g. `violationstatus` vs `currentstatus`).
- **definition**: the counts cover different things (e.g. HPD Online excludes class I, or counts problems differently).
- **our bug**: none of the above. It gets fixed, a regression test is added, and the affected tests are rerun.

| Building | Check | Our value | HPD Online value | Date checked | HPD Online "as of" | Category | Resolution |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Results (from Sanyam's report)

_Not yet received. Validation status: PENDING. Task 1 stays open until this section is filled in._


# Part B: complaints (Task 2)

**Status: PENDING.** Added 2026-10-21 after the Task 2 review. Nothing here is validated until Sanyam fills in the "HPD Online" columns. Part A above stays open on its own terms.

Our numbers come from NYC Open Data `ygpa-z7cr` (Housing Maintenance Code Complaints and Problems), dataset version `Wed, 21 Oct 2026 14:10:13 GMT`, recorded 2026-10-21 (see `backend/tests/fixtures/hpd/complaints_cassette.meta.json`). Please write down the date you checked and any "data as of" date HPD Online shows.

How to check: open https://hpdonline.nyc.gov, search the address, and open the building's **Complaints** tab (or the equivalent section).

**Complaints vs problems.** One complaint holds one or more problems. Open Data has one row per *problem*: the test building has 912 problem rows in 539 complaints. HPD Online may list complaints, problems, or complaints with their problems underneath. It may also show only a recent period, or group by building differently. A different number is therefore not automatically an error. Every difference is recorded and classified in the reconciliation table below, using the same categories as Part A (snapshot, attribution, status interpretation, definition, our bug). Nothing is changed to make it match.

What we never show, and will not ask you to compare: apartment numbers, the complainant-anonymity flag, and the 311 request number.

## B1: 1134 West Farms Road, Bronx (BBL 2027540005, BIN 2006185)

All 912 problems (539 complaints) are on this BIN.

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Does HPD Online count complaints, problems, or both? Does it limit the period shown? | (question) | | |
| Complaints, all time | 539 (912 problems) | | |
| Open complaints | 0 (0 open problems) | | |
| Complaints received in 2026 (to 2026-10-21) | 22 | | |
| Complaints received in 2025 | 27 | | |

| Complaint ID | Received | Problems | Our complaint status | Categories | HPD's disposition (our classification) | HPD Online status | Match? |
|---|---|---|---|---|---|---|---|
| 15038142 | 2026-10-19 | 2 | CLOSE | HEAT/HOT WATER, WATER LEAK | 1 no violation found, 1 no access | | |
| 15019620 | 2026-10-12 | 2 | CLOSE | UNSANITARY CONDITION, WATER LEAK | 2 violations issued | | |
| 14975256 | 2026-09-14 | 1 | CLOSE | DOOR/WINDOW | violations issued | | |
| 14915481 | 2026-08-01 | 1 | CLOSE | HEAT/HOT WATER | duplicate of a building-wide complaint | | |

## B2: 220 West 17th Street, Manhattan (BBL 1007660055, BIN 1081543)

The lot holds 8 buildings. Of 105 problems (79 complaints) on the lot: 13 problems / 10 complaints on this BIN, 8 / 8 on other BINs, and 84 / 61 with **no BBL and no BIN**. We do not attribute those 61 complaints to any building. This check shows where HPD Online puts them.

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Complaints shown for 220 West 17th Street | 10 on BIN 1081543 (13 problems) | | |
| Open complaints for 220 West 17th Street | 5 | | |
| Complaints received in 2026 for 220 West 17th Street | 7 | | |
| Open complaints on the lot with no BBL/BIN: which address does HPD Online show them under? | 13 open of 61 (unattributed by us) | | |

| Complaint ID | Received | Our attribution | Our complaint status | Categories | Which address does HPD Online show it under? | HPD Online status | Match? |
|---|---|---|---|---|---|---|---|
| 15041183 | 2026-10-20 | this building | OPEN | GENERAL | | | |
| 15041170 | 2026-10-20 | this building | OPEN | HEAT/HOT WATER (duplicate of a building-wide complaint) | | | |
| 15042281 | 2026-10-20 | unattributed on lot (no BBL/BIN) | OPEN | HEAT/HOT WATER | | | |
| 15041535 | 2026-10-20 | unattributed on lot (no BBL/BIN) | OPEN | GENERAL | | | |

## B3: 1845 Adam Clayton Powell Jr Blvd, Manhattan (condo billing BBL 1018227502, BIN 1054974)

Two buildings on the billing lot: 1,717 problems / 747 complaints on BIN 1054974 (1845), and 936 / 377 on BIN 1055002 (1851).

| Check | Our value | HPD Online | Match? |
|---|---|---|---|
| Date you checked | | | |
| Complaints for 1845, all time | 747 (1,717 problems) | | |
| Open complaints for 1845 | 1 (2 open problems) | | |
| Complaints received in 2026 for 1845 | 10 | | |
| Does HPD Online show 1845 and 1851 separately? | We treat them as separate (different BINs) | | |

| Complaint ID | Received | Problems | Our attribution | Our complaint status | Categories | HPD Online status | Match? |
|---|---|---|---|---|---|---|---|
| 14947909 | 2026-08-25 | 2 | this building (1845) | OPEN | ELECTRIC, GENERAL | | |
| 14979335 | 2026-09-17 | 3 | this building (1845) | CLOSE | FLOORING/STAIRS, PLUMBING, UNSANITARY CONDITION | | |
| 14965812 | 2026-09-04 | 4 | this building (1845) | CLOSE | PAINT/PLASTER, PLUMBING, UNSANITARY CONDITION, WATER LEAK | | |
| 15016775 | 2026-10-09 | 5 | other building on lot (1851) | CLOSE | DOOR/WINDOW, PAINT/PLASTER, PLUMBING, UNSANITARY CONDITION | | |

## Part B reconciliation (filled in after results arrive)

| Building | Check | Our value | HPD Online value | Date checked | HPD Online "as of" | Category | Resolution |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Part B results (from Sanyam's report)

_Not yet received. Validation status: PENDING. Part A and Part B are tracked separately; results for one do not settle the other._
