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

Expected outcomes use the resolver's three outcomes: `verified` (one building passed every
check), `ambiguous` (the user must choose or confirm), `no_reliable_match` (nothing passed).
A verified lot can still carry an **unresolved BIN**: the building number is kept empty, never guessed.

## The 20 candidates

All 5 boroughs covered. Single-borough rows: Manhattan 5, Bronx 4, Brooklyn 3, Queens 2, Staten Island 2. A07 and A08 span two boroughs; E18 and E19 are not NYC addresses.

| ID | Input | Type | Expected | Proposed BBL | Proposed BIN | Evidence |
|---|---|---|---|---|---|---|
| N01 | 1134 West Farms Rd, Bronx, NY 10459 | normal | verified | 2027540005 | 2006185 | PAD, PLUTO, Footprints, HPD agree. Lot holds a 2nd BIN (2117584). |
| N02 | 350 5th Avenue, New York, NY 10118 | normal | verified | 1008350041 | 1015862 | All agree; PLUTO and HPD list the lot as **338** 5 Avenue. |
| N03 | 530 2nd Street, Brooklyn, NY 11215 | normal | verified | 3010780016 | 3025292 | All agree; one building on lot. |
| N04 | 33-43 14th Street, Astoria, NY 11106 | normal | verified | 4005280050 | 4430813 | All agree; lot holds **7** BINs. PAD says Long Island City, input says Astoria. |
| N05 | 100 Belmont Place, Staten Island, NY 10301 | normal | verified | 5000200008 | 5112989 | PAD and HPD agree; BIN is on the lot in Footprints. PLUTO's lot address is 100 Daniel Low Terrace (2 buildings). |
| N06 | 1279 First Avenue, Manhattan | normal | verified | 1014430030 | 1044761 | All agree. |
| A07 | 100 Broadway | ambiguous | ambiguous | 1000460003 or 3021310001 | 1001024 or 3324734 | No borough. Manhattan (office) and Brooklyn (residential) both confirmed by PLUTO and Footprints. |
| A08 | 1 Center St | ambiguous | ambiguous | 1001210001 or 2056270046 | 1001394 or 2103477 | Manhattan option confirmed by PLUTO/Footprints; Bronx option is PAD-only (not cross-checked). GeoSearch's **top result is a Queens pre-K**. |
| A09 | 3343 14th St, Queens | ambiguous | ambiguous | 4005280050 | 4430813 | Hyphen missing. GeoSearch maps it to N04's building. |
| A10 | Broadway, Manhattan | ambiguous | no_reliable_match | none | none | No house number; GeoSearch still returns specific buildings. |
| A11 | 1134 West Farms Rd, Brooklyn | ambiguous | ambiguous | (2027540005) | (2006185) | Wrong borough. GeoSearch returns the Bronx building first; we should ask, not silently switch boroughs. |
| E12 | 873 East 228th Street, Bronx | edge | verified | 2048530011 | 2130638 | All agree. Same lot has a GARAGE record with BIN 2000000, and HPD keeps an **Inactive / Demolished** building BIN 2064005 here (older records may use it). |
| E13 | 1217 West Farms Road, Bronx | edge | verified (lot); BIN unresolved | 2029930031 | **unresolved** | PAD BIN is placeholder 2000000. Footprints has one building on the lot (2128717), which HPD lists as 1017 Home Street. Proposed: BBL yes, BIN 2128717 only as "inferred, needs review". |
| E14 | 1A Centre Street, Manhattan | edge | ambiguous; BIN unresolved | 1001210001 (PAD) | **unresolved** (PAD says 1091024) | **Sources disagree:** Footprints places BIN 1091024 on lot 1001210998, not 1001210001; HPD has no record. Needs your call. |
| E15 | 55 Austin Place, Staten Island, NY 10304 | edge | verified | 5005797501 | 5112220 | Condo billing lot (PLUTO condo #45). Footprints base lot is 5005790002. PAD, PLUTO, HPD agree on the billing lot. |
| E16 | 530 2nd Stret, Brooklyn | edge | ambiguous | 3010780016 | 3025292 | Typo; GeoSearch top result is N03 (exact, 1.0), but also offers 530 East 2 St and 530 2 Ave. |
| E17 | 99999 Broadway, New York, NY | edge | no_reliable_match | none | none | GeoSearch returns 23/1/5/98 Broadway at the **same confidence (0.8)** as real matches. Must not select. |
| E18 | 1600 Pennsylvania Avenue NW, Washington, DC 20500 | edge | no_reliable_match | none | none | Outside NYC; GeoSearch returned 0 results. |
| E19 | zzqx qqzz nowhere | edge | no_reliable_match | none | none | 0 results. |
| E20 | 1310 Pennsylvania Avenue, Brooklyn, NY 11239 | edge | verified | 3044520001 | 3342903 | PAD, Footprints, HPD agree. Lot holds **8** BINs (Spring Creek complex); PLUTO lot address is 1260 Croton Loop. |

## Supporting official records

Each link runs a live query against the City's own dataset, so you can check a label yourself.
ZoLa is NYC Planning's public map page for the lot. Rows without a proposed label are omitted.

**N01** 1134 West Farms Rd, Bronx, NY 10459
- Lot 2027540005: [ZoLa](https://zola.planning.nyc.gov/l/lot/2/2754/5) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=2027540005) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%272027540005%27+OR+mappluto_bbl%3D%272027540005%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=2&block=2754&lot=5)
- BIN 2006185: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=2006185) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=2006185) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=2006185)

**N02** 350 5th Avenue, New York, NY 10118
- Lot 1008350041: [ZoLa](https://zola.planning.nyc.gov/l/lot/1/835/41) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=1008350041) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%271008350041%27+OR+mappluto_bbl%3D%271008350041%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=1&block=835&lot=41)
- BIN 1015862: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=1015862) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=1015862) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=1015862)

**N03** 530 2nd Street, Brooklyn, NY 11215
- Lot 3010780016: [ZoLa](https://zola.planning.nyc.gov/l/lot/3/1078/16) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=3010780016) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%273010780016%27+OR+mappluto_bbl%3D%273010780016%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=3&block=1078&lot=16)
- BIN 3025292: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=3025292) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=3025292) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=3025292)

**N04** 33-43 14th Street, Astoria, NY 11106
- Lot 4005280050: [ZoLa](https://zola.planning.nyc.gov/l/lot/4/528/50) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=4005280050) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%274005280050%27+OR+mappluto_bbl%3D%274005280050%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=4&block=528&lot=50)
- BIN 4430813: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=4430813) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=4430813) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=4430813)

**N05** 100 Belmont Place, Staten Island, NY 10301
- Lot 5000200008: [ZoLa](https://zola.planning.nyc.gov/l/lot/5/20/8) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=5000200008) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%275000200008%27+OR+mappluto_bbl%3D%275000200008%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=5&block=20&lot=8)
- BIN 5112989: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=5112989) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=5112989) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=5112989)

**N06** 1279 First Avenue, Manhattan
- Lot 1014430030: [ZoLa](https://zola.planning.nyc.gov/l/lot/1/1443/30) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=1014430030) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%271014430030%27+OR+mappluto_bbl%3D%271014430030%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=1&block=1443&lot=30)
- BIN 1044761: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=1044761) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=1044761) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=1044761)

**A07** 100 Broadway
- Lot 1000460003: [ZoLa](https://zola.planning.nyc.gov/l/lot/1/46/3) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=1000460003) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%271000460003%27+OR+mappluto_bbl%3D%271000460003%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=1&block=46&lot=3)
- Lot 3021310001: [ZoLa](https://zola.planning.nyc.gov/l/lot/3/2131/1) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=3021310001) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%273021310001%27+OR+mappluto_bbl%3D%273021310001%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=3&block=2131&lot=1)
- BIN 1001024: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=1001024) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=1001024) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=1001024)
- BIN 3324734: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=3324734) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=3324734) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=3324734)

**A08** 1 Center St
- Lot 1001210001: [ZoLa](https://zola.planning.nyc.gov/l/lot/1/121/1) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=1001210001) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%271001210001%27+OR+mappluto_bbl%3D%271001210001%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=1&block=121&lot=1)
- Lot 2056270046: [ZoLa](https://zola.planning.nyc.gov/l/lot/2/5627/46) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=2056270046) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%272056270046%27+OR+mappluto_bbl%3D%272056270046%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=2&block=5627&lot=46)
- BIN 1001394: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=1001394) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=1001394) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=1001394)
- BIN 2103477: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=2103477) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=2103477) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=2103477)

**A09** 3343 14th St, Queens
- Lot 4005280050: [ZoLa](https://zola.planning.nyc.gov/l/lot/4/528/50) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=4005280050) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%274005280050%27+OR+mappluto_bbl%3D%274005280050%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=4&block=528&lot=50)
- BIN 4430813: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=4430813) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=4430813) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=4430813)

**A11** 1134 West Farms Rd, Brooklyn
- Lot 2027540005: [ZoLa](https://zola.planning.nyc.gov/l/lot/2/2754/5) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=2027540005) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%272027540005%27+OR+mappluto_bbl%3D%272027540005%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=2&block=2754&lot=5)
- BIN 2006185: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=2006185) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=2006185) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=2006185)

**E12** 873 East 228th Street, Bronx
- Lot 2048530011: [ZoLa](https://zola.planning.nyc.gov/l/lot/2/4853/11) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=2048530011) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%272048530011%27+OR+mappluto_bbl%3D%272048530011%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=2&block=4853&lot=11)
- BIN 2130638: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=2130638) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=2130638) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=2130638)

**E13** 1217 West Farms Road, Bronx
- Lot 2029930031: [ZoLa](https://zola.planning.nyc.gov/l/lot/2/2993/31) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=2029930031) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%272029930031%27+OR+mappluto_bbl%3D%272029930031%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=2&block=2993&lot=31)
- Inferred BIN 2128717 (not a label): [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl&bin=2128717) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin&bin=2128717)

**E14** 1A Centre Street, Manhattan
- Lot 1001210001: [ZoLa](https://zola.planning.nyc.gov/l/lot/1/121/1) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=1001210001) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%271001210001%27+OR+mappluto_bbl%3D%271001210001%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=1&block=121&lot=1)
- BIN 1091024: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=1091024) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=1091024) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=1091024)

**E15** 55 Austin Place, Staten Island, NY 10304
- Lot 5005797501: [ZoLa](https://zola.planning.nyc.gov/l/lot/5/579/7501) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=5005797501) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%275005797501%27+OR+mappluto_bbl%3D%275005797501%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=5&block=579&lot=7501)
- BIN 5112220: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=5112220) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=5112220) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=5112220)

**E16** 530 2nd Stret, Brooklyn
- Lot 3010780016: [ZoLa](https://zola.planning.nyc.gov/l/lot/3/1078/16) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=3010780016) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%273010780016%27+OR+mappluto_bbl%3D%273010780016%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=3&block=1078&lot=16)
- BIN 3025292: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=3025292) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=3025292) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=3025292)

**E20** 1310 Pennsylvania Avenue, Brooklyn, NY 11239
- Lot 3044520001: [ZoLa](https://zola.planning.nyc.gov/l/lot/3/4452/1) · [PLUTO](https://data.cityofnewyork.us/resource/64uk-42ks.json?bbl=3044520001) · [Footprints on lot](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&%24where=base_bbl%3D%273044520001%27+OR+mappluto_bbl%3D%273044520001%27) · [HPD buildings on lot](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cbin%2Crecordstatus&boroid=3&block=4452&lot=1)
- BIN 3342903: [Footprints](https://data.cityofnewyork.us/resource/5zhs-2jue.json?%24select=bin%2Cbase_bbl%2Cmappluto_bbl&bin=3342903) · [HPD buildings](https://data.cityofnewyork.us/resource/kj4p-ruqc.json?%24select=housenumber%2Cstreetname%2Cboroid%2Cblock%2Clot%2Crecordstatus&bin=3342903) · [DOB BIS](https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin=3342903)

## Questions for review (with my recommendation)

1. **E14, 1A Centre Street.** PAD puts BIN 1091024 on lot 1001210001; Building Footprints puts it on lot 1001210998.
   *Recommend:* `ambiguous` with BIN unresolved and both lots shown, because official sources disagree. This follows your rule that conflicts stay unresolved.
2. **E13, 1217 West Farms Road.** The lot is certain, but PAD gives placeholder BIN 2000000. The only footprint on the lot is 2128717 (HPD calls it 1017 Home Street).
   *Recommend:* `verified` for the lot with BIN **unresolved**. The UI says "building number unknown", and BIN-keyed sources are reported as unavailable. 2128717 is not stored as the building's BIN.
3. **A11, wrong borough** ("1134 West Farms Rd, Brooklyn").
   *Recommend:* `ambiguous`. Offer the Bronx building with the note "you wrote Brooklyn; this address exists in the Bronx". Never auto-select.
4. **Non-residential rows** (N02 Empire State Building, and the Manhattan options in A07 and A08).
   *Recommend:* keep them. They test resolution, which does not depend on use. Residential coverage comes from the other rows.
5. **Thin borough coverage.** Queens and Staten Island have 2 rows each.
   *Recommend:* add 2 Queens and 2 Staten Island rows before finalising (24 total), including one input with no borough that the resolver must infer. I'd draft them with the same evidence method once you agree.
