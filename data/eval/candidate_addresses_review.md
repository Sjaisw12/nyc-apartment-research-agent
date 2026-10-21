# Candidate test addresses: DRAFT for review

**Status: not final.** Nothing here is a ground-truth label until Sanyam reviews and approves it.
Machine-readable copy: [`candidate_addresses_draft.csv`](candidate_addresses_draft.csv).
Raw evidence (every API answer used below): [`candidate_addresses_evidence.json`](candidate_addresses_evidence.json),
collected 2026-10-21 by `backend/scripts/collect_address_evidence.py`.

## How labels were proposed

A BBL/BIN is proposed only when separately maintained city datasets agree:

- **PAD** via GeoSearch (address → BBL/BIN),
- **PLUTO** (DCP tax-lot table; address → BBL, and number of buildings on the lot),
- **Building Footprints** (OTI; which BINs physically stand on the lot, and which lot a BIN is on),
- **HPD Buildings** (HPD's own building register; address or BIN → BIN and block/lot).

These share city address standards, so they are not fully independent, but they are kept by
different agencies and disagree in real cases (see E13, E14, E15). Anyone can re-check a label
with the queries in the evidence file, or on ZoLa / DOB BIS.

Expected behaviour codes: `select` = pick this building; `clarify` = show options and ask;
`abstain` = say no NYC building was found; `select_bbl_flag_bin` = the lot is known but the
building number is not.

## The 20 candidates

All 5 boroughs covered. Single-borough rows: Manhattan 5, Bronx 4, Brooklyn 3, Queens 2, Staten Island 2. A07 and A08 span two boroughs; E18 and E19 are not NYC addresses.

| ID | Input | Type | Expected | Proposed BBL | Proposed BIN | Evidence |
|---|---|---|---|---|---|---|
| N01 | 1134 West Farms Rd, Bronx, NY 10459 | normal | select | 2027540005 | 2006185 | PAD, PLUTO, Footprints, HPD agree. Lot holds a 2nd BIN (2117584). |
| N02 | 350 5th Avenue, New York, NY 10118 | normal | select | 1008350041 | 1015862 | All agree; PLUTO and HPD list the lot as **338** 5 Avenue. |
| N03 | 530 2nd Street, Brooklyn, NY 11215 | normal | select | 3010780016 | 3025292 | All agree; one building on lot. |
| N04 | 33-43 14th Street, Astoria, NY 11106 | normal | select | 4005280050 | 4430813 | All agree; lot holds **7** BINs. PAD says Long Island City, input says Astoria. |
| N05 | 100 Belmont Place, Staten Island, NY 10301 | normal | select | 5000200008 | 5112989 | PAD and HPD agree; BIN is on the lot in Footprints. PLUTO's lot address is 100 Daniel Low Terrace (2 buildings). |
| N06 | 1279 First Avenue, Manhattan | normal | select | 1014430030 | 1044761 | All agree. |
| A07 | 100 Broadway | ambiguous | clarify | 1000460003 or 3021310001 | 1001024 or 3324734 | No borough. Manhattan (office) and Brooklyn (residential) both confirmed by PLUTO and Footprints. |
| A08 | 1 Center St | ambiguous | clarify | 1001210001 or 2056270046 | 1001394 or 2103477 | Manhattan option confirmed by PLUTO/Footprints; Bronx option is PAD-only (not cross-checked). GeoSearch's **top result is a Queens pre-K**. |
| A09 | 3343 14th St, Queens | ambiguous | select or clarify | 4005280050 | 4430813 | Hyphen missing. GeoSearch maps it to N04's building. |
| A10 | Broadway, Manhattan | ambiguous | clarify | none | none | No house number; GeoSearch still returns specific buildings. |
| A11 | 1134 West Farms Rd, Brooklyn | ambiguous | clarify | (2027540005) | (2006185) | Wrong borough. GeoSearch returns the Bronx building first; we should ask, not silently switch boroughs. |
| E12 | 873 East 228th Street, Bronx | edge | select | 2048530011 | 2130638 | All agree. Same lot has a GARAGE record with BIN 2000000, and HPD keeps an **Inactive / Demolished** building BIN 2064005 here (older records may use it). |
| E13 | 1217 West Farms Road, Bronx | edge | select_bbl_flag_bin | 2029930031 | **unresolved** | PAD BIN is placeholder 2000000. Footprints has one building on the lot (2128717), which HPD lists as 1017 Home Street. Proposed: BBL yes, BIN 2128717 only as "inferred, needs review". |
| E14 | 1A Centre Street, Manhattan | edge | select or clarify | 1001210001 (PAD) | 1091024 (PAD) | **Sources disagree:** Footprints places BIN 1091024 on lot 1001210998, not 1001210001; HPD has no record. Needs your call. |
| E15 | 55 Austin Place, Staten Island, NY 10304 | edge | select | 5005797501 | 5112220 | Condo billing lot (PLUTO condo #45). Footprints base lot is 5005790002. PAD, PLUTO, HPD agree on the billing lot. |
| E16 | 530 2nd Stret, Brooklyn | edge | select or clarify | 3010780016 | 3025292 | Typo; GeoSearch top result is N03 (exact, 1.0), but also offers 530 East 2 St and 530 2 Ave. |
| E17 | 99999 Broadway, New York, NY | edge | abstain | none | none | GeoSearch returns 23/1/5/98 Broadway at the **same confidence (0.8)** as real matches. Must not select. |
| E18 | 1600 Pennsylvania Avenue NW, Washington, DC 20500 | edge | abstain | none | none | Outside NYC; GeoSearch returned 0 results. |
| E19 | zzqx qqzz nowhere | edge | abstain | none | none | 0 results. |
| E20 | 1310 Pennsylvania Avenue, Brooklyn, NY 11239 | edge | select | 3044520001 | 3342903 | PAD, Footprints, HPD agree. Lot holds **8** BINs (Spring Creek complex); PLUTO lot address is 1260 Croton Loop. |

## Questions for review

1. **E14**: accept PAD's lot for 1A Centre Street, or treat the disagreement as "clarify"?
2. **E13**: is "BBL known, BIN inferred" an acceptable outcome, or should it abstain on BIN?
3. **A11**: with a wrong borough, should the system offer the Bronx building or abstain?
4. **N02 / A07 Manhattan / A08 Manhattan** are not residential rentals. Keep them (they test
   resolution) or swap for rentals?
5. Queens and Staten Island have 2 rows each. Add more before finalising?
