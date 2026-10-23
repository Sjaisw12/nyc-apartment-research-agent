# Phase 2, Task 5: DOB and 311 records (design for approval)

Status: **design only, no implementation.** Written 2026-10-23 on branch
`claude/project-thread-5d0l0f`, after Task 4 was approved and closed (2026-10-23 03:53Z).
No adapter, model, migration or test code is written for Task 5 until this design is
approved.

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
| 1134 West Farms Rd (2027540005 / 2006185) | 8 on 2006185; 4 on 2117584 | 0 | 399 (HPD 309, NYPD 76) | Footprints lists BINs 2006185 and 2117584; HPD lists only 2006185 |
| 220 W 17th St (1007660055 / 1081543; lot BINs 1081542 to 1081549) | 17 on 1081543 (1 active); 19 on 1081547; 4 on 1081549; others | 0 | 165 | 311 addresses on this lot: 224 W 17 (42), 218 FRONT W 17 (29), 220 (26), 218 (23) |
| 1217 West Farms Rd (tax lot 2029930031, no verified BIN; Footprints BIN 2128717) | 6 on 2128717; 1 on retired BIN 2010717 | 0 | 77 | BIN 2010717 is not in Footprints but has 14 DOB violations and 1 DOB complaint. 311 rows mostly addressed "1017 HOME STREET" (75) |
| 350 5th Ave (1008350041 / 1015862) | 141 (1 active), addressed "350 5 AVENUE" (92), "338 5 AVENUE" (24), "350 FIFTH AVENUE" (13), "348 5 AVENUE" (2) | 241 | 504 | Lot format split above |
| 1845 Adam Clayton Powell Jr Blvd (condo lot 1018227502 / 1054974 and 1055002) | 111 on 1054974 (74 addressed "1845 7 AVENUE"); 120 on 1055002 | 0 | 435 | Condo billing lot |

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

### 4.2 Status vocabulary (public)

**DOB complaint `complaint_status`:** `active` (ACTIVE), `closed` (CLOSED), `unknown`
(anything else, flag `status_unknown`).

**DOB complaint `outcome`** (from `disposition_code`, using `6v9u-ndjg`):

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

### 5.2 What V1 reports

For each DOB complaint whose disposition is an order code, an `OrderEvent`:

| `order_event` | Codes |
|---|---|
| `stop_work_full_issued` | A3 |
| `stop_work_partial_issued` | L1, K6 |
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

### 6.3 BIN search set for DOB complaints (D24)

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

### 6.4 Decision table

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

### 6.5 Condo lots

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

### 8.2 Per dataset

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

### 8.3 Unstable truth header on 311 (D27)

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

### 10.1 Field classification

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

## 12. Planned PostgreSQL changes (not written yet)

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

## 16. Decisions requested

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
