# Phase 2, Task 5: DOB and 311 records (design for approval)

Status: **three-dataset V1 approved in principle (2026-10-23 04:29Z), with four required
amendments, now written in section 18.** Step 1 was accepted on 2026-10-23 05:15Z. The
disposition-code mapping was reconciled with DOB's 2021 code list in section 18.6 and
awaits approval. Step 1 (measurement and fixtures) is authorized;
no adapter, production model or migration is written until the step-1 review gate is
passed and step 2 is separately approved. Written 2026-10-23 on branch
`claude/project-thread-5d0l0f`. Where section 18 differs from an earlier section,
section 18 wins; the earlier sections carry a pointer.

All numbers come from live queries against `data.cityofnewyork.us` on 2026-10-23, about
03:55 to 04:15 UTC, and from the official data dictionaries attached to each dataset.
The queries are listed in the appendix. Anything not measured is marked **to measure**
or **not verified**.

Open verification obligations carried over, unchanged and separate from this task:

- **HPD Online independent validation, Part A: pending** (Sanyam's results).
- **HPD Online independent validation, Part B: pending** (Sanyam's results).

This design adds two new obligations of the same kind (Parts C and D, section 11). It
does not replace A or B.

## 1. Objective and recommended V1

Add what the Department of Buildings (DOB) and NYC 311 have on file about a resolved
building or tax lot, with the same guarantees as Tasks 1 to 4: Phase 1 join rules, the
five retrieval statuses, attribution buckets, provenance, privacy and the PostgreSQL
roles.

**Recommended V1 (D18): three datasets, not seven.**

| Dataset | Why it is in V1 | What it answers |
|---|---|---|
| DOB Complaints Received `eabe-havv` | DOB's own complaint log, with the inspection outcome and the only open record of stop-work and vacate dispositions | What was reported to DOB, and what DOB did |
| DOB ECB Violations `6bgk-3dad` | Summonses DOB issued, with a documented correction status and the OATH hearing outcome | What DOB charged, and whether it was upheld |
| 311 Service Requests 2020 to present `erm2-nwe9` | Requests to agencies other than HPD and DOB (noise, pests, water, sanitation) that no other source covers | What residents reported to the city, at lot level only |

Deferred to V2 (reasons in section 3): DOB Violations `3h2n-5cm9`, DOB Safety Violations
`855j-jady`, 311 from 2010 to 2019 `76ig-c548`.

What V1 will **not** claim:

- that any stop-work or vacate order is currently in effect;
- that a 311 request, a DOB complaint or an unadjudicated summons proves a violation;
- that a 311 request concerns one particular building, or any building at all;
- that "no records found" means nothing happened, when the source has known gaps
  (section 4.4).

## 2. Verified source registry

All links are official NYC Open Data pages. Row counts and update times are from the
dataset metadata and `count(*)` queries on 2026-10-23.

| ID | Name | Rows | Rows updated (UTC) | Stated frequency | Dictionary |
|---|---|---|---|---|---|
| [`eabe-havv`](https://data.cityofnewyork.us/d/eabe-havv) | DOB Complaints Received | 3,145,939 | 2026-10-22 16:15 | Daily | [DD_DOB_Complaints_Received_2019-08-21.xlsx](https://data.cityofnewyork.us/api/views/eabe-havv/files/e19c463a-69dd-4810-ae72-3c7c7fc657f8), [category list PDF](https://data.cityofnewyork.us/api/views/eabe-havv/files/dc709ed2-7af1-429c-92c9-71ec3a4c23fa) |
| [`6bgk-3dad`](https://data.cityofnewyork.us/d/6bgk-3dad) | DOB ECB Violations | 1,841,577 | 2026-10-22 17:04 | Every weekday | [DD_DOB_ECB_Violations_2019-07-10.xlsx](https://data.cityofnewyork.us/api/views/6bgk-3dad/files/824ab7b0-4416-4dac-ba0f-c3fae9311c8c) |
| [`erm2-nwe9`](https://data.cityofnewyork.us/d/erm2-nwe9) | 311 Service Requests from 2020 to Present | 22,892,475 | daily, one-day buffer | Daily | [311_ServiceRequest_2020-present_DataDictionary_Updated_2025.xlsx](https://data.cityofnewyork.us/api/views/erm2-nwe9/files/c5cc6926-45bb-4af3-8aab-0ee6a29bcf4d) |
| [`6v9u-ndjg`](https://data.cityofnewyork.us/d/6v9u-ndjg) | Building Complaint Disposition Codes (reference) | 160 | last updated 2017 | none | [dictionary](https://data.cityofnewyork.us/api/views/6v9u-ndjg/files/d64d25fb-7192-4560-875f-913aadd893a7) |
| [`3h2n-5cm9`](https://data.cityofnewyork.us/d/3h2n-5cm9) | DOB Violations (older BIS civil penalties) | 2,477,418 | 2026-10-22 17:39 | Every weekday | [DD_DOB_Violations_2019-03-19.xlsx](https://data.cityofnewyork.us/api/views/3h2n-5cm9/files/09445410-995e-40ad-aa44-37049d1f4e4d) |
| [`855j-jady`](https://data.cityofnewyork.us/d/855j-jady) | DOB Safety Violations (DOB NOW civil penalties) | 1,118,944 | 2026-10-22 18:07 | Daily | [DOB_NOW_Safety_Violations_Data_Dictionary.xlsx](https://data.cityofnewyork.us/api/views/855j-jady/files/9e8abcb0-c377-4b5f-a5ed-7c4cf490b2b7) |
| [`76ig-c548`](https://data.cityofnewyork.us/d/76ig-c548) | 311 Service Requests from 2010 to 2019 | 22,613,917 | historical | not automated | [dictionary](https://data.cityofnewyork.us/api/views/76ig-c548/files/5cdaad94-8bca-41b8-85fd-59ec9e1e20c6) |

Also seen, not proposed for V1:

- [`jz4z-kudi`](https://data.cityofnewyork.us/d/jz4z-kudi) OATH Hearings Division Case
  Status. The ECB dictionary says `ecb_violation_number` "can be used to check the status
  on the OATH Hearings Division Case Status dataset". Proposed as an independent
  cross-check (section 11), not as a report source.
- HPD Order to Repair/Vacate `tb8q-a3ar`, FDNY vacate list `n5xc-7jfa`, DOB Stalled
  Construction Sites `i296-73x5`. Out of scope for Task 5.
- **No open dataset of current DOB stop-work orders was found in the catalog.** The only
  open record of stop-work orders is the complaint disposition code (section 5.2).

Not verified:

- The 311 "updates" page on `opendata.cityofnewyork.us` could not be read from this
  environment (blocked by the network proxy). The 2025-12-24 revision below comes from
  the dataset's own metadata and dictionary change log.
- The DOB BIS and DOB NOW public lookup sites were not opened from here. They are named in
  section 11 as manual checks for Sanyam, as with HPD Online.

### 2.1 Fields per V1 dataset (from the live schema)

**`eabe-havv` DOB Complaints Received.** All fields are text.
`complaint_number`, `status`, `date_entered`, `house_number`, `house_street`, `zip_code`,
`bin`, `community_board`, `special_district`, `complaint_category`, `unit`,
`disposition_date`, `disposition_code`, `inspection_date`, `dobrundate`.

- **No BBL, no block, no lot.** BIN is the only building key.
- The description says it "includes complaints that come from 311 or that are entered
  into the system by DOB staff".
- `unit` is the DOB unit that handled the complaint (for example `BEST`, `SEP`), not an
  apartment.
- No free text and no personal names.

**`6bgk-3dad` DOB ECB Violations.** Text, except the three money fields.
`isn_dob_bis_extract`, `ecb_violation_number`, `ecb_violation_status`,
`dob_violation_number`, `bin`, `boro`, `block`, `lot`, `hearing_date`, `hearing_time`,
`served_date`, `issue_date`, `severity`, `violation_type`, `respondent_name`,
`respondent_house_number`, `respondent_street`, `respondent_city`, `respondent_zip`,
`violation_description`, `penality_imposed` (sic), `amount_paid`, `balance_due`,
`infraction_code1..10`, `section_law_description1..10`, `aggravated_level`,
`hearing_status`, `certification_status`.

**`erm2-nwe9` 311 (2020 to present).** Fields used or classified:
`unique_key`, `created_date`, `closed_date`, `due_date`, `resolution_action_updated_date`,
`agency`, `agency_name`, `complaint_type` (now labelled "Problem"), `descriptor`
("Problem Detail"), `descriptor_2`, `location_type`, `incident_zip`, `incident_address`,
`street_name`, `cross_street_1/2`, `intersection_street_1/2`, `address_type`, `city`,
`landmark`, `facility_type`, `status`, `resolution_description`, `community_board`,
`council_district`, `police_precinct`, `bbl`, `borough`, `x_coordinate_state_plane`,
`y_coordinate_state_plane`, `open_data_channel_type`, park, vehicle, taxi and bridge
fields, `latitude`, `longitude`, `location`.

- **BBL is present; BIN is not.** The dictionary calls `bbl` the "parcel number that
  identifies the location of the building or property associated with the service
  request".
- `incident_address` "is provided by the submitter"; `street_name` "is provided by
  geovalidation".
- The 2025-12-24 revision added columns (police precinct, council district, additional
  details) and moved 2010 to 2019 into `76ig-c548`.

## 3. Measured findings

### 3.1 Identifiers and keys

| Finding | Dataset | Measured |
|---|---|---|
| BIN present on every row, always 7 characters, no placeholder BIN | `eabe-havv` | yes |
| `complaint_number` is **not unique** (some numbers appear twice, e.g. 2124643, 4439958) | `eabe-havv` | yes; full count **to measure** |
| `isn_dob_bis_extract` and `ecb_violation_number` unique | `6bgk-3dad` | yes (no duplicates found) |
| `lot` mixes formats: 1,494,206 four-digit, 320,102 five-digit, 27,268 null | `6bgk-3dad` | yes |
| At 350 5th Ave: lot `00041` gives 147 rows, `0041` gives 94, BIN 1015862 gives 241 | `6bgk-3dad` | yes |
| Placeholder or null BIN: 5,427 rows | `6bgk-3dad` | yes |
| `block` format variants | `6bgk-3dad` | **to measure** |
| `unique_key` unique | `erm2-nwe9` | dictionary says so; no duplicates found |
| No BBL on 65,042 of 543,806 rows created since 2026-09-01 (12.0%) | `erm2-nwe9` | yes |
| Of those, only 625 are routed to HPD or DOB | `erm2-nwe9` | yes |
| `unique_key` "is NOT the Service Request (SR) # provided to the initiating customer. SR #s are not available" | `erm2-nwe9` | dictionary |
| `violation_number` not unique (up to 54 copies) | `855j-jady` | yes (V2) |
| `violation_category` can say ACTIVE while `disposition_comments` say "DELETED ... BECAUSE BELONGS TO 34 FLETCHER ST" | `3h2n-5cm9` | yes (V2) |

**A query using one lot format misses rows.** ECB must be queried with both the
four-digit and the five-digit lot, and the result checked against the BIN query
(section 8.2).

### 3.2 Attribution evidence at the test addresses

Same five addresses as the Phase 1 benchmark and Tasks 1 to 3.

| Address (BBL / resolved BIN) | DOB complaints | ECB | 311 by BBL since 2020 | Notes |
|---|---|---|---|---|
| 1134 West Farms Rd (2027540005 / 2006185) | 8 on 2006185; 4 on 2117584 | 3 (corrected, see note) | 399 (HPD 309, NYPD 76) | Footprints lists BINs 2006185 and 2117584; HPD lists only 2006185 |
| 220 W 17th St (1007660055 / 1081543; lot BINs 1081542 to 1081549) | 17 on 1081543 (1 active); 19 on 1081547; 4 on 1081549; others | 4 (corrected) | 165 | 311 addresses on this lot: 224 W 17 (42), 218 FRONT W 17 (29), 220 (26), 218 (23) |
| 1217 West Farms Rd (tax lot 2029930031, no verified BIN; Footprints BIN 2128717) | 6 on 2128717; 1 on retired BIN 2010717 | 12 (corrected) | 77 | BIN 2010717 is not in Footprints but has 14 DOB violations and 1 DOB complaint. 311 rows mostly addressed "1017 HOME STREET" (75) |
| 350 5th Ave (1008350041 / 1015862) | 141 (1 active), addressed "350 5 AVENUE" (92), "338 5 AVENUE" (24), "350 FIFTH AVENUE" (13), "348 5 AVENUE" (2) | 241 | 504 | Lot format split above |
| 1845 Adam Clayton Powell Jr Blvd (condo lot 1018227502 / 1054974 and 1055002) | 111 on 1054974 (74 addressed "1845 7 AVENUE"); 120 on 1055002 | 52 (corrected; 23 of them on the condo base lot 0001) | 435 | Condo billing lot |

**Correction (step 1, 04:39Z):** the ECB column first showed 0 for four addresses. Those
counts came from queries that used only the five-digit lot, which is exactly the
lot-format gap in 3.1. With both formats the counts are 3, 4, 12, 241 and 52. Details
in `docs/phase2_task5_step1_report.md`.

What this shows:

- **Street address alone is not conclusive.** A DOB complaint query on house number 220
  and a street containing "17" returned 8 BINs in 4 boroughs, and the same query for 350
  and "5" returned 8 BINs, only one of them 1015862. Address aliases are common on the right
  building too ("338 5 AVENUE" and "350 FIFTH AVENUE" are both BIN 1015862; "1845 7
  AVENUE" for ACP). V1 never uses address text to attribute a row.
- **311 rows on a lot often name a different street address** (1017 Home Street on the
  1217 West Farms lot; 218 and 224 W 17th on the 220 W 17th lot). A BBL places the
  request on the lot, not on a building.
- **BIN sets disagree between sources.** Footprints and HPD agree on four of five lots.
  At 1134 West Farms, Footprints has an extra BIN (2117584, constructed 1926, 4 DOB
  complaints). At 1217 West Farms, DOB rows carry a BIN that is in neither current list.

### 3.3 Status fields

| Dataset | Field | Values seen | Documented |
|---|---|---|---|
| `eabe-havv` | `status` | ACTIVE 21,101; CLOSED 3,124,838 | "based on the Disposition Code. Expected Values: ACTIVE, CLOSED" |
| `eabe-havv` | `disposition_code` | 160 codes in `6v9u-ndjg`; top: I2 (1.23M). Code N1 (1134 West Farms) is **not** in the table | reference table last updated 2017; how many codes in use are absent **to measure** |
| `6bgk-3dad` | `ecb_violation_status` | ACTIVE 281,197; RESOLVE 1,560,377; Unknown 3 | "whether or not the violation has been corrected. This is the status of the violation with DOB, not the status of the hearing with OATH. ACTIVE - still needs to be addressed; RESOLVE - the issue was either fixed with DOB or dismissed by OATH" |
| `6bgk-3dad` | `hearing_status` | IN VIOLATION, DISMISSED, WRITTEN OFF, CURED/IN-VIO, STIPULATION/IN-VIO, DEFAULT, PENDING, others | "Status of the hearing" (no list); full vocabulary **to measure** |
| `6bgk-3dad` | `certification_status` | e.g. CERTIFICATE ACCEPTED | "whether respondent/owner has certified the violation as corrected with DOB"; vocabulary **to measure** |
| `6bgk-3dad` | `severity` | Hazardous, Non-Hazardous, Unknown, and also `CLASS - 1/2/3` | dictionary lists only Hazardous, Non-Hazardous, Unknown |
| `erm2-nwe9` | `status` | Closed, Open, In Progress, Assigned, Pending, Canceled, others | "Assigned, Canceled, Closed, Pending, +" (non-exhaustive); full vocabulary **to measure** |
| `855j-jady` | `violation_status` | Active, Dismissed, plus 5 undocumented values | "Active, Dismissed" (V2) |

### 3.4 Dates

| Dataset | Field | Format seen |
|---|---|---|
| `eabe-havv` | `date_entered`, `disposition_date`, `inspection_date` | text `MM/DD/YYYY` |
| `eabe-havv` | `dobrundate` | text `YYYYMMDDhhmmss` (extract run time, not an event) |
| `6bgk-3dad` | `issue_date`, `served_date`, `hearing_date` | text `YYYYMMDD` |
| `erm2-nwe9` | `created_date`, `closed_date`, `due_date`, `resolution_action_updated_date` | floating timestamp, no time zone (New York local time assumed, **not verified**) |

### 3.5 Overlap between sources

- **311 rows routed to HPD duplicate HPD complaints.** At 1134 West Farms, 294 of the 309
  HPD-routed 311 requests since 2020 have a `unique_key` that also appears in HPD
  Complaints `ygpa-z7cr` (Task 2), where it is already withheld. Counting both would
  double-count.
- **311 rows routed to DOB become DOB complaints**, per the DOB dataset description, but
  there is **no key linking them**. Counting both would double-count with no way to
  remove the duplicates.
- **DOB complaints with dispositions A8 or A9 ("ECB violation served") have a matching
  ECB summons somewhere**, but the ECB dataset carries no complaint number. V1 shows
  both, separately, and never adds them together.
- **DOB Safety Violations and DOB Violations overlap** (stated in both descriptions).
  One reason both are deferred.

### 3.6 Free text and personal data

| Dataset | Field | Risk measured |
|---|---|---|
| `6bgk-3dad` | `respondent_name` | present on 406,671 of 406,830 rows since 2020; may name a person |
| `6bgk-3dad` | `respondent_house_number`, `_street`, `_city`, `_zip` | address of the respondent, may be a home address |
| `6bgk-3dad` | `violation_description` | inspector free text, cut at 210 characters. "apt" or "#" in 156,905 rows since 2020 (overcounts: "#" also matches "NOV #"). Floor detail seen ("AT 10TH FL") |
| `erm2-nwe9` | `incident_address` | submitter-provided. No "apt" or "#" found since 2026-09-01; still treated as personal |
| `erm2-nwe9` | `resolution_description` | agency text; DOB top values are boilerplate. Treated as free text |
| `erm2-nwe9` | `latitude`, `longitude`, `x/y`, `location` | point of the request |
| `erm2-nwe9` | `unique_key` | links to one person's request; private by instruction |
| `erm2-nwe9` | dictionary "Public View" column | some complaint types are marked "No"; meaning **not verified** (section 4.4) |
| `3h2n-5cm9` | `description`, `disposition_comments` | apartment mentions in 16,844 of 359,154 rows since 2020 (V2) |
| `eabe-havv` | none | no free text, no names |

## 4. Source semantics

### 4.1 What each record is

The report must keep five kinds of statement apart.

| Kind | Meaning | V1 records |
|---|---|---|
| Complaint or request | someone told an agency about a condition | DOB complaint (as received); 311 request |
| Inspection outcome | the agency went, or tried to go, and recorded a result | DOB complaint disposition (I2, I1, C1 to C8, ...) |
| Enforcement action | the agency issued a summons, violation or order | ECB summons; DOB dispositions A1, A8, A9, stop-work and vacate codes |
| Adjudicated finding | an independent tribunal decided | ECB `hearing_status` IN VIOLATION, DEFAULT, STIPULATION/IN-VIO, CURED/IN-VIO (mapping in 5.3) |
| Administrative status | whether the agency considers it open or corrected | DOB complaint `status`; ECB `ecb_violation_status`, `certification_status`; 311 `status` |

`EvidenceKind` per record (section 7):

- DOB complaint: `reported_allegation`. Its disposition is shown as an inspection outcome
  or enforcement action, never as the complaint's own kind.
- ECB summons: new kind `enforcement_action`. A summons is an agency charge, not a
  finding. The adjudication field says whether OATH upheld it.
- 311 request: `reported_allegation`, always. **A 311 request is not proof that a
  violation occurred**, and a "Closed" status is not proof that a condition was fixed.

### 4.2 Status vocabulary (public; DOB outcomes superseded by 18.6)

**DOB complaint `complaint_status`:** `active` (ACTIVE), `closed` (CLOSED), `unknown`
(anything else, flag `status_unknown`).

**DOB complaint `outcome`** (from `disposition_code`, using `6v9u-ndjg`). *This table is
the first proposal. Section 18.6 replaces it with outcomes taken from DOB's 2021 list.*

| Outcome | Codes (all from `6v9u-ndjg`) |
|---|---|
| `violation_served` | A1, A2, A4, A5, A6, A8, A9, B1, B2, H3, H4, V3, W1, ND, MB, MC, MD, MJ, MK, MG |
| `order_event` | A3, L1, L2, L3, K6, Y1, Y2, Y3, Y4, P3, P4, RZ, H5, ME, MF, MH, MI (section 5.2) |
| `no_violation` | I2 (no violation warranted at time of inspection), MA, R1, R6, WA, WF |
| `unsubstantiated` | I1 (unsubstantiated based on department records) |
| `no_access` | C1 to C8, WB |
| `referred` | F1 to F9, G1 to G9 |
| `assigned_or_pending` | D1 to D9, E1 to E9, EA to EE, EZ, J1, J3, J4, P2, K2 |
| `other_recorded` | every other code in the table |
| `none` | no disposition code |
| `unknown_code` | a code not in `6v9u-ndjg` (flag `disposition_code_unknown`) |

H3, H4, V3 and W1 (violations for disobeying an order) are `violation_served` and also
produce an order event (section 5.2). The code and DOB's own description are always
shown verbatim next to the outcome. The
grouping is ours, documented, and tested code by code. Some codes carry two meanings
(for example MI, "NO PA PERMIT/ECB NOV & PARTIAL VACATE ISSUED"); those go to
`order_event` and also set `violation_served = true`. The exact table is fixed in step 1
from all codes in use, and Sanyam reviews it before the adapter is written.

**ECB `correction_status`** (from `ecb_violation_status`, as documented): `open_with_dob`
(ACTIVE), `resolved_with_dob` (RESOLVE: "fixed with DOB or dismissed by OATH"),
`unknown`.

**ECB `adjudication`** (from `hearing_status`): `upheld`, `dismissed`, `pending`,
`other`, `unknown`. Proposed mapping, to be finalized from the full measured vocabulary
in step 1: IN VIOLATION, DEFAULT, STIPULATION/IN-VIO, CURED/IN-VIO → `upheld`; DISMISSED
→ `dismissed`; PENDING → `pending`; WRITTEN OFF → `other`; anything unlisted →
`unknown` with flag `status_unknown`. The raw value is always shown.

**ECB `severity`:** `hazardous`, `non_hazardous`, `class_1`, `class_2`, `class_3`,
`unknown`. `CLASS - n` values are kept but flagged `severity_undocumented_value`, because
the dictionary does not list them.

**311 `request_status`:** `open` (Open, Assigned, In Progress, Pending, Started),
`closed` (Closed), `canceled` (Canceled), `unknown` (anything else, flag
`status_unknown`). The full value list is fixed in step 1.

### 4.3 Status disagreement rules

| Condition | Result |
|---|---|
| DOB complaint `status` ACTIVE with a disposition that our table marks final (e.g. I2), or CLOSED with no disposition | keep both values; flag `status_fields_disagree`; outcome shown as recorded |
| ECB `ecb_violation_status` ACTIVE and `adjudication` = `dismissed` (the dictionary says OATH dismissal means RESOLVE) | flag `status_fields_disagree` |
| ECB RESOLVE and `adjudication` = `pending` | flag `status_fields_disagree` |
| 311 `closed` with no `closed_date`, or `open` with a `closed_date` | flag `status_fields_disagree` |
| A status date earlier than the received or issue date | flag `date_order` (existing) |

Rates of each disagreement are measured in step 1 before any wording is fixed.

### 4.4 Unknown, incomplete and "no records"

- `no_records_found` is reported only for a complete fetch, as today.
- **311 coverage note, always shown with any 311 result:** rows without a geovalidated
  BBL are not searched (12.0% of recent rows citywide); agencies with their own systems
  (the dictionary names NYCHA and the Department of Correction) are absent; the
  dictionary's "Public View" column suggests some complaint types are not published
  (**not verified**). So "no 311 requests found" means "none found on this lot in the
  published data since 2020", never "none made".
- **DOB coverage note:** DOB complaints are searched only by BIN. A complaint filed under
  a BIN we do not know for this lot (for example a retired BIN, section 6.3) is not
  found.
- A date that does not parse keeps the raw text and sets `unparseable_date`; a date before
  1900 or after the fetch sets `implausible_date` (existing rules).

### 4.5 Date vocabulary (public)

| Model field | Source | Meaning |
|---|---|---|
| `received_date` | DOB `date_entered`; 311 `created_date` (date part only, D21) | when the complaint or request was made |
| `inspection_date` | DOB `inspection_date` | when DOB inspected or attempted |
| `disposition_date` | DOB `disposition_date` | date of the recorded disposition |
| `issue_date`, `served_date`, `hearing_date` | ECB | as named |
| `closed_date` | 311 `closed_date` (date part only) | when the agency closed the request |
| `agency_updated_date` | 311 `resolution_action_updated_date` (date part only) | last agency update |

`dobrundate` and the 311 `due_date` are kept only in private evidence: one is an extract
time, the other an internal service-level date.

## 5. Orders: stop-work and vacate

### 5.1 Rule (D25)

**V1 never says an order is currently in effect.** No V1 dataset has a documented field
that says so:

- a DOB complaint row carries **one** disposition code; an order issued and later
  rescinded may appear as a single code on one complaint, or on different complaints;
- the complaint `status` is "based on the Disposition Code" and does not track the order;
- no open dataset of current stop-work orders was found.

### 5.2 What V1 reports (U4 and U5 added, and an `order_action` field, in 18.6)

For each DOB complaint whose disposition is an order code, an `OrderEvent`:

| `order_event` | Codes |
|---|---|
| `stop_work_full_issued` | A3, U4 |
| `stop_work_partial_issued` | K6, U5 |
| `stop_work_partial_recorded` | L1 (DOB's text has no verb) |
| `stop_work_crane_no_address_recorded` | K4 (no verb) |
| `stop_work_rescinded_full` | L2 |
| `stop_work_rescinded_partial` | L3 |
| `stop_work_noncompliance` | H3, H4, V3 |
| `stop_all_work_no_tcao` | H5 |
| `vacate_full_issued` | Y1, ME, MH |
| `vacate_partial_issued` | Y3, MF, MI |
| `vacate_rescinded_full` | Y2 |
| `vacate_rescinded_partial` | Y4 |
| `vacate_previously_issued` | RZ |
| `vacate_noncompliance` | W1 |
| `padlock_issued` | P3 |
| `padlock_rescinded` | P4 |

Wording, fixed and tested: "DOB recorded *[event]* on *[disposition_date]* for complaint
*[number]*. This dataset does not show whether the order is still in effect. Check DOB's
own records." The report lists events in date order and never nets issues against
rescissions. HPD vacate orders stay in the HPD violation results (Task 1), unchanged.

## 6. Attribution

### 6.1 Principles

1. A row is attributed only from **identifiers on the row**, checked against the
   resolver's verified BBL and BIN and the Footprints and HPD building lists for that lot.
2. **Address text never attributes a row.** It is recorded privately, never compared.
3. **Tax-lot resolution never authorizes a BIN join** (Phase 1 rule, unchanged).
4. Each row ends in one of the four places Sanyam named:
   - **this building:** `this_building`;
   - **another building on the lot:** `other_building_on_lot`;
   - **the lot generally:** `lot_level` (tax-lot scope) or `unattributed_on_lot`
     (building scope, row on the lot with no defensible building);
   - **no defensible building or lot:** the row is never fetched (address-only matches
     are not queried) or, if fetched by BIN with a conflicting lot, kept as
     `unattributed_on_lot` with `lot_membership = conflicting`.

### 6.2 New attribution vocabulary

| Value | Type | Meaning |
|---|---|---|
| `bin_lot_mapping` | `AttributionBasis` | the row's own BIN decided attribution, and the BIN's lot membership comes from Footprints or HPD, not from the row (DOB complaints have no lot) |
| `via_bin_mapping` | `LotMembership` | the row has no lot identifiers; lot membership is inferred from its BIN through Footprints or HPD |

No new `Attribution` value is needed.

### 6.3 BIN search set for DOB complaints (D24; amended in 18.3)

At **building scope** only:

```
search_bins = {resolved BIN}
            ∪ Footprints bins_on_lot (base_bbl or mappluto_bbl = resolved BBL)
            ∪ HPD building-record BINs on the lot (Task 3, current records)
```

- Placeholder BINs (n000000) are never searched.
- Each BIN carries its provenance (`resolver`, `footprints`, `hpd`, or several).
- If Footprints and HPD disagree, the result carries flag `bin_sets_disagree` and lists
  both.
- A cap of 25 BINs; above it the fetch is `incomplete_pagination` with reason
  `bin_set_too_large` (no test lot comes close; 220 W 17th has 8).
- **Retired BINs** (in DOB rows on the lot but in neither current list, like 2010717) are
  not searched in V1. When the ECB fetch for the same lot shows such a BIN, the DOB
  complaint result says so in a coverage note.

At **tax-lot scope**, DOB complaints return `unsupported_join` (D23). DOB complaints have
only a BIN, and the Phase 1 rule says tax-lot resolution never authorizes a BIN join.
1217 West Farms therefore gets no DOB complaints in V1; it still gets ECB (by lot) and
311 (by BBL).

### 6.4 Decision table (amended in 18.3)

`R` = resolved BIN. `L` = current lot BIN set (Footprints ∪ HPD). Building scope unless
stated.

**DOB complaints (`eabe-havv`)**

| Row BIN | Scope | Attribution | Basis | Lot membership | Flags |
|---|---|---|---|---|---|
| = R | building | `this_building` | `row_identifiers` | `via_bin_mapping` | |
| in L, ≠ R, in both Footprints and HPD | building | `other_building_on_lot` | `bin_lot_mapping` | `via_bin_mapping` | |
| in L, ≠ R, in only one list | building | `other_building_on_lot` | `bin_lot_mapping` | `via_bin_mapping` | `bin_sets_disagree` |
| any | tax lot | n/a: `unsupported_join` | | | |

A row's BIN is always one we searched for, so no other case arises. Address text that
differs from the resolved address (aliases) changes nothing.

**ECB violations (`6bgk-3dad`)**, fetched by lot (both formats) **or** BIN = R:

| Lot parts on row | Row BIN | Scope | Attribution | Basis | Lot membership | Flags |
|---|---|---|---|---|---|---|
| match | = R | building | `this_building` | `row_identifiers` | `confirmed` | |
| match | in L, ≠ R | building | `other_building_on_lot` | `row_identifiers` | `confirmed` | |
| match | valid, not in L | building | `unattributed_on_lot` | `row_identifiers` | `confirmed` | `bin_not_on_current_lot` |
| match | placeholder / missing / invalid | building | `unattributed_on_lot` | `row_identifiers` | `confirmed` | `bin_placeholder` / `bin_missing` / `bin_invalid` |
| missing | = R | building | `this_building` | `row_identifiers` | `via_bin_mapping` | `lot_parts_missing` |
| different lot | = R | building | `unattributed_on_lot` | `row_identifiers` | `conflicting` | `lot_identifier_conflict` |
| match | any | tax lot | `lot_level` | `row_identifiers` | `confirmed` | BIN flags as above |

At tax-lot scope the BIN clause is not sent (no BIN join).

**311 (`erm2-nwe9`)**, fetched by BBL only (D29):

| Row BBL | Scope | Attribution | Basis | Lot membership |
|---|---|---|---|---|
| = resolved BBL | building | `unattributed_on_lot` | `row_identifiers` | `confirmed` |
| = resolved BBL | tax lot | `lot_level` | `row_identifiers` | `confirmed` |

**A 311 request is never attributed to a building**, even on a one-building lot. The BBL
is geocoded from an address the submitter typed; the request may be about the sidewalk,
the street, or another structure. `location_type` (for example `RESIDENTIAL BUILDING`,
`Street/Sidewalk`) is shown as reported, mapped to a `LocationScope`-style hint, and
never upgrades attribution.

### 6.5 Condo lots (measured; see 18.3)

1845 ACP is a condominium billing lot (lot 7502). 311 returned 435 rows for the billing
BBL. Whether some 311 rows use unit-lot BBLs instead (lots 1001 and up on the same block)
is **to measure** in step 1. If they do, V1 adds the unit lots that Footprints or the
resolver ties to the billing lot, each with provenance, still at lot level. The same
check applies to ECB lots on condo blocks.

## 7. Typed output models

New module `records/dob_models.py` and `records/sr311_models.py`, following
`models.py` and `complaint_models.py`. Shared types (`RetrievalProvenance`,
`DataFreshness`, `PrivacySummary`, `RedactionInfo`, `DateValue`, `QualityFlag`,
`Attribution`, `AttributionBasis`, `LotMembership`, `EvidenceKind`, `DigestBasis`) are
reused.

```python
class DobComplaintOutcome(StrEnum): ...  # section 4.2
class OrderEventKind(StrEnum): ...       # section 5.2

class OrderEvent(BaseModel):
    kind: OrderEventKind
    disposition_code: str
    disposition_date: DateValue | None
    still_in_effect: Literal["not_shown_by_source"] = "not_shown_by_source"

class NormalizedDobComplaint(BaseModel):
    complaint_number: str                 # public: DOB's own identifier
    source_bin: str
    bin_provenance: list[Literal["resolver", "footprints", "hpd"]]
    complaint_category: str
    complaint_category_description: str | None
    complaint_status: Literal["active", "closed", "unknown"]
    status_raw: str | None
    disposition_code: str | None
    disposition_description: str | None
    outcome: DobComplaintOutcome
    violation_served: bool
    order_event: OrderEvent | None
    received_date: DateValue | None
    inspection_date: DateValue | None
    disposition_date: DateValue | None
    dob_unit: str | None                  # DOB unit, not an apartment
    recorded_address: str | None          # DOB's house number + street, shown as recorded
    evidence_kind: Literal[EvidenceKind.REPORTED_ALLEGATION]
    attribution: Attribution
    attribution_basis: AttributionBasis
    lot_membership: LotMembership
    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis

class NormalizedEcbViolation(BaseModel):
    ecb_violation_number: str             # public: OATH-searchable
    dob_violation_number: str | None
    source_bin: str | None
    source_block: str | None
    source_lot: str | None                # as recorded; lot_format kept privately
    violation_type: str | None
    severity: Literal["hazardous", "non_hazardous", "class_1", "class_2", "class_3", "unknown"]
    severity_raw: str | None
    correction_status: Literal["open_with_dob", "resolved_with_dob", "unknown"]
    adjudication: Literal["upheld", "dismissed", "pending", "other", "unknown"]
    hearing_status_raw: str | None
    certification_status: str | None
    issue_date: DateValue | None
    served_date: DateValue | None
    hearing_date: DateValue | None
    description: str | None               # redacted
    infractions: list[Infraction]         # code + section_law_description
    penalty_imposed: Decimal | None
    amount_paid: Decimal | None
    balance_due: Decimal | None
    aggravated_level: str | None
    redaction: RedactionInfo
    evidence_kind: Literal[EvidenceKind.ENFORCEMENT_ACTION]
    attribution: Attribution
    attribution_basis: AttributionBasis
    lot_membership: LotMembership
    location_scope: LocationScope         # from the redacted description, existing rules
    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis
    # never present: respondent_* (withheld), hearing_time

class NormalizedServiceRequest(BaseModel):
    request_ref: str                      # keyed token, never the unique_key (D20)
    agency: str
    complaint_type: str
    descriptor: str | None
    descriptor_2: str | None
    location_type: str | None
    request_status: Literal["open", "closed", "canceled", "unknown"]
    status_raw: str | None
    received_date: DateValue | None       # date only
    closed_date: DateValue | None         # date only
    agency_updated_date: DateValue | None # date only
    resolution_description: str | None    # redacted
    redaction: RedactionInfo
    evidence_kind: Literal[EvidenceKind.REPORTED_ALLEGATION]
    attribution: Literal[Attribution.UNATTRIBUTED_ON_LOT, Attribution.LOT_LEVEL]
    attribution_basis: Literal[AttributionBasis.ROW_IDENTIFIERS]
    lot_membership: Literal[LotMembership.CONFIRMED]
    quality_flags: list[QualityFlag]
    original_digest: str
    digest_basis: DigestBasis
    # never present: unique_key, incident_address, cross/intersection streets,
    # latitude, longitude, x/y, location, open_data_channel_type, due_date
```

Result models, each a `SourceResult` subclass with the usual `status`, `join_scope`,
`provenance`, `freshness`, `privacy`, `warnings`:

- `DobComplaintResult`: records, `search_bins` with provenance, `bin_sets_disagree`,
  summary (by attribution × status, by outcome, order events in date order, received in
  the last 12 and 36 months, as HPD complaints do), coverage notes.
- `EcbViolationResult`: records, `lot_formats_queried`, summary (by attribution ×
  correction status × adjudication, by severity, balance due total for this building
  only), retired BINs seen on the lot.
- `ServiceRequestResult`: records for agencies other than HPD and DOB, `excluded_counts`
  (`{"HPD": n, "DOB": m}` with the reason "covered by that agency's own dataset"),
  summary by agency × complaint type × status, last 12 and 36 months, the coverage note
  of section 4.4.

New `QualityFlag` values: `bin_not_on_current_lot`, `bin_sets_disagree`,
`disposition_code_unknown`, `severity_undocumented_value`. Existing flags reused:
`bin_placeholder`, `bin_missing`, `bin_invalid`, `lot_parts_missing`,
`lot_identifier_conflict`, `status_fields_disagree`, `status_unknown`, `date_order`,
`unparseable_date`, `implausible_date`, `duplicate_id_same_content`,
`duplicate_id_different_content`, `unit_detail_redacted`.

New `EvidenceKind`: `enforcement_action`.

## 8. Query strategy and pagination

### 8.1 Common rules (reuse `sources/socrata_pager.fetch_all`)

- Count first, then pages ordered by a stable key plus `:id`, then compare rows fetched
  with the count and the truth header before and after (existing behaviour).
- Page size and row cap stay at the pager's current values. No new API limit is assumed.
  `SOCRATA_APP_TOKEN` stays optional and in the environment only.
- Schema check: a missing required field makes the result `source_unavailable` with
  reason `schema_changed` (existing `_check_schema`).
- Freshness from dataset metadata (`socrata_metadata.py`); stale if not updated within 3
  days (daily) or 4 days (weekday datasets). Thresholds are D28.
- Values in `$where` come only from the resolver (digits) and are checked against
  `^\d+$` before building the clause, as today.

### 8.2 Per dataset (311 query amended in 18.4)

**DOB complaints**

```
$where = bin in ('2006185','2117584')
$order = complaint_number, :id
```

**ECB violations**, building scope:

```
$where = boro='1' AND block='00835' AND (lot='00041' OR lot='0041') OR bin='1015862'
$order = isn_dob_bis_extract, :id
```

(with parentheses so that the BIN clause is a separate alternative). At tax-lot scope
the `OR bin=...` clause is omitted. If step 1 finds block format variants, both block
formats are included the same way. Rows returned by both clauses are de-duplicated by
`isn_dob_bis_extract` (expected, not a quality issue). The result records
`lot_formats_queried`, and a check compares the per-format counts with the measured
split (section 3.1).

**311**

```
$where = bbl='1008350041' AND created_date >= '2020-01-01T00:00:00'
$order = :id
```

All agencies are fetched, then HPD and DOB rows are counted and dropped before
normalization (D19), so the excluded counts are exact. The 2020 floor is the dataset's
own start; 2010 to 2019 is out of V1 (D28).

### 8.3 Unstable truth header on 311 (D27; amended in 18.4)

Five identical-style 311 calls returned two different `X-SODA2-Truth-Last-Modified`
values, alternating between "Thu, 22 Oct 2026 01:37:36 GMT" and "Fri, 23 Oct 2026
01:38:29 GMT". This looks like replicas one day apart behind a load balancer (**not
verified**). The pager would see a "change" mid-fetch and mark the result incomplete, or,
worse, combine pages from two replicas.

Recommended handling, 311 only:

1. If the count is at most one page (the largest test lot has 504 rows since 2020), fetch
   all rows in **one** response, so every row comes from one replica.
2. The rows returned must equal the count. If they do not (count and data served by
   different replicas), retry the pair once.
3. Still unequal, or more than one page needed and the header changes between pages:
   `incomplete_pagination` with reason `source_snapshot_unstable`, with the headers seen.
4. `data_as_of` is the header of the data response, and every header seen is recorded in
   the page provenance.

The same check runs on DOB datasets; if they show the same behaviour, the same rule
applies.

## 9. Duplicates

| Case | Rule |
|---|---|
| Same `complaint_number`, identical content | collapse, flag `duplicate_id_same_content` |
| Same `complaint_number`, different content (e.g. different BIN or disposition) | keep both, flag `duplicate_id_different_content`, both shown |
| ECB row returned by both the lot and the BIN clause | collapse by `isn_dob_bis_extract`, no flag |
| ECB `ecb_violation_number` repeated (none measured) | keep both, flag `duplicate_id_different_content` |
| 311 `unique_key` repeated (none measured) | as for complaints, keyed by the token |
| 311 HPD-routed vs HPD complaints | excluded from 311 records; count shown (D19) |
| 311 DOB-routed vs DOB complaints | excluded from 311 records; count shown (D19) |
| DOB complaint A8/A9 vs ECB summons | both shown, never summed; no link claimed |

## 10. Privacy, evidence and retention

### 10.1 Field classification (amended in 18.2)

| Field | Public record | Private evidence (`original_row`, 180 days) | Never stored |
|---|---|---|---|
| DOB complaint fields | all fields except `dobrundate` | all | |
| ECB `respondent_name`, `respondent_house_number/street/city/zip` | **withheld** (D22) | yes | |
| ECB `violation_description` | **redacted** (unit and floor detail) | original | |
| ECB `hearing_time` | no | yes | |
| ECB other fields | yes | yes | |
| 311 `unique_key` | **no**; a keyed token instead (D20) | yes, only inside `original_row` | |
| 311 `incident_address`, cross and intersection streets, `landmark` | **withheld** (D21) | yes | |
| 311 `latitude`, `longitude`, `x/y`, `location` | **withheld** (D21) | yes | |
| 311 `created_date`, `closed_date`, `resolution_action_updated_date` | **date only** (D21) | full timestamp | |
| 311 `resolution_description` | **redacted** | original | |
| 311 `open_data_channel_type`, `due_date` | no | yes | |
| 311 HPD- and DOB-routed rows | counts only | **not stored** (not normalized, so no raw row) | |
| Free-text address typed by the user | | | never (D16) |

### 10.2 Reuse, no weakening

- `records/redaction.py`: `redact_text_detailed` for ECB descriptions and 311 resolution
  text; `withhold_fields` extended with the ECB respondent fields and the 311 location
  fields. `WITHHELD_FIELDS` already contains `unique_key` (the HPD complaint copy of the
  same 311 key).
- **Known over-redaction:** "NOV # 38167719M" in ECB text may be treated as a unit marker.
  V1 accepts over-redaction (D31); a test pins the behaviour so any change is deliberate.
- Digests: `keyed_digest` over the original row (basis `original_row`), as today. The 311
  token is a separate HMAC: `k311_` + first 32 hex characters of
  HMAC-SHA256(`EVIDENCE_DIGEST_KEY`, `"nyc311-unique-key\x00" + unique_key`). Domain
  separation means the token cannot be confused with a row digest. Without the key,
  nobody can map a token back to a 311 key, even though the key space is small.
- Roles and grants: **no change.** `app_reader` keeps no access to `evidence`;
  `records.evidence_identity` stays readable by `ingest_writer` only, and for 311 it will
  show only tokens. The 0010 lifecycle trigger, the retention job and the restore
  boundary apply to the new rows without change.
- Retention: D15, 180 days for originals, same tombstones. Respondent names are held only
  under that window.
- Logging: the DEBUG row filter and scrubbed write errors from step 6 apply; new privacy
  audit cases are listed in section 11.

### 10.3 Why the 311 key stays private

The dictionary says `unique_key` is not the SR number given to the caller, so it does not
directly identify a person. It still names one person's request. Publishing it turns the
report into an index of individual requests that anyone can join back to the full open
row (address as typed, exact time, coordinates). There is no user need for it: the
report shows counts and categories at lot level. So it stays private until a specific
public-exposure policy is justified and approved, as instructed.

## 11. Evaluation and tests

### 11.1 Independent agency-record validation (new obligations)

Not adapter-to-database equality. Ground truth comes from a different agency system or
a different dataset, collected **before** the adapter runs, and never edited to improve
results.

| Part | What | Who | Pass rule |
|---|---|---|---|
| A, B | HPD Online (Tasks 1 to 3) | Sanyam | **still pending**, unchanged |
| **C** | DOB BIS / DOB NOW complaint lookup for the BINs of the five test addresses: all ACTIVE complaints and 10 random closed ones per BIN, comparing number, status, disposition code and date | Sanyam (manual), with a checklist generated by a script | every sampled complaint matches, or the difference is explained by data timing |
| **D** | ECB: automated cross-check of every `ecb_violation_number` found at the test lots against OATH Hearings Division Case Status `jz4z-kudi` (hearing status, penalty); plus a manual OATH or DOB BIS lookup of 5 summonses | script plus Sanyam | numbers found in OATH; hearing outcomes agree after the documented mapping; disagreements reported, not hidden |
| E | 311: no public SR lookup exists for open-data keys. Aggregate checks only: per-lot counts by agency and month re-derived with an independently written query, and HPD-routed rows matched to HPD complaints `ygpa-z7cr` by key (294 of 309 at 1134 West Farms today) | automated | counts agree; the HPD match rate is reported, not asserted |

Attribution ground truth: a hand-labelled table of expected attribution for every BIN
and every ECB row at the five lots, made from Footprints, HPD and agency lookups in
step 1, reviewed by Sanyam, then frozen.

### 11.2 Test matrix

| # | Scenario | Fixture | Expected |
|---|---|---|---|
| 1 | DOB complaint on the resolved BIN | recorded (350 5th) | `this_building` |
| 2 | DOB complaints on other lot BINs | recorded (220 W 17th) | `other_building_on_lot`, never the resolved BIN |
| 3 | Footprints and HPD BIN sets disagree | recorded (1134 West Farms, BIN 2117584) | `other_building_on_lot` + `bin_sets_disagree` |
| 4 | Tax-lot scope, DOB complaints | recorded (1217 West Farms) | `unsupported_join`, no request sent |
| 5 | Retired BIN on lot | recorded (1217 West Farms ECB/BIS) | coverage note; BIN not searched |
| 6 | ECB lot format split | recorded (350 5th: 147 + 94 = 241) | all 241 found once; per-format counts recorded |
| 7 | ECB row with placeholder BIN on the lot | synthetic | `unattributed_on_lot` + `bin_placeholder` |
| 8 | ECB row by BIN with a different lot | synthetic | `unattributed_on_lot`, `conflicting`, `lot_identifier_conflict` |
| 9 | ECB row with null lot, BIN = R | synthetic | `this_building`, `via_bin_mapping`, `lot_parts_missing` |
| 10 | 311 on a one-building lot | recorded | `unattributed_on_lot`, never `this_building` |
| 11 | 311 at tax-lot scope | recorded (1217 West Farms) | `lot_level` |
| 12 | 311 HPD and DOB rows | recorded (1134 West Farms) | excluded, exact counts |
| 13 | Address alias on the right BIN | recorded ("338 5 AVENUE") | attribution unchanged |
| 14 | Same address in another borough | synthetic | never fetched; not in any result |
| 15 | Duplicate complaint number, same / different content | synthetic | collapse + flag / keep both + flag |
| 16 | Count changes mid-fetch, page short, page error | synthetic | `incomplete_pagination`, partial rows kept and marked |
| 17 | 311 truth header alternates | synthetic, and the recorded header pair | single-response rule; retry; then `source_snapshot_unstable` |
| 18 | Source down before count | synthetic | `source_unavailable` |
| 19 | Schema missing a required field | synthetic | `source_unavailable`, `schema_changed` |
| 20 | Status disagreement (each rule in 4.3) | synthetic | flag set, both values shown |
| 21 | Unknown disposition code, unknown hearing status, `CLASS - 2` severity | synthetic + recorded | `disposition_code_unknown`, `status_unknown`, `severity_undocumented_value` |
| 22 | Each order code in 5.2 | synthetic, plus recorded Y2 (1134 West Farms) and L2 (350 5th) | correct event; wording never says "in effect" |
| 23 | Missing and malformed dates | synthetic | `unparseable_date` / `implausible_date`, raw kept privately |
| 24 | Respondent name, apartment and floor in ECB text | synthetic sentinel values | absent from public records, API, logs, DB `records` schema; present only in `original_row` |
| 25 | 311 `unique_key`, address, coordinates | synthetic sentinels | absent everywhere public, including `evidence_identity`, `fetch_page.url`, `source_record_id`, `source_row_id`, payload JSON, logs and error text |
| 26 | 311 token stability and key separation | synthetic | same key gives the same token; different `EVIDENCE_DIGEST_KEY` gives a different token; token never equals any row digest |
| 27 | Round trip through PostgreSQL, then expiry | db | records unchanged after the retention job; tokens and redactions kept |
| 28 | Phase 1 benchmark and HPD results | existing | 14/14 correct, 0 wrong building, 6/6, 4/4, 2/2; HPD recorded results identical |
| 29 | Live smoke (opt-in, `live` marker) | live | each dataset answers; schema check passes |

The live tests stay opt-in and never run in CI, as today.

## 12. Planned PostgreSQL changes (not written yet; corrected in 18.1)

Migration 0011 (one migration, planned for step 5):

1. Allowed `result_type` values: add `dob_complaints`, `dob_ecb_violations`,
   `service_requests_311`. Allowed `record_kind` values: the three record types.
2. `evidence_kind`: add `enforcement_action`. `attribution_basis`: add
   `bin_lot_mapping`. `lot_membership`: add `via_bin_mapping`. Quality flags: add the four
   new values, wherever the column is constrained.
3. **311 identifier CHECKs**, enforced in the database, not only in Python:
   - `records.normalized_record`: when `dataset_id = 'erm2-nwe9'`, `source_record_id` and
     `source_row_id` must match `^k311_[0-9a-f]{32}$`, and `payload` must not contain the
     key `unique_key`;
   - `records.record_source_row`: same pattern for `source_row_id`;
   - `evidence.raw_row`: when `dataset_id = 'erm2-nwe9'`, `source_record_id` must match
     the token pattern. The real key exists only inside `original_row`, readable by
     `evidence_admin` alone.
4. ECB withheld fields: when `dataset_id = '6bgk-3dad'`, `payload` must not contain any
   `respondent_` key.
5. No new roles, no grant changes, no change to the 0010 trigger, the restore function
   or the retention job.

`downgrade()` removes only what 0011 adds and refuses if Task 5 rows exist, as earlier
migrations do.

## 13. Implementation steps and acceptance gates

Each step is a separate set of commits, reported, and approved before the next.

| Step | Work | Gate |
|---|---|---|
| 1 | Measurement and fixtures, no adapter: full vocabularies (hearing status, certification, 311 status, disposition codes in use), block formats, complaint-number duplicate count, status-disagreement rates, condo unit-lot BBLs in 311, truth-header behaviour on DOB datasets. Record cassettes for the five addresses. Write and freeze the attribution ground-truth table and the outcome mapping | Sanyam reviews the measurements, the outcome and adjudication tables, and the frozen ground truth |
| 2 | DOB complaints: models, adapter, BIN search set, outcomes, order events, offline tests | tests 1 to 5, 13 to 16, 20 to 23 pass; benchmark and HPD unchanged |
| 3 | ECB: models, adapter, two-format lot query, redaction and withholding | tests 6 to 9, 21, 24 pass; benchmark and HPD unchanged |
| 4 | 311: models, adapter, exclusion of HPD and DOB rows, tokens, single-response fetch | tests 10 to 12, 17, 25, 26 pass |
| 5 | Migration 0011, repository save and load, privacy audit extended to the new tables and fields | test 27 and the full PostgreSQL suite pass; privacy audit clean; roles unchanged |
| 6 | Independent validation: Part D script and report, Part C and E checklists, Task 5 report | Parts C and D results from Sanyam; A and B still tracked separately |

Every gate also requires: CI green, no secrets in the repository, and the existing test
counts not reduced.

## 14. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Footprints BIN-to-lot mapping is current, not historical | old complaints on a BIN since moved to another lot would be shown on this lot | provenance shown per BIN; lot changes are rare; noted in coverage |
| ECB lot format mix | missed summonses | query both formats plus BIN; per-format counts recorded |
| 311 missing BBLs (12%) | undercount | coverage note on every 311 result; no address fallback |
| 311 replica disagreement | mixed snapshots | single-response fetch, retry, then incomplete (D27) |
| Disposition code table last updated 2017 | codes in use not in the table | `unknown_code` outcome, flag, raw code shown |
| Undocumented values (`CLASS - n`, Safety statuses) | misreading | raw value always shown; flag; mapping reviewed |
| 311 schema revised 2025-12-24; more revisions possible | adapter breaks | schema check, `schema_changed` |
| DOB complaints from DOB NOW may not all reach `eabe-havv` | undercount | **not verified**; open question Q3; Part C checks it |
| Over-redaction of ECB text | lost detail | accepted; pinned by test (D31) |
| Condo unit lots | missed 311 and ECB rows | measured in step 1 (section 6.5) |

## 15. Open questions

1. Does `6bgk-3dad` also mix `block` formats? (step 1)
2. Do 311 rows on condo blocks use unit-lot BBLs? (step 1)
3. Are complaints filed in DOB NOW fully present in `eabe-havv`? (Part C will show
   gaps; no official statement found)
4. What does the 311 dictionary's "Public View = No" mean for the published data? (not
   verified)
5. Is the 311 timestamp New York local time? (not verified; only the date is public, so
   the effect is limited to rows near midnight)

## 16. Decisions requested (D19, D20, D21, D24, D27, D31 revised in 18.5)

| # | Decision | Recommended | Alternatives |
|---|---|---|---|
| D18 | V1 scope | DOB complaints + ECB + 311 (2020 on) | add Safety Violations `855j-jady` now; or DOB only, 311 later |
| D19 | 311 rows routed to HPD and DOB | exclude from records, show exact counts | include with a "may duplicate" flag |
| D20 | 311 identifier | keyed `k311_` token everywhere outside `original_row`, enforced by CHECK | plain key in private `raw_row.source_record_id` only |
| D21 | 311 location and time | withhold address, cross streets, coordinates; dates only | show geovalidated street name |
| D22 | ECB respondent fields | withheld publicly; kept in private evidence for 180 days | never stored (sanitized basis, losing digest verification) |
| D23 | DOB complaints at tax-lot scope | `unsupported_join` (keeps the Phase 1 rule) | query Footprints BINs as `lot_level` (would need an explicit rule change) |
| D24 | BIN search set | resolved + Footprints + HPD lot BINs; retired BINs not searched, noted | also search retired BINs seen on lot rows, as `unattributed_on_lot` |
| D25 | Orders | report recorded order events; never "in effect" | none recommended |
| D26 | ECB status | `ecb_violation_status` as correction status; `hearing_status` as adjudication; disagreement flagged | single combined status |
| D27 | 311 unstable truth header | single-response fetch, count match, one retry, then `incomplete_pagination` | accept header changes when counts match |
| D28 | History and freshness | DOB complaints and ECB full history; 311 from 2020; 12 and 36 month summaries; stale after 3 days (daily) or 4 days (weekday) | add 311 2010 to 2019 |
| D29 | 311 attribution | never building-level | building-level on one-building lots |
| D30 | Validation | add Parts C, D, E; A and B stay separate and pending | |
| D31 | Over-redaction of "NOV #" | accept and pin by test | add a narrow, tested exception |

## 17. Still tracked before public launch

Unchanged from earlier tasks: rate limiting, CORS, apartment and unit parsing, GitHub
Actions upgrades, NYC API terms of use, a privacy notice stating retention and backup
periods, and deployment of the retention job (not authorized).

## 18. Required amendments (2026-10-23, after the 04:29Z review)

Sanyam accepted D18 to D31 subject to four amendments. Each is written here against the
Task 4 schema as built (migrations 0004 to 0010 and `storage/repository.py`), and checked
against the step-1 measurements (`docs/phase2_task5_step1_report.md`).

### 18.1 Amendment 1: the `k311_` token and lineage contract

**Correction.** Section 12 named a `source_row_id` column on `records.normalized_record`.
There is none. As built:

- `records.normalized_record` has `source_record_id text NOT NULL` (0004), and its
  payload is checked by `payload_has_no_private_keys`, which calls
  `records.forbidden_keys_absent` and already rejects a `unique_key` key at any depth
  (0004). `source_fetch.result_payload` has the same check.
- `records.record_source_row` has `source_record_id text NOT NULL` and a nullable
  `source_row_id`. 0006 requires that a record **without** `source_rows` in its payload
  has exactly one link, with `source_row_id` NULL, and that every link's
  `source_record_id` equals `coalesce(source_row_id, record.source_record_id)`.
- `records.evidence_identity` (0007) exposes `original_digest`, `dataset_id`,
  `source_record_id` from `evidence.raw_row` to `ingest_writer` only, and the commit-time
  trigger `check_link_identity` requires each link's `source_record_id` to equal its raw
  row's `source_record_id`.
- Row-level lineage (`source_rows`, `source_row_id`) is used only by HPD registrations and
  contacts, where `source_row_id` is the Socrata `:id`.

**Contract.**

| Place | Value for a 311 record | Enforced by |
|---|---|---|
| Token | `k311_` + first 32 hex characters of HMAC-SHA256(`EVIDENCE_DIGEST_KEY`, `b"nyc311-unique-key\x00" + unique_key`) | Python, one function, unit-tested |
| `normalized_record.source_record_id` | the token | new 0011 CHECK: for `record_kind = 'service_request_311'`, `dataset_id = 'erm2-nwe9'`, `source_record_id ~ '^k311_[0-9a-f]{32}$'` and `payload ->> 'request_ref' = source_record_id` |
| record payload | `request_ref` = token; no `unique_key`, no `:id` | existing `forbidden_keys_absent` (unique_key); new 0011 CHECK that a 311 payload has no `source_rows` and no `:id` key |
| `record_source_row` | exactly one link per record; `source_row_id` NULL; `source_record_id` = token | existing 0006 rules (single-row record) and `check_link_identity`; nothing new |
| `evidence.raw_row.source_record_id` | the token | new 0011 CHECK: `dataset_id <> 'erm2-nwe9' OR source_record_id ~ '^k311_[0-9a-f]{32}$'` |
| `records.evidence_identity` | shows the token only | follows from the raw-row CHECK; grants unchanged |
| `evidence.raw_row.original_row` | the full original row, plain `unique_key` included | `evidence_admin` only; 180-day expiry; 0010 trigger |
| `fetch_page.url`, `source_fetch.where_clause`, provenance | `bbl in (...) AND created_date >= ... AND agency not in ('HPD','DOB')`; offset paging; `$order=unique_key` names the field only | no value of `unique_key` or `:id` is ever placed in a query |
| `incomplete_reasons`, warnings | field names only (`duplicate_unique_key_across_pages`); a record is named by its token | adapter code and the privacy audit |
| logs and errors | counts and dataset ids only (the pager logs nothing else); Task 4 scrubbed write errors | existing; the runbook's `log_error_verbosity = terse` covers a raw-row `DETAIL` line on the server |

**Why no `:id` for 311.** The Socrata `:id` is also a row locator: anyone can fetch the full
public row with it, which is exactly what keeping `unique_key` private prevents. So 311
never selects `:id`, never pages by it, and never uses row-level lineage. The dictionary
says `unique_key` is unique, and the measurements found no repeat, so it is a valid
ordering key for the pager.

**Duplicate source rows.**

| Case | What happens | Constraint effect |
|---|---|---|
| Same `unique_key`, identical content | identical keyed digest, one raw row; one record flagged `duplicate_id_same_content` | existing 0006 rule: fewer links than `source_count` only when collapsed |
| Same `unique_key`, different content | two records with the same token and different digests, both flagged `duplicate_id_different_content`; the pager adds `duplicate_unique_key_across_pages`, so the fetch is `incomplete_pagination` | none needed: no uniqueness on `source_record_id`; raw rows are keyed by digest |
| Same row twice through offset paging (snapshot moved) | as the first case, and the fetch is already incomplete | existing rule: a raw row may be linked twice only in an incomplete fetch |

**What the token does not do.** It keeps the 311 identifier out of everything public. It
does not make the public record unlinkable to the open dataset: a lot, a date and a
complaint type can often be matched back to the public row by anyone. The public record
holds only what the open dataset already publishes, minus identifiers, location detail
and exact times.

**Key rotation.** A new `EVIDENCE_DIGEST_KEY` gives new tokens. Old snapshots keep their
old tokens, so the same request is no longer linkable across the rotation. Acceptable and
documented.

### 18.2 Amendment 2: free-text privacy

Measured on 2026-10-23 (311: rows created since 2026-09-01, agencies other than HPD and
DOB, 423,189 rows; ECB: the 5,000 newest rows issued since 2024). Text was scanned
locally; only counts were written.

| Field | Measured | Classification | Public rule |
|---|---|---|---|
| 311 `complaint_type` | 149 values; 31 values (175 rows) seen fewer than 20 times | agency picklist ("Problem", defined by the responding agencies) | **allowlist** (below) |
| 311 `descriptor` | 683 values; 264 rare values (1,714 rows) | agency picklist ("Problem Detail") | allowlist |
| 311 `descriptor_2` | 508 values; 263 rare values (1,528 rows); longest rare value 59 characters; rare values are category names (e.g. "Tenant Refused Repair Request") | agency picklist ("Additional Details, a third level of detail") | allowlist |
| 311 `location_type` | 148 values; 63 rare (368 rows) | agency picklist | allowlist |
| 311 `resolution_description` | 497 values; 228 rare (1,306 rows); all inspected values are agency template sentences; 15 of 5,000 contain a phone number, all agency numbers (311, DCWP) | agency template | allowlist, then unit and floor redaction |
| 311 `landmark` | 7,121 values; 4,503 rare | place name | withheld |
| 311 `incident_zip` | | location | withheld (the lot is already known) |
| ECB `violation_description` | unit-id redactor fired on 193 of 5,000 rows, floor on 875; 19 emails (all DOB addresses seen); 5 phone numbers (one a private business number); 13 honorific patterns, mostly "MR" for machine room, but **at least one names a person** | inspector free text | **withheld in V1** (changes 10.1) |
| ECB `section_law_description1..10` | 980 code-text pairs, every infraction code has exactly one text | fixed code list | shown only when the code and text match the measured list; otherwise withheld |
| ECB `violation_type` | 15 values | picklist | shown |
| DOB `house_number`, `house_street` | agency-entered building address | administrative address | shown after the unit and floor redaction pass |

**Allowlist rule (311).** A picklist or template value is shown only if it is in a committed
allowlist built from (a) the 311 dictionary's complaint-detail tab and (b) values seen at
least 20 times citywide in a fixed reference window. Any other value becomes
`[UNCOMMON VALUE WITHHELD]` with flag `uncommon_value_withheld`. The dictionary tab alone
covers too little (59.5% of `complaint_type` rows, 33.5% of `descriptor`, 75.7% of
`descriptor_2`, 98.4% of `location_type`), so both sources are needed. The allowlist is a
reviewed data file, built in step 4.

**D31 resolved.** The redactor's unit pattern requires APT, APARTMENT or UNIT before the
identifier, so "NOV # 38167719M" is not redacted. The question no longer matters,
because ECB descriptions are withheld.

**Privacy test matrix additions** (replace rows 24 and 25 of 11.2). Sentinel values for a
person's name ("MR. JOHN SENTINEL"), a unit ("APT 4B-SENTINEL"), a floor, a phone number,
an email, an exact location (coordinates, cross streets) and a personal detail ("injured
SENTINEL") are placed in **every** public text field: 311 `complaint_type`, `descriptor`,
`descriptor_2`, `location_type`, `resolution_description`; ECB `violation_description`,
`section_law_description1..10`, `violation_type`; DOB `house_street`. The test asserts
none of them appears in record payloads, `source_fetch.result_payload`, API output, logs,
error text, `fetch_page.url` or `evidence_identity`, and that each is present only in
`original_row`.

### 18.3 Amendment 3: conflicting BIN and lot evidence (D24 clarified)

Sources that can place a BIN on a lot: the resolver (for the resolved BIN only),
Footprints (`base_bbl`, `mappluto_bbl`), HPD buildings (`boroid`/`block`/`lot`), and an
ECB row's own `boro`/`block`/`lot`. The **lot family** of the resolved lot is the resolved
BBL plus every `base_bbl` and `mappluto_bbl` that Footprints gives for the lot's BINs.
Measured: for the 1845 ACP condo, Footprints gives `base_bbl` 1018220001 and
`mappluto_bbl` 1018227502 (the billing lot) for both BINs, and DOB recorded 23 ECB rows
for BIN 1054974 on lot 0001.

| BIN evidence | Example (measured) | DOB complaints | ECB rows on the lot family |
|---|---|---|---|
| All sources that know the BIN agree | 220 W 17th, 8 BINs | `this_building` (R) or `other_building_on_lot` | same |
| One source places the BIN on the lot; the other is **silent** | 1134 West Farms BIN 2117584: Footprints yes, HPD has no record | `other_building_on_lot`, flag `bin_single_source`, provenance listed. Silence is not a conflict (HPD covers only buildings under its jurisdiction) | same |
| Two sources place the BIN on **different** lots outside the lot family | none at the test lots; synthetic | BIN not searched; reported in a `bin_lot_conflicts` list. If it is R: every row `unattributed_on_lot`, `lot_membership = conflicting`, flag `bin_lot_conflict`. **Never** `this_building` or `other_building_on_lot` | `unattributed_on_lot`, `conflicting`, `bin_lot_conflict` |
| Row lot inside the lot family but not the resolved lot (condo base lot) | ACP: BIN 1054974 on lot 0001 | n/a | attribution by BIN as usual, `lot_membership = confirmed`, flag `condo_lot_family` |
| Row lot outside the family for a lot BIN (often a lot that no longer exists) | ACP BIN 1055002: 28 ECB rows from 1990 to 2009 on lot 0061, which is in neither Footprints nor HPD today | not fetched unless the BIN is R; if R: `unattributed_on_lot`, `conflicting`, `lot_identifier_conflict` | |
| BIN on the lot family's rows, in no current source | 1217 West Farms BIN 2010717 (BIS violations; 1 DOB complaint) | not searched; coverage note | `unattributed_on_lot`, flag `bin_retired_or_unknown` |
| BIN on the lot family's rows, Footprints places it on another lot | synthetic | not searched | `unattributed_on_lot`, flag `bin_on_other_lot` |
| Placeholder or missing BIN | synthetic | never searched | `unattributed_on_lot`, `bin_placeholder` or `bin_missing` |
| Tax-lot scope | 1217 West Farms | `unsupported_join` | `lot_level` for every row |

To tell "retired" from "on another lot", the adapter looks up the unknown BINs from ECB
rows in Footprints by BIN (one extra query, recorded in provenance). New flags:
`bin_single_source` (replaces `bin_sets_disagree`), `bin_lot_conflict`,
`bin_retired_or_unknown`, `bin_on_other_lot`, `condo_lot_family`.

Tests added to 11.2: resolved BIN, other lot BINs, single-source BIN, explicit two-source
conflict (on R and on another BIN), condo base lot, historical lot, retired BIN, BIN on
another lot, missing and placeholder BIN, and tax-lot scope, using the fixtures where
they exist (draft ground truth in the step-1 report) and synthetic rows otherwise.

### 18.4 Amendment 4: incomplete counts and unstable snapshots

**Measured.** Ten identical citywide 311 counts returned two truth headers ("Thu, 22 Oct
2026 01:37:36 GMT" and "Fri, 23 Oct 2026 01:38:29 GMT") and **two counts, 22,879,856 and
22,892,475**. The two versions differ by 12,619 rows. In ten count-then-data pairs at
350 5th Ave, 4 pairs had different headers on the count and the data, yet all 10 had
count = rows (504). During the committed fixture recording (04:52Z) the existing pager
restarted on a header change, and two of the five 311 fetches still ended
`dataset_changed_during_retrieval`; different addresses landed on different versions. The DOB complaint
and ECB datasets each returned one header and one count over ten calls.

**D19, revised: excluded rows are counted, not retrieved.** The HPD and DOB filter moves
into `$where` (`agency not in ('HPD', 'DOB')`; no 311 row with a null agency was found
since 2026-09-01). The retrieved set is then exactly what is normalized, so the Task 4
lineage rule (links equal `source_count`) holds unchanged. The excluded numbers come from
a separate `count(*) ... group by agency` query and are reported as
`excluded_source_counts` with that query's own truth header and time, labelled "source
count, not retrieved". If that query fails, or its header differs from the main fetch's
header, or the main fetch is incomplete, the numbers are shown as not established for
this snapshot, with the reason, and never presented as covering the full result.

**D27, revised: what the one-page strategy establishes.**

- Established when complete: every returned row came from one HTTP response carrying one
  truth header; the count came from a response carrying the **same** header; rows equal
  the count; keys are distinct.
- Not established: that the count and the data were served by the same replica (two
  replicas could carry the same header; not verified either way); that the version
  served is the newest (the other version may be a day newer); that the row set equals
  what another version would return. Equal counts across different headers prove
  nothing: the two versions' totals differ.
- Rule: a count and a data response with different headers are not a complete pair.
  Retry the pair up to twice; if no attempt yields matching headers and counts:
  `incomplete_pagination`, reason `source_snapshot_unstable`, rows kept and marked.
  Multi-page 311 results keep the existing rule (one header across count and all pages).
- Provenance keeps every header seen, including those of discarded attempts
  (`attempt_headers`), and `data_as_of` is the header of the accepted pair. When two
  versions were seen, the result says so.

### 18.5 Revised decisions

| # | Revised recommendation |
|---|---|
| D19 | Filter HPD and DOB rows in `$where`; report their numbers from a separate count query as source counts, with header and time, and only when consistent with the main fetch (18.4) |
| D20 | The token contract in 18.1, including no `:id` and no row-level lineage for 311 |
| D21 | Public 311 fields: agency, allowlisted `complaint_type`, `descriptor`, `descriptor_2`, `location_type`, allowlisted and redacted `resolution_description`, status, dates (date only), BBL. Withheld: address as typed, street names, cross and intersection streets, landmark, zip, coordinates, location, channel, due date |
| D24 | The evidence table in 18.3: silence is not conflict; explicit two-source conflict never yields a building attribution; condo lot family from Footprints; historical lots and retired BINs preserved as uncertain |
| D27 | Header-matched count and data, two retries, then `source_snapshot_unstable` (18.4) |
| D31 | Resolved: ECB descriptions withheld in V1; the redactor does not touch "NOV #" |

New privacy decision for review: **ECB `violation_description` withheld from public
output in V1** (18.2), with the infraction codes and their fixed law texts shown instead.

### 18.6 Disposition codes reconciled with DOB's 2021 list (2026-10-23, after 05:15Z)

**Source.** DOB, "BIS Complaint Disposition Codes", Rev. 09/21, 6 pages, PDF created
2 September 2021, from
`https://www.nyc.gov/assets/buildings/pdf/bis_complaint_disposition_codes.pdf`. nyc.gov is
blocked from the cloud environment, so the copy used was downloaded from that URL by
Sanyam and supplied through the project on 2026-10-23. SHA-256
`7ee93140b24d56fec2bacddc3c5f6037ab3b377c7d112440c2a322f3efc636ba` (175,729 bytes). The
PDF is not committed. `backend/scripts/task5_extract_dob_codes.py` extracts its 184 codes
verbatim (only line wrapping is joined) into `docs/task5/dob_disposition_codes_2021.json`.

**Counts.** All counts come from the recorded step-1 measurements
(`docs/task5/step1_measurements.json`, 04:39Z). No new snapshot was taken.

**Definition rule.** The 2021 text is used where the PDF has the code. The 2017 table
(`6v9u-ndjg`) is used only for the 9 codes the PDF no longer lists. Both texts are kept
side by side. A code in neither stays `unknown_code`, with no guessed meaning.

**Coverage.**

| | Codes | Rows |
|---|---|---|
| In use but not in the 2017 table (step 1) | 39 | 25,956 |
| Now defined by the 2021 PDF | 20 | 13,190 |
| Still without an official definition (`unknown_code`) | 19 | 12,766 |

Resolved: AA, AB, AC, EF, EG, EJ, EK, J5, J6, U1, U2, U3, U4, U5, UA, UB, V1, V2, X1, X2.
Still unknown: AD, AE, AF, AG, AH, AI, AJ, D0, EI, H6, H7, H8, L4, N1, N2, N3, Q6, Q7, Y5.
All 19 are CLOSED except D0 (5 rows) and Q6 (43 rows), which are ACTIVE.

**Outcomes (replaces the table in 4.2).** Five concepts that were mixed before are now kept
apart, each following DOB's own verb:

| Outcome | Rule | Codes |
|---|---|---|
| `violation_served` | text says "Served" | A1, A2, A4, A5, A8, A9, H4, V3, W1 |
| `violation_issued` | text says "Issued" or "Written", not "Served" | A6, H3, R5, RK, MB to MK (not MA), U3, U6, X2, X7, X8 |
| `violation_prepared_not_served` | "Prepared and Attempt to Serve will be Made" | B1, B2 |
| `corrective_action_requested` / `_corrected` / `_not_corrected` | HRP request for corrective action, by stage | AA / AB / AC |
| `inspection_completed` | "Compliance Inspection Performed" (no result stated) | I3 |
| `inspection_passed` / `inspection_failed` | "PASS Inspection" / "FAIL Inspection" | V1 / V2 |
| `inspection_findings_under_review` | sign inspection, findings under review | J5 |
| `order_event` | an order is recorded (5.2) | A3, H5, K4, K6, L1, L2, L3, P3, P4, RZ, U4, U5, Y1 to Y4 |
| `no_violation` | no violation or enforcement action | I2, MA, R1, R6, U2, WA, WF, X1 |
| `unsubstantiated` | I1 | I1 |
| `no_access` | C1 to C8, WB, U1 | |
| `referred` | F1 to F9, G1 to G9, WI | |
| `assigned_or_pending` | D1 to D9, E1 to E9, EA to EH, EJ, EK, EZ, J1, J3, J4, P2, K2, A7 | |
| `other_recorded` | any other officially defined code (for example ND, J6, UA, UB, RA, RU) | |
| `unknown_code` | no official definition | the 19 above |

Changes from the step-1 proposal: H3, R5, RK, A6 and MB to MK move from
`violation_served` to `violation_issued` (their text says issued or written); I3 moves from
`other_recorded` to `inspection_completed`; the 20 resolved codes leave `unknown_code`.
A7 and E2 ("Padlock Unit") are assignments, not orders. RA ("Commissioner's Order: Issued
to Owner") and the emergency declarations stay `other_recorded`: they are not stop-work,
vacate or padlock orders.

**Order events (amends 5.2).** U4 ("CSC: Full Stop Work Order Issued – On Action
Complaint", 5 rows) is `stop_work_full_issued`; U5 ("CSC: Partial SWO Issued – On Action
Complaint", 1 row) is `stop_work_partial_issued`. Each order event also gets an
`order_action` from DOB's own verb: `served` (A3, Y1, Y3), `issued` (K6, P3, U4, U5),
`rescinded` (L2, L3, P4, Y2, Y4), `previously_issued` (RZ), `noncompliance_violation`
(H3, H4, V3, W1) or `not_stated` (H5, K4, L1). ME, MF, MH and MI keep their vacate event
with action `issued`. The event label names the kind of order; the action says what the
record says happened. Neither says the order is in effect now (D25 unchanged). A label
contains "issued" only when DOB's text says served, issued or previously issued; L1 and
K4, whose text has no verb, are `stop_work_partial_recorded` and
`stop_work_crane_no_address_recorded` (corrected after the 06:22Z review).

**Status disagreement, recomputed.** ACTIVE complaints whose code is a final outcome: 897
(I2 1, X1 863, X2 33; step 1 reported 1 because X1 and X2 were unknown then). These keep
both values and are flagged `status_fields_disagree` (4.3). Every order-code complaint
is still CLOSED.

**2017 table versus 2021 PDF.** 105 codes have the same wording after normalization; 9
differ only by "ECB" becoming "OATH" (A8, MC, MD, ME, MF, MG, MH, MI, MK); 37 differ in
other wording; 33 are new in 2021; 9 appear only in 2017 (A5 55 rows, D8 9, EE 3, F4 2,
RZ 2, and E7, E8, EA, RT unused). The full side-by-side list is in
`docs/task5/proposed_mappings.md`. Differences that matter for wording:

| Code | 2017 table (`6v9u-ndjg`, updated 2017) | 2021 PDF (Rev. 09/21) | Effect |
|---|---|---|---|
| Y1 | FULL VACATE ORDER ISSUED F | Full Vacate Order Served | action `served` (2021 text) |
| Y3 | PARTIAL VACATE ORDER ISSUED P | Partial Vacate Order Served | action `served` |
| H5 | STOP ALL WORK / NO TCAO ISSUED | Stop All Work/No TCAO (Temporary Certificate of Approval and Operation) | action `not_stated` |
| P4 | CLOSURE/PADLOCK ORDER RESCINDED | Closure/Padlock Order Issued Rescinded | still `padlock_rescinded` |
| B2 | ECB VIOLATION(S) PREPARED & ATTEMPT TO SERVE WILL BE MADE | OATH Summons Prepared and Attempt to Serve will be Made | still prepared, not served |
| C2 | INSPECTOR UNABLE TO GAIN ACCESS - 2ND ATTEMPT | Inspector Unable to Gain Access – 2nd Final Attempt | still `no_access` |
| D4 | COMPLAINT ASSIGNED TO BEST SQUAD | Complaint Assigned to Construction Safety Compliance Unit | unit renamed |

**Tests.** `backend/tests/test_task5_mappings.py` checks the source hash, that every code in
use and every defined code appears once, that only officially defined codes get an
outcome, that the wording matches each outcome, the AA/AB/AC stages, U4 and U5, that every
order event's text names a stop-work, vacate or padlock order, that no label implies an
order is in effect, and the counts above.

### 18.7 Step 2 as built (DOB complaints, 2026-10-23, after 06:22Z)

Where the built adapter differs from, or adds to, earlier sections:

- **Order key.** `$order=:id` with `$select=*, :id`, as in the step-1 recording, not
  `complaint_number, :id` (8.2). `complaint_number` is not unique (6 numbers repeat), so
  `:id` is the pager's unique key; duplicates are handled after the fetch (section 9).
  The Socrata `:id` of every source row is kept on the record (`source_row_ids`); DOB
  complaint rows hold nothing personal.
- **Duplicates.** Copies are compared without `:id`. Identical copies collapse into one
  record that keeps every `:id`; differing copies are all kept, flagged
  `duplicate_id_different_content`, and lose building attribution.
- **BIN evidence.** Footprints by lot (`base_bbl` or `mappluto_bbl`); HPD building records
  from the Task 3 adapter (current records on the confirmed lot, assigned BIN). A BIN
  that only the resolver or HPD knows is looked up in Footprints by BIN: no row means
  silence (`bin_single_source`), a row on a lot outside the lot family means
  `bin_lot_conflict`. HPD is never queried by BIN. If Footprints or HPD cannot be checked,
  only the resolved BIN is searched and the result is `incomplete_pagination` with reason
  `bin_evidence_incomplete`.
- **Categories.** DOB's "Complaint Category Description" (Rev. 12/18), the PDF attached to
  `eabe-havv` on NYC Open Data, SHA-256 `e8751c6c...fa0974d`, 97 codes, extracted verbatim
  into `docs/task5/dob_complaint_categories_2018.json`. 100 codes in use (477,755 of
  3,145,939 rows, recorded measurements) are not in that list: their records keep the
  raw code with no description and flag `complaint_category_unknown`.
- **Reference file.** `records/data/dob_complaints_reference.json` is generated from the
  approved mapping and the category list by `scripts/task5_build_dob_reference.py`; a
  test rebuilds it and compares.
- **Schema.** A required field absent from every row gives `source_unavailable` with
  `schema_changed` and no records (8.1).
- **Order wording.** `DOB recorded disposition {code} ("{DOB text}") on {date} for
  complaint {number}. This dataset does not show whether the order is still in effect.
  Check DOB's own records.` DOB's own text replaces the event label, so a code with no
  verb is never described as issued.
- **Not in step 2:** storage (needs migration 0011, step 5), the retired-BIN note from
  ECB rows (step 3), freshness from catalog metadata, ECB and 311.

### 18.8 Step 3 as built (DOB ECB violations, 2026-10-23, after 07:00Z)

Where the built adapter differs from, or adds to, earlier sections. Section 18 still
takes precedence over sections 1 to 17.

- **Query.** The step-1 recording's form, reproduced exactly: for each lot in the lot
  family, in BBL order, `(boro='B' AND block='BBBBB' AND lot in ('LLLL', '0LLLL'))`,
  joined by OR; at building scope ` OR bin='R'` (resolved BIN only); `$order=
  isn_dob_bis_extract`, no `$select`. This replaces the 8.2 sketch. The block is always
  the 5-digit form, as measured (one 4-digit block in 1.84 million rows). At tax-lot scope
  there is no BIN clause, and the result model refuses one.
- **Lot family.** Building scope reuses the DOB complaint BIN evidence (Footprints by
  lot, HPD buildings, Footprints by BIN for BINs Footprints does not place). Tax-lot scope
  uses Footprints by lot only (no HPD, no lookup by BIN). Only a failed or incomplete
  Footprints lot lookup makes the ECB result `incomplete_pagination`
  (`lot_family_incomplete`), because only it can shrink the query. Other evidence gaps
  are warnings: the rows they touch fail closed.
- **Attribution (building scope).** From the row's own identifiers, basis
  `row_identifiers`:

  | Row lot | Row BIN | Attribution | Lot membership | Flags |
  |---|---|---|---|---|
  | in family | R | `this_building` | `confirmed` | `condo_lot_family` if not the resolved lot |
  | in family | Footprints or HPD place it on the lot | `other_building_on_lot` | `confirmed` | `bin_single_source` if one source |
  | in family | in no current source (Footprints by BIN: no row) | `unattributed_on_lot` | `confirmed` | `bin_retired_or_unknown` |
  | in family | Footprints by BIN: another lot only | `unattributed_on_lot` | `conflicting` | `bin_on_other_lot` |
  | in family | two-source conflict (in `bin_lot_conflicts`), including R | `unattributed_on_lot` | `conflicting` | `bin_lot_conflict` |
  | in family | missing, placeholder, invalid | `unattributed_on_lot` | `confirmed` | `bin_missing` / `bin_placeholder` / `bin_invalid` |
  | in family | Footprints by-BIN lookup failed | `unattributed_on_lot` | `confirmed` | `bin_not_on_current_lot` |
  | outside family | any | `unattributed_on_lot` | `conflicting` | `lot_identifier_conflict` |
  | missing or malformed | R (no conflict) | `this_building` | `via_bin_mapping` | `lot_parts_missing` |
  | missing or malformed | anything else | `unattributed_on_lot` | `conflicting` | `lot_parts_missing` |

  Tax-lot scope: every family-lot row is `lot_level`; a BIN not on the Footprints lot list
  is flagged `bin_not_on_current_lot` and never looked up or promoted.
- **BINs seen only on ECB rows** are listed in `row_bins_not_on_lot` with their
  classification and rows. They are never added to the DOB complaint BIN search.
- **Duplicates.** One query, so a row matching both clauses arrives once; `matched_by`
  says which clauses it satisfies. Identical copies of an extract id collapse with every
  copy in lineage (`row_index`); differing copies, and a summons number repeated under two
  extract ids, are all kept, flagged `duplicate_id_different_content`, and lose building
  attribution. A repeated extract id also makes the fetch incomplete (the paging key).
- **Statuses.** `correction_status` from `ecb_violation_status` (documented),
  `adjudication` from `hearing_status` (the approved mapping, with `upheld_by_default`
  for DEFAULT), `certification_status` as recorded. Raw values are kept. Each record
  carries an `adjudication_note`; DEFAULT, POP/IN-VIO and WRITTEN OFF say their meaning
  is not documented. ACTIVE with dismissed, and RESOLVE with pending, are flagged
  `status_fields_disagree`. Every result carries a statement that a summons is a charge,
  not a finding.
- **Severity.** CLASS - 1, 2, 3 are kept and flagged `severity_undocumented_value`.
- **Money.** Decimal, plain numbers only (`-?digits(.dd)?`); anything else is `None`
  with `amount_unparseable`. Negative balances are kept as recorded. The financial
  summary is for `this_building` records only, and is absent at tax-lot scope or when
  the resolved BIN conflicts. Unparseable rows are counted and excluded from totals.
- **Privacy.** Never public: `respondent_*`, `violation_description`, `hearing_time`.
  Picklists (correction status, hearing, certification, severity, violation type,
  aggravated level) are shown only if on the measured list, else
  `[UNCOMMON VALUE WITHHELD]` with `uncommon_value_withheld`. Infraction codes are shown
  only if on the measured code list; a section/law text only for an allowlisted pair
  (`docs/task5/ecb_reference_measurements.json`: 1,011 pairs, every code with one text;
  898 allowlisted; 108 with a missing code or text; 5 withheld on the privacy pattern,
  all "FLOOR NUMBERING SIGNS", fail closed). Section 18.2 reported 980 pairs from an
  earlier scan; this measurement, over all ten columns, finds 1,011. Summons and DOB violation numbers must match their formats, else they are
  withheld (`identifier_withheld`). Malformed dates do not show their raw text. Raw rows
  go only to the evidence store, with keyed digests and the existing 180-day retention.
- **New values.** `EvidenceKind.enforcement_action`; flags `bin_retired_or_unknown`,
  `bin_on_other_lot`, `condo_lot_family`, `severity_undocumented_value`,
  `uncommon_value_withheld`, `amount_unparseable`, `identifier_withheld` (additions only).
- **Restarts.** The pager may restart twice on a changed truth header (18.4).
- **Not in step 3:** storage (migration 0011, step 5), freshness from catalog metadata,
  the OATH cross-check (validation part D), 311.

### 18.9 Step 4 as built (311 Service Requests, 2026-10-23, after 07:55Z)

Where the built adapter differs from, or adds to, earlier sections. Section 18 still
takes precedence over sections 1 to 17.

- **Query (replaces the 8.2 sketch).** `bbl in (<lot family, sorted>) AND created_date >=
  '2020-01-01T00:00:00' AND agency not in ('HPD', 'DOB')`, `$order=unique_key`, no
  `$select` (so no `:id`). This is the step-1 recording's form, reproduced exactly. Every
  lot value is checked as a 10-digit BBL before it is quoted. The result model refuses a
  `$where` naming `unique_key`, `:id` or `bin`.
- **Lot family.** The resolved BBL plus the `base_bbl` and `mappluto_bbl` that Footprints
  gives for the lot, from the Footprints lot lookup only, at both scopes. There is no
  HPD lookup and no lookup by BIN (311 has no BIN). A failed or incomplete lookup makes
  the result `incomplete_pagination` (`lot_family_incomplete`).
- **Attribution.** Building scope: every row `unattributed_on_lot`. Tax-lot scope: a row
  whose BBL is in the family is `lot_level`. A row with no BBL (`bbl_missing`), a
  malformed BBL (`bbl_invalid`) or a BBL outside the family (`lot_identifier_conflict`)
  is `unattributed_on_lot` with `lot_membership = conflicting` at both scopes. A row on a
  family lot other than the resolved lot is flagged `condo_lot_family`. Nothing else
  (address, location type, a one-building lot) changes the attribution, and the models
  refuse `this_building` and `other_building_on_lot`.
- **Snapshots (D27).** The pager restarts up to twice when the count and data headers
  differ, when rows and count disagree (`short_page`, `row_count_mismatch`,
  `page_too_long`) or when a key repeats. Each attempt is kept (`attempts`: count header,
  page headers, count, rows, reasons, accepted). Rows from different attempts are never
  combined. If the last attempt still has a header or count reason, the result is
  `incomplete_pagination` with `source_snapshot_unstable`; a repeated key keeps its own
  reason `duplicate_unique_key_across_pages`. `data_versions_seen` lists every header;
  `accepted_data_version` is set only for a complete fetch. Row cap and failed pages are
  not retried. A failed retry keeps the previous attempt, marked `retry_failed`.
- **Excluded counts (D19).** A separate `$select=agency, count(*) AS n ... AND agency in
  ('HPD', 'DOB')&$group=agency` query. Established only when it succeeded, the main fetch
  is complete, and its header equals the accepted data version; otherwise
  `not_established_reason` says which (`count query failed`, `main fetch incomplete`,
  `count response has no data version header`, `data version differs`). An agency
  missing from the grouped answer is 0 only when established. The numbers are never
  records and never enter the summary totals.
- **NULL agency (new decision, for review).** NULL-agency rows cannot match `agency not
  in (...)`. A second count, `... AND agency IS NULL`, runs on each lookup. Established
  and above 0: the result is `incomplete_pagination` (`null_agency_rows_not_retrieved:
  n`). Not established: a warning, and the status is unchanged, because none was found
  citywide since 2026-09-01 (step 1). The recordings have no answer for this query, so
  in replay it fails and is shown as not established.
- **Token and evidence.** `request_ref` = `source_record_id` = evidence `source_record_id`
  = the `k311_` token (18.1); `source_row_id` is always null. A row with a missing or
  malformed `unique_key` (not 1 to 12 digits) gets no token, no record and no evidence;
  it is counted in `rows_rejected` and the result is incomplete. Identical copies of one
  key collapse (`collapsed_copies`, `duplicate_id_same_content`); differing copies are all
  kept with the same token and `duplicate_id_different_content`.
- **Public fields (D21).** `agency` (allowlisted agency codes), `complaint_type`,
  `descriptor`, `descriptor_2`, `location_type` (allowlists), `resolution_description`
  (allowlisted template, then the unit and floor redactor), `status` (measured or mapped
  values only), the three dates (date part only), and the BBL. Every other source field
  is withheld and named in `withheld_fields`. A value off its list, or matching the unit,
  floor, phone, email or honorific pattern even if listed, becomes `[UNCOMMON VALUE
  WITHHELD]` with `uncommon_value_withheld`.
- **Allowlists.** `records/data/sr311_reference.json` (SHA-256
  `6b2a6055...d532`), built by `backend/scripts/task5_build_311_reference.py` from
  `docs/task5/sr311_dictionary_values.json` (the dictionary tab, extracted by
  `backend/scripts/task5_extract_311_dictionary.py`; workbook SHA-256 `6cc30e13...94fc`)
  and the step-1 vocabularies (window: created since 2026-09-01, agency not HPD or DOB,
  at least 20 rows). A test rebuilds it byte for byte and pins both hashes. It is a
  snapshot, not exhaustive. Its step-1 vocabulary queries came back on two data versions
  (`complaint_type` and `resolution_description` on Thu 22 Oct 01:37:36; the other three
  on Fri 23 Oct 01:38:29), so its counts differ slightly from the 18.2 table.
- **Statuses and dates.** Approved mapping (4.2); `Unspecified` is shown and mapped to
  `unknown`; an unmeasured value is withheld and `unknown`; `unknown` always carries
  `status_unknown`. `status_fields_disagree` for closed without `closed_date` and open with
  one. Status, `closed_date` and `agency_updated_date` stay separate. Malformed dates show
  no raw text (`unparseable_date`); before 1900 or after the fetch date
  `implausible_date`; closed or updated before created `date_order`.
- **Summary.** By attribution: totals by agency, complaint type, status, and
  agency/type/status; 12- and 36-month windows only when the fetch is complete (else
  null); counts of status disagreements and withheld values; `building_specific_claims_allowed`
  is always false; a statement that a request is a report, not a finding, and that closed
  is not repaired.
- **Not in step 4:** storage (migration 0011, step 5), freshness from catalog metadata,
  validation part E.

## Appendix: live queries (2026-10-23, about 03:55 to 04:15 UTC)

All against `https://data.cityofnewyork.us/resource/<id>.json`, metadata from
`/api/views/<id>.json`, catalog from `/api/catalog/v1`.

```
# Row counts and status values
eabe-havv   $select=count(*)
eabe-havv   $select=status,count(*) $group=status
6bgk-3dad   $select=ecb_violation_status,count(*) $group=ecb_violation_status
6bgk-3dad   $select=length(lot),count(*) $group=length(lot)
855j-jady   $select=violation_status,count(*) $group=violation_status
erm2-nwe9   $select=count(*) $where=created_date>='2026-09-01T00:00:00'
erm2-nwe9   $select=count(*) $where=created_date>='2026-09-01T00:00:00' AND bbl IS NULL

# Duplicate checks
eabe-havv   $select=complaint_number,count(*) $group=complaint_number $having=count(*)>1 $limit=3
6bgk-3dad   $select=ecb_violation_number,count(*) $group=ecb_violation_number $having=count(*)>1
6bgk-3dad   $select=isn_dob_bis_extract,count(*) $group=isn_dob_bis_extract $having=count(*)>1
erm2-nwe9   $select=unique_key,count(*) $group=unique_key $having=count(*)>1 (on the test lots)

# Per address (example: 350 5th Ave, BBL 1008350041, BIN 1015862)
eabe-havv   $where=bin='1015862'
eabe-havv   $where=bin in (<lot BINs>)          $select=bin
eabe-havv   $where=house_number='220' AND upper(house_street) like '%17%'   (address-only test)
3h2n-5cm9   $where=boro='1' AND block='00835' AND lot='00041'
6bgk-3dad   $where=boro='1' AND block='00835' AND lot='00041'
6bgk-3dad   $where=boro='1' AND block='00835' AND lot='0041'
6bgk-3dad   $where=bin='1015862'
855j-jady   $where=bbl=1008350041
erm2-nwe9   $where=bbl='1008350041' $select=agency,complaint_type,bbl,incident_address,status
5zhs-2jue   $where=base_bbl='<bbl>' OR mappluto_bbl='<bbl>'   (Footprints BINs on lot)
kj4p-ruqc   $where=boroid='<b>' AND block='<block>' AND lot='<lot>' $select=bin   (HPD BINs)

# Overlap (1134 West Farms)
erm2-nwe9   $where=bbl='2027540005' AND agency='HPD' $select=unique_key
ygpa-z7cr   (HPD complaints on the same lot) $select=unique_key

# Truth header stability: five calls of the same 311 count query, header recorded each time
```

Scratch scripts and raw outputs were kept outside the repository; they contain no
secrets and no personal data beyond what the open datasets publish.
