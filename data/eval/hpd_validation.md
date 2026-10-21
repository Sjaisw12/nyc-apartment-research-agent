# HPD Online validation checklist (Phase 2, Task 1)

**Status: PENDING.** Nothing here is validated until Sanyam fills in the "HPD Online" columns. This file records his results exactly as reported, including mismatches.

Why manual: `hpdonline.nyc.gov` is blocked from the cloud environment (proxy 403, checked 2026-10-21) and is a JavaScript app.

Our numbers come from NYC Open Data `wvxf-dwi5`, dataset version `Wed, 21 Oct 2026 14:51:46 GMT`, recorded 2026-10-21 (see `backend/tests/fixtures/hpd/violations_cassette.meta.json`). The data updates daily, so small differences after that date are expected. Please write down the date you checked.

How to check: open https://hpdonline.nyc.gov, search the address, open the building's **Violations** tab. For counts, filter to open violations if the page allows it.

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
| Is the building shown as in the Alternative Enforcement Program (AEP)? | AEP order record 18617994, Open, "INFO NOV SENT OUT", inspected 2026-02-02 | | |

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
| Open class I | 0 (2 AEP and 1 underlying-conditions order records exist, all closed or dismissed) | | |
| Does HPD Online show 1845 and 1851 as separate buildings? | We treat them as separate (different BINs) | | |

| Violation ID | Class | Inspected | Our status / current status | HPD Online status | Match? |
|---|---|---|---|---|---|
| 19258242 | C | 2026-09-29 | Open / NOTICE OF ISSUANCE SENT TO TENANT | | |
| 19258243 | A | 2026-09-29 | Open / NOV SENT OUT | | |
| 19258245 | C | 2026-09-29 | Open / NOV SENT OUT | | |
| 19034717 | C | 2026-06-24 | Close / VIOLATION CLOSED | | |
| 14810382 | I | 2022-01-31 | Close / VIOLATION DISMISSED | | |

## Results (to be filled from Sanyam's report)

_Not yet received._
