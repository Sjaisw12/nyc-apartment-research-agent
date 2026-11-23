# Phase 2, Task 3: HPD buildings and registrations (design for approval)

Status: **design only, no implementation.** Written 2026-10-21 on branch `claude/project-thread-5d0l0f`, after Task 2 was conditionally accepted. No Task 3 code will be written until the Task 2 corrections are reviewed and this design is approved.

All numbers come from live queries against `data.cityofnewyork.us` on 2026-10-21 (about 23:35 to 23:45 UTC). The queries are listed in the appendix.

## 1. Objective

Answer three questions about a resolved lot or building, using HPD's own records and the same guarantees as Tasks 1 and 2: Phase 1 join rules, the five retrieval statuses, attribution buckets, provenance, and privacy.

1. **What HPD knows about the building:** HPD building ID, legal class A/B units, legal stories, DOB building class, management program, lifecycle and record status.
2. **Whether a registration is on file:** registration ID, last registration date, registration end date.
3. **Who is registered as responsible:** corporate owner, agent, managing agent and similar roles, subject to the privacy decision D11.

These are **administrative records**. They are not findings about conditions in the building, and not allegations either. Task 3 adds a third evidence kind, `administrative_record`.

## 2. Data sources

| Dataset | ID | Rows | Last updated | Stated frequency | Unique key |
|---|---|---|---|---|---|
| Buildings Subject to HPD Jurisdiction | `kj4p-ruqc` | 380,119 | **2026-08-12 14:57 GMT** | Monthly | `buildingid` (380,119 distinct) |
| Multiple Dwelling Registrations | `tesw-yqqr` | 203,887 | **2026-08-12 15:00 GMT** | Monthly | none: 194,218 registration IDs, 203,811 building IDs |
| Registration Contacts | `feu5-w2e2` | 810,494 | **2026-08-12 15:01 GMT** | Monthly | `registrationcontactid` |

Fields used:
- **Buildings:** `buildingid`, `boroid`, `block`, `lot`, `bin`, `housenumber`, `lowhousenumber`, `highhousenumber`, `streetname`, `zip`, `managementprogram`, `dobbuildingclass`, `legalstories`, `legalclassa`, `legalclassb`, `registrationid`, `lifecycle`, `recordstatus`.
- **Registrations:** `registrationid`, `buildingid`, `boroid`, `block`, `lot`, `bin`, `lastregistrationdate`, `registrationenddate`.
- **Contacts:** `registrationcontactid`, `registrationid`, `type`, `contactdescription`, `corporationname`, `title`, `firstname`, `middleinitial`, `lastname`, `businesshousenumber`, `businessstreetname`, `businessapartment`, `businesscity`, `businessstate`, `businesszip`.

**None of the three datasets has a `bbl` field.** The lot is queried by `boroid` + `block` + `lot` only. `bin` is a number.

## 3. Data-quality and interpretation findings (live)

1. **The snapshot is stale.**
   - All three datasets were last updated on 2026-08-12, although they are listed as monthly. That is more than two months before today (2026-10-21), so at least one monthly update appears to be missing.
   - The newest `lastregistrationdate` in the whole dataset is 2026-07-31.
2. **Registration end dates fall right after the snapshot.**
   - 130,690 registrations end on **2026-09-01**, including every registration on the five test lots. 23,560 already end on 2027-09-01 (renewed before the snapshot).
   - Read naively, almost every test building is "expired" today. But the renewal season runs from July to September, which is exactly when the snapshot stops. The data cannot show whether these buildings renewed.
   - **Rule (D10):** never say "expired" or "unregistered" when the end date is later than the snapshot date, or when the snapshot is older than the renewal it would need to show. Say what the record shows, and say the data is stale.
3. **Registrations are not one per building.** 194,218 registration IDs cover 203,811 building IDs (one registration can cover several buildings), and about 76 buildings have two registration rows. The join is `buildingid` + `registrationid`, never the address.
4. **The HPD building ID maps the "unattributed" rows on 220 W 17th to real buildings.**
   - On lot 1007660055 the buildings dataset lists 8 buildings, each with its own BIN and HPD building ID.
   - The rows that Tasks 1 and 2 leave `unattributed_on_lot` (no BBL, no BIN) all carry an HPD building ID:
     - violations: 61 on 805523, 26 on 30619;
     - complaints: 54 on 805523, 30 on 30619.
   - These map to BIN 1081547 (224 Front) and BIN 1081549 (218 Front). **None maps to 220 Front (BIN 1081543), the requested building.**
   - So the building ID is a real, HPD-internal key that could attribute those rows (D9). This would change Task 1 and Task 2 counts, so it needs approval.
5. **Placeholder and missing BINs in the buildings dataset:** 11,989 records have no BIN and 5,589 carry a borough placeholder BIN (1000000, 2000000, ...). Those records can only be lot-level.
6. **Tax-lot case (1217 West Farms):**
   - Phase 1 resolved this address to the lot only, because PAD gives the placeholder BIN 2000000.
   - HPD lists one building on the lot, **1017 Home Street, BIN 2128717**, with 63 legal class A units and an active registration.
   - Task 3 must not treat that as the user's building. A tax-lot result still never authorizes a BIN join. The record is shown as "HPD lists 1 building on this lot (1017 Home Street)", at lot level.
7. **Non-residential building (Empire State Building):** the HPD building record exists (building ID 2780, listed as "338 5 AVENUE") with `registrationid = 0`, DOB class "NOT AVAILABLE", and 0 legal class A or B units. There is no registration. This is **not** non-compliance: registration applies to multiple dwellings (and certain 1-2 family homes), so it is "no registration on file; HPD's record lists no legal dwelling units" (D12).
8. **Address spellings differ:** "224FRONT WEST 17 STREET" (no space) and "ADAM C POWELL BOULEVARD". Addresses are displayed but never used as join keys.
9. **Contacts contain personal data.**
   - On the five lots: 51 contacts. 38 carry a person's name (head officer, officer, agent, site manager). 24 carry corporation names, some of them on the same rows.
   - 32 carry a `businessapartment` (a suite or unit number in a business address).
   - Contact types seen: CorporateOwner, HeadOfficer, Officer, Agent, SiteManager. Citywide there are also IndividualOwner, JointOwner, Shareholder and Lessee.

Measured per test lot:

| Lot | HPD buildings | Registrations (end date) | Contacts |
|---|---|---|---|
| 2027540005 West Farms | 1 (BIN 2006185, 44 class A units) | 1 (ends 2026-09-01) | 5 |
| 1007660055 220 W 17th | 8 (8 BINs, 4 to 16 class A units each) | 8 (all end 2026-09-01) | 32 |
| 2029930031 (tax lot) | 1 (1017 Home St, BIN 2128717, 63 units) | 1 (ends 2026-09-01) | 4 |
| 1008350041 Empire State Bldg | 1 (registration ID 0, 0 units) | 0 | 0 |
| 1018227502 1845 ACP condo | 2 (BINs 1054974, 1055002; 24 and 23 units) | 2 (end 2026-09-01) | 10 |

## 4. Architecture: reuse, then add

**Reused unchanged:**
- the join policy (`plan_lot_query`; a tax-lot plan yields only lot-level results);
- the verified pager, with count first, the freshness header on every page, and restart and row cap;
- the evidence store and keyed digest, including the new `digest_basis`;
- the five retrieval statuses and the fixture privacy scan.

**New:**
1. `records/hpd_buildings.py`
   - **Query:** `boroid = b AND block = k AND lot = l`, ordered by `buildingid`.
   - **Attribution at building scope:** `this_building` if `bin` equals the resolved BIN; `other_building_on_lot` for another assigned BIN; `unattributed_on_lot` for a missing or placeholder BIN.
   - **Attribution at tax-lot scope:** `lot_level` only.
2. `records/hpd_registrations.py`
   - **Query:** registrations by lot, ordered by `registrationid, buildingid`, each row tied to its building through `buildingid`.
   - **Contacts:** fetched with `registrationid IN (...)`, using only the IDs returned by the registration query, in batches and ordered by `registrationcontactid`.
   - **Completeness:** each batch gets its own count check. If any batch is incomplete, the whole contacts result is `incomplete_pagination`.
3. **Registration status**, a computed field with an explicit basis, never a bare "expired":
   - `on_file_current`: the end date is after today.
   - `on_file_end_date_passed_snapshot_stale`: the end date is in the past, but after the dataset's last update. The data cannot show a renewal (D10).
   - `on_file_end_date_passed`: the end date is in the past and before the dataset's last update.
   - `none_on_file`: no registration row. This is a statement about the dataset, never "unregistered" or "non-compliant" (D12).
4. **Freshness metadata.** Every result carries the dataset's `rowsUpdatedAt` (from `/api/views/<id>.json`) next to the truth header, plus a `stale` flag when the last update is more than 45 days old (monthly cadence plus a margin).
5. **Evidence kind:** `administrative_record`.
6. **Optional, if D9 is approved:** a building-ID lookup that Task 1 and Task 2 can use to attribute rows that have no BIN. Rules:
   - The row's lot must already be confirmed.
   - The row's building ID must match exactly one buildings-dataset record on the same lot, and that record must have an assigned BIN.
   - Building scope only.
   - The record keeps `attribution_basis = hpd_building_id` instead of `bin`.
   - Reports show the counts before and after.

## 5. Privacy design (D11)

Registration contacts mix companies and private individuals. Recommended defaults:
- **Public:** corporation names, contact role types (owner, agent, managing agent, and so on), and the business city/state/zip for corporate contacts.
- **Withheld from public output, kept in the evidence store:** individual names (`firstname`, `middleinitial`, `lastname`, `title`), every `businessapartment`, and the business street address for individual contacts.
- When an owner is an individual (`IndividualOwner`, `JointOwner`), public output says "individual owner (name withheld)".
- Fixtures: contact rows are sanitized with the same marker and event approach (`[WITHHELD]`). The privacy scan is extended to the contact name and address fields.
- Logs: IDs and counts only.

The trade-off, stated plainly: individual owner names are public record and useful to a renter ("who is my landlord?"). Withholding them is the conservative default for a public portfolio app. It can be relaxed later by an explicit decision, without changing the data model.

## 6. Tests planned

**Recorded:**
- A sanitized cassette for the same five addresses: buildings, registrations and contacts, with withheld markers and redaction events. Roughly 13 buildings, 12 registrations and 51 contacts, so the fixture is small (well under 1 MB).

**Synthetic:**
- one registration covering two buildings;
- two registration rows for one building;
- a missing or placeholder BIN;
- tax-lot scope yields lot-level only, with no building claim, and the 1217 West Farms pattern (HPD building with a real BIN on a tax-lot result);
- `registrationid = 0`;
- each registration status;
- the stale-snapshot boundary;
- contact batching with one failing batch;
- unsupported join makes zero calls;
- 5xx, 429, short page, refresh mid-fetch, row cap;
- individual names, business apartments and individual addresses absent from public output and logs;
- the keyed digest and digest basis for both live and replayed input.

**If D9 is approved:**
- building-ID attribution on the 220 W 17th rows gives `other_building_on_lot` (224 Front, 218 Front), and none gives `this_building`;
- an ambiguous or missing building ID stays unattributed;
- never at tax-lot scope.

**Live (opt-in):**
- the schema headers still have every field used;
- freshness metadata is present;
- the test building has exactly one HPD building record with BIN 2006185.

## 7. Acceptance criteria

1. Offline tests reproduce the recorded per-lot counts of buildings, registrations and contacts.
2. No result claims a registration is expired, missing or non-compliant beyond what the snapshot supports. Every status carries its basis and the dataset's last-updated date.
3. Tax-lot results contain only lot-level records. The 1017 Home Street record on lot 2029930031 is never presented as the user's building.
4. Individual names, business apartments and individual business addresses never appear in public output, fixtures or logs (scanned in CI).
5. All Phase 1, Task 1 and Task 2 tests keep passing. If D9 is approved, Task 1 and Task 2 count changes are reported before and after, with the basis, and not applied silently.
6. CI is green, and the live tests pass.

## 8. Decisions needed

- **D9.** Use HPD's building ID as a secondary attribution key for violation and complaint rows that lack a BIN, under the strict rules in section 4.6.
  - Effect on 220 W 17th: 87 violations and 84 complaint problems move from `unattributed_on_lot` to `other_building_on_lot`. None moves to the requested building.
  - **Recommended: yes**, with `attribution_basis` visible and before/after counts reported. Alternative: keep those rows unattributed and only display the building-ID mapping.
- **D10.** Registration status is reported with its basis and the dataset's freshness; "expired" is never stated when the snapshot cannot show a renewal. **Recommended: yes.**
- **D11.** Contacts privacy defaults as in section 5 (corporations public; individual names and unit-level business addresses withheld). **Recommended: yes.** Alternative: show individual owner names too, as public record.
- **D12.** "No registration on file" is never presented as non-compliance; the HPD building record's legal unit counts are shown alongside. (Registration-noncompliance orders are already visible as Class I violations in Task 1.) **Recommended: yes.**

## 9. Still tracked before public launch

Rate limiting, CORS, apartment/unit parsing in addresses, GitHub Actions upgrades, NYC Open Data terms-of-use review, optional `SOCRATA_APP_TOKEN`, the Git-history fixture remediation (Step 1 done; Steps 2 and 3 not approved), the Task 1 and Task 2 HPD Online validations (Parts A and B), and the stale HPD registration datasets (re-check before launch).

## Appendix: live queries (2026-10-21, ~23:35 to 23:45 UTC)

- Schema and freshness: `GET /resource/{kj4p-ruqc|tesw-yqqr|feu5-w2e2}.json?$limit=1` (headers `X-SODA2-Fields`, `X-SODA2-Types`, `X-SODA2-Truth-Last-Modified`), and `GET /api/views/{id}.json` (`rowsUpdatedAt`, "Update Frequency: Monthly").
- Counts: `$select=count(*)`. Uniqueness: `$select=count(distinct buildingid), count(*)` on buildings; `count(distinct buildingid), count(distinct registrationid), count(*)` on registrations.
- Per lot: `$where=boroid=B AND block=K AND lot=L` on buildings and registrations; contacts `$where=registrationid in (...)`.
- End-date distribution: `$select=date_trunc_ym(registrationenddate) as m, count(*) $group=m $order=m DESC`. Newest registration: `$select=max(lastregistrationdate)`.
- BIN quality: `sum(case(bin is null,1,true,0))`, `sum(case(bin in (1000000,...,5000000),1,true,0))`.
- Building IDs on unattributed rows (lot 1007660055): `ygpa-z7cr` `$where=borough='MANHATTAN' AND block=766 AND lot=55 AND bbl IS NULL $select=building_id,count(*) $group=building_id`, and the same on `wvxf-dwi5` with `boroid=1`.
