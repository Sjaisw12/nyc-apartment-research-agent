# Phase 2, Task 3 report: HPD buildings, registrations and registration contacts

Status: implementation complete on branch `claude/project-thread-5d0l0f`, in four separate
commits, for review. Nothing has been merged or deployed. PostgreSQL and Task 4 have not
been started. The HPD Online validations for Task 1 (Part A) and Task 2 (Part B) in
`data/eval/hpd_validation.md` remain **PENDING**.

| Step | Commit | What |
|---|---|---|
| 1 | `82d4dd9` | HPD buildings adapter (kj4p-ruqc), freshness metadata, `administrative_record` kind |
| 2 | `6d25f90` | HPD registrations adapter (tesw-yqqr), registration status (D10, D12) |
| 3 | `ee3cf8e` | HPD registration contacts (feu5-w2e2), privacy controls (D11) |
| 4 | `87646eb` | HPD BuildingID as a secondary attribution key for violations and complaints (D9) |

All counts below come from the recorded cassettes (recorded live on 2026-10-21/22) and
the live tests run on 2026-10-22. The data changes monthly; these are snapshot values.

## 1. Actual counts per test address

| Address | Resolution | HPD building records | Registration rows | Contacts |
|---|---|---|---|---|
| 1134 West Farms Rd (test building) | building | 1 (building 122027, current) | 1 (203625) | 5, 1 batch |
| 220 W 17th St | building | 8 | 8 (8 registrations) | 32, 2 batches of 4 IDs |
| 1217 West Farms Rd | tax lot | 1 (972462, 1017 HOME STREET, BIN 2128717): `lot_level` | 1 (228115), `lot_level` | 4, `lot_level` |
| 350 Fifth Ave (Empire State) | building | 1 (2780, registration id 0, 0 legal units) | 0: `no_records_found` | none; no contact request made |
| 1845 Adam Clayton Powell (condo) | building | 2 | 2 (113010 this building, 113011 other building) | 10 |

Every registration on these lots has an end date of 2026-09-01, while the registrations
dataset's rows were last updated 2026-08-12. Status for all of them is therefore
`on_file_end_date_passed_after_data_update`: the snapshot cannot show whether they were
renewed. Nothing is labelled expired, unregistered or noncompliant.

Contacts at the test building: corporate owner "BRONX 1134 WEST FARMS ROAD L.P.", agent
"LANGSAM PROP SERV CORP", plus a head officer, officer and site manager whose names are
withheld (3 people named, all withheld). HPD's `contactdescription` for registration
203625 is "RECEIVERSHIP"; it is reported only as HPD's description of the registration.

## 2. Before and after BuildingID attribution (D9)

Step 4 is an optional post-step (`records/building_id_attribution.py`). The default
outputs of the Task 1 and Task 2 adapters are unchanged; the step returns a new result
with a `building_id_attribution` report.

**220 W 17th, violations (row counts):**

| Bucket | Before | After |
|---|---|---|
| this_building | 24 | 24 |
| other_building_on_lot | 63 | 150 |
| unattributed_on_lot | 87 | 0 |

**220 W 17th, complaint problems (and complaints):**

| Bucket | Problems before | Problems after | Complaints before | Complaints after |
|---|---|---|---|---|
| this_building | 13 | 13 | 10 | 10 |
| other_building_on_lot | 8 | 92 | 8 | 69 |
| unattributed_on_lot | 84 | 0 | 61 | 0 |

The moved rows carry BuildingID 805523 (61 violations, 54 problems), which maps to BIN
1081547 (224 Front), and BuildingID 30619 (26 violations, 30 problems), which maps to BIN
1081549 (218 Front). The requested building is 806076 (BIN 1081543). **None** of the
moved rows went to the requested building. Each moved row keeps
`original_attribution = unattributed_on_lot`, `attribution_basis = hpd_building_id`, and
`mapped_bin`.

**Other addresses:**

- 1217 West Farms: not applied (`not_a_building_level_join`); all 12 violations and 51
  problems stay `lot_level`.
- Empire State: violations have no records. The one unattributed complaint problem stays
  unattributed (`row_lot_not_confirmed`: its borough contradicts its BBL).
- 1134 West Farms and 1845 ACP: applied, nothing to move (0 unattributed rows), and 0
  disagreements between a row's BIN and its BuildingID.

**Conditions enforced, each with a test:**
- building scope only;
- the HPD building fetch must be complete and for the same resolution;
- the row's lot must be confirmed, with no lot conflict and no conflicting duplicate;
- the row's BIN must be missing or a placeholder (a malformed BIN counts as contradictory);
- the BuildingID must map to exactly one HPD record, and that record must be on the same
  lot, current, not one of several current records sharing a BIN, and have an assigned BIN.

A row whose own BIN already decided its bucket is never changed. Disagreements between
that BIN and the BuildingID are counted only. Model validators reject a BuildingID basis
on any row that was not unattributed, or at non-building scope.

## 3. Source-data quality (measured)

**Buildings (kj4p-ruqc):** 380,119 rows; `buildingid` is unique.
- BIN: 11,989 null and 5,589 placeholder. 368,130 non-null BIN values cover only 356,862
  distinct BINs, so a BIN can have more than one HPD record.
- Lifecycle and record status include 10,603 Demolished/Inactive and 12,145
  Building/Inactive records.
- `registrationid` is 0 on 179,601 rows.
- The dataset has no `bbl` field, so the query is by `boroid`, `block` and `lot`.

**Registrations (tesw-yqqr):** 203,887 rows, 194,218 registration IDs. There is no
unique business key: (registrationid, buildingid) pairs repeat with different content
(renewal periods), so paging uses the Socrata `:id`. One registration can cover several
buildings, so the join is by building ID, never by address.

**Contacts (feu5-w2e2):** `registrationcontactid` is not unique (759,625 distinct of
810,494, repeats mostly on registration 0), so paging also uses `:id`.

**Freshness, all three datasets:** data last updated (`rowsUpdatedAt`) 2026-08-12;
metadata last updated (`viewLastModified`) 2026-10-01/02; stated frequency Monthly. On
2026-10-22 that is 71 days, beyond the 45-day threshold, so the results are marked
stale. The newest `lastregistrationdate` in the dataset is 2026-07-31.

## 4. Privacy (D11)

- Public: corporate names, the role HPD has on file (with a note that a role is not
  proof of title or of who manages the building), and HPD's registration description.
- Withheld from records, fixtures and logs: person names (first, middle, last, title);
  business house number, street and apartment/suite for every contact; and business
  city, state and zip for any contact that names a person.
- Fixtures store the SHA-256 of each original page body and the redaction events, not
  the original values. Synthetic test persons use invented names.
- `test_fixture_privacy.py` scans the contacts cassette, and the model validator refuses
  any address detail on a contact that names a person.

## 5. Files

**New:**
- Adapters: `records/hpd_buildings.py`, `records/hpd_registrations.py`,
  `records/hpd_contacts.py`, `records/building_id_attribution.py`.
- Models: `records/admin_models.py`.
- Freshness: `sources/socrata_metadata.py`.
- Recorder: `scripts/record_hpd_admin_fixtures.py`.
- Cassettes: three, each with its meta file.
- Tests: eight new unit test files.

**Modified:**
- `records/models.py`: evidence kind, quality flags, DataFreshness, AttributionBasis,
  BuildingIdAttributionReport, basis invariants.
- `records/complaint_models.py`: basis fields and report.
- `records/redaction.py`: contact rules.
- `sources/socrata_pager.py`: optional `$select` for `:id` paging.
- Test files: `tests/hpd_helpers.py`, `tests/live/test_live_hpd_admin.py`,
  `test_fixture_privacy.py`, `test_socrata_pager.py`.

Phase 1 resolution rules and benchmark labels were not touched.

## 6. Test results (2026-10-22)

- Offline suite: 474 passed, 22 skipped (the skipped tests are the live tests).
- Live tests (`RUN_LIVE_TESTS=1`): 22 passed.
- Benchmark replay: unchanged, with these results:
  - 14/14 correct auto-matches, 0 wrong building;
  - 6/6 ambiguity handled; 4/4 correct abstentions; 2/2 unresolved BINs preserved.
- Lint and format: clean.
- Privacy checks: the fixture privacy tests passed, including the contacts cassette.
- CI (GitHub Actions):
  - step 1: green;
  - step 2 (run 39703886361): failed, on the format check only, in the recorder script;
    fixed in step 3;
  - step 3 (run 39704710729): green;
  - step 4 (run 39705229105): green.

New tests by area:

| Area | Synthetic | Replay | Live |
|---|---|---|---|
| Buildings | 29 | 6 | 3 |
| Registrations | 28 | 5 | 1 |
| Contacts | 30 | 6 | 1 |
| BuildingID (D9) | 23 | 5 | 1 |

There is also 1 new pager test.

## 7. Uncertainties

- **Registration status:** every registration on the test lots ends after the snapshot
  date, so the current status is unknown until HPD publishes newer data.
- **Historical BuildingID mapping:** the mapping uses HPD's current building records. An
  old violation's BuildingID is mapped through today's record, and requiring a current
  record limits the risk but cannot prove the historical link.
- **Role labels:** `ContactRole` maps HPD's raw `type` values; the raw value is always
  kept. HPD does not define "RECEIVERSHIP" in the dataset dictionary.
- **Repeated `buildingid`:** a repeated `buildingid` across pages marks the buildings
  fetch incomplete, so D9 is skipped entirely. This is conservative by design.
- **HPD Online:** no Task 3 check against HPD Online has been done, and the Task 1 and
  Task 2 checklists are still pending your results.

## 8. Interview concepts

- **Secondary keys with provenance.** A weaker identifier is only used when stronger
  ones are absent, under explicit conditions. The original value and the basis travel
  with the row, so every upgrade is auditable and reversible.
- **Freshness versus metadata edits.** A dataset whose metadata was edited this month
  can still hold data from August. Status claims are bounded by the data date.
- **Absence of evidence.** "No registration on file" is a statement about one snapshot,
  not a finding of noncompliance.
- **Paging without a business key.** When no business column is unique, ordering by the
  platform row ID (`:id`) gives deterministic, gap-free paging.
- **Privacy by construction.** Redaction happens at the record boundary and in fixtures.
  Validators refuse unsafe combinations instead of relying on every caller.
- **Fail-closed composition.** Each source keeps its own status and provenance. A partial
  contacts fetch is marked incomplete without discarding verified building records.
