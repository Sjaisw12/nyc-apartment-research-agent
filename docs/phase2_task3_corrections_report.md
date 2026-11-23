# Phase 2, Task 3: corrections before closure

Status: the three corrections requested on 2026-10-22 are done, in commit `052a6c7` on
`claude/project-thread-5d0l0f`. This asks for final Task 3 closure. Nothing has been
merged or deployed, and PostgreSQL implementation has not started. The HPD Online
validations for Task 1 (Part A) and Task 2 (Part B) remain **PENDING**.

## 1. Incomplete upstream registrations

**Before:** if the registrations fetch was `incomplete_pagination` and returned no usable
registration ID, the contacts result was `no_records_found`. That is an absence claim the
data does not support.

**Now:** that case returns `incomplete_pagination`, with:
- `partial = true`;
- no source count and no summary;
- zero contact requests;
- the reason "registration_list_incomplete: no registration ID was retrieved, so contacts
  could not be looked up; this is not evidence that none exist".

`no_records_found` now only follows a complete registrations fetch with nothing on file.

Regression test `test_registration_paging_failing_before_any_row_is_not_an_absence_of_contacts`:
the registrations count succeeds, then the first page fails twice.

## 2. Shared registration contacts

Contacts belong to a registration, and a registration can cover more than one HPD
building. Measured live on 2026-10-22: 80 registration IDs (not counting ID 0) cover
more than one building, and 55 of them cover buildings on more than one lot. One
covers 87 buildings. Because a lot query cannot see the other lots, the contacts step
now also queries the registrations dataset (tesw-yqqr) by registration ID, in the same
batches, to list every covered building.

Each registration in the contacts summary now carries:

- `building_ids_on_lot`: the HPD building IDs on this lot.
- `covered_building_ids`: every HPD building ID with that registration ID. It is `None`
  when the coverage lookup did not complete.
- `covered_buildings_off_lot`.
- `shared_registration`: true, false, or `None` when unknown.
- `association`, one of:
  - `requested_building_only`;
  - `shared_including_requested_building`;
  - `requested_building_sharing_unknown`;
  - `other_buildings_only`;
  - `lot_level`;
  - `unattributed`.
- A plain-language note. For example, for a shared registration: "Its contacts are
  registered for all of them, not specifically for the requested building."

Each contact record carries `association` and `registration_shared` too.

Two rules are enforced by the model validator:
- `requested_building_only` requires a completed coverage check showing exactly one
  building;
- "shared" must agree with the coverage.

If the coverage lookup fails, the contacts stay verified and the association becomes
`requested_building_sharing_unknown`. The contacts status is not downgraded, because the
contacts fetch itself was complete.

New tests:
- shared with another building on the lot;
- shared with a building on another lot;
- unshared after the check;
- coverage lookup failing;
- the validator refusing `requested_building_only` without complete coverage;
- live: registration 10391 covers several buildings.

## 3. Source row identities and deduplication

**Before:**
- Contacts: identical rows under different Socrata `:id`s were silently dropped.
- Registrations: they were flagged, but only one `:id` was kept.
- The status text equated normalized counts with source counts.

**Now, for both registrations and contacts:**
- Identical copies still collapse into one normalized record. The record's `source_rows`
  lists every original row (`:id` and its own keyed digest), and every row is written
  to the evidence store.
- Summaries report `source_rows`, the normalized count (`total_rows` / `total_contacts`)
  and `identical_rows_collapsed`.
- Status detail reads, for example, "3 source rows match the source count; 2 normalized
  records (1 identical copies collapsed, each row still listed)".
- On a complete fetch, the validator requires the listed source rows to be unique and to
  equal the source count, so no row can be dropped without failing validation.

Synthetic regression tests cover both adapters, including the validator refusing a
record set that drops a row.

## Recorded results: still valid

- Contacts cassette re-recorded: 61 responses, up from 51 because of the coverage
  lookups. Same data date (12 Aug 2026).
- Contacts per address: West Farms 5, 220 W 17th 32, 1217 West Farms 4, Empire State
  none, ACP 10.
- None of the recorded registrations is shared. Coverage is complete for all of them.
  The test building, 220 W 17th (123116) and ACP (113010) are each
  `requested_building_only`.
- Registrations: source rows equal normalized records at every address (1, 8, 1, 0, 2),
  with 0 collapsed. Contacts: 0 collapsed.
- BuildingID before/after counts are unchanged at every address.
- 220 W 17th: 87 violation rows and 84 complaint problems move to
  `other_building_on_lot`; 0 move to the requested building.
- 1217 West Farms stays `lot_level`.

## Test results (2026-10-22)

- Offline suite: 483 passed, 23 skipped (the skipped tests are the live tests).
- Live tests: 23 passed.
- Lint and format: clean.
- Phase 1 benchmark replay: unchanged.
  - 14/14 correct auto-matches, 0 wrong building;
  - 6/6 ambiguity handled; 4/4 correct abstentions; 2/2 unresolved BINs preserved.
- Fixture privacy tests: pass. The coverage rows hold only IDs and lot numbers.

## Remaining uncertainty

The coverage lookup lists building IDs from the registrations dataset as of its last data
update (12 Aug 2026). It shows which buildings HPD has under that registration ID, not
who manages them in practice.
