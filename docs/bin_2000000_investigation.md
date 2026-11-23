# Is BIN 2000000 a placeholder?

**Status:** investigated 2026-10-21. **Conclusion:** yes, with strong evidence. A BIN made of a
borough digit followed by six zeros (`1000000`, `2000000`, `3000000`, `4000000`, `5000000`)
means the building number is **unassigned or unknown** for that record. It does **not** mean
the address is invalid or that no building exists. We flag it and never join on it; we do
not reject the address.

## Evidence

### 1. Official data dictionary (primary source)

The NYC Open Data **Building Footprints** dataset (`5zhs-2jue`, published by the City),
column `bin`, description retrieved live from
`https://data.cityofnewyork.us/api/views/5zhs-2jue.json` on 2026-10-21:

> Building Identification Number. A number assigned by City Planning and used by Dept. of
> Buildings to reference information pertaining to an individual building. The first digit is
> a borough code (1 = Manhattan, 2 = The Bronx, 3 = Brooklyn, 4 = Queens, 5 = Staten Island).
> The remaining 6 digits are unique for buildings within that borough. In some cases where
> these 6 digits are all zeros (e.g. 1000000, 2000000, etc.) the BIN is unassigned or unknown.

### 2. It is shared across hundreds of unrelated lots (live query)

If `2000000` identified a building, it would sit on one tax lot. Counting HPD violations
(`wvxf-dwi5`) on 2026-10-21:

| BIN | violation rows | distinct BBLs |
|---|---|---|
| 1000000 | 7,435 | 237 |
| 2000000 | 7,848 | 438 |
| 3000000 | 12,144 | 832 |
| 4000000 | 4,708 | 416 |
| 5000000 | 829 | 109 |

Joining records on `bin = 2000000` would merge 438 different properties: a cross-building
contamination bug of exactly the kind Phase 1 acceptance forbids.

### 3. Real examples from GeoSearch (fixtures saved in `backend/tests/fixtures/geosearch/`)

- **873 East 228 Street, Bronx** (lot `2048530011`): GeoSearch returns two PAD records on the
  same lot. The house has BIN `2130638`; a separate "873 GARAGE EAST 228 STREET" record has
  `2000000`. Building Footprints lists only `2130638` on that lot.
- **1217 West Farms Road, Bronx** (lot `2029930031`): every West Farms Road address on this lot
  returns BIN `2000000`, yet Building Footprints shows one building, BIN `2128717`, and HPD
  lists that BIN at **1017 Home Street**. A building clearly exists; PAD just does not tie
  these West Farms Road address points to a BIN. Calling this address "invalid" would be wrong.

### 4. Secondary sources (not relied on alone)

- A web search surfaced the OpenStreetMap wiki page for `nycdoitt:bin`, which describes the same
  values as borough placeholders, and an NYC MODA analysis notebook that drops
  `[1000000, 2000000, 3000000, 4000000, 5000000]` as dummy BINs. Both are summaries from the
  search tool; the pages themselves could not be opened from this environment.

### Not yet read

DCP's Geosupport User Programming Guide and PAD user guide (on `nyc.gov`) are the most
authoritative descriptions, but `nyc.gov` is blocked by this environment's network policy.
Worth reading once reachable; the conclusion above does not depend on them.

## What the code does

`identifiers.classify_bin()` returns `BinStatus.UNASSIGNED_PLACEHOLDER` for these values.
The GeoSearch adapter keeps the candidate, sets `bin_status`, and adds a warning
("do not join on it"). The resolver (next task) should then:

1. Still resolve the **BBL**, which is valid.
2. Never query BIN-keyed sources (e.g. DOB complaints) with a placeholder BIN.
3. Optionally propose a BIN from Building Footprints when the lot has exactly one building,
   labelled as inferred and requiring confirmation, never as verified.
