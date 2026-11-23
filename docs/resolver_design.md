# Address resolver: design v3

**Status:** v3 is the implemented design. v2 added the six requirements from 2026-10-21 06:30; v3 adds the six corrections from 06:40:

1. **Lot vs building.** A `verified` result now says whether it is verified at the **tax-lot** level or the **building** level. Only building level permits BIN-based joins or building-specific findings.
2. **`source_unavailable`** is a fourth outcome in the typed model.
3. **No merging of unresolved candidates.** Candidates are merged only when they share a BBL **and** a real (assigned) BIN. Unresolved-BIN records stay separate, so competing candidates produce `ambiguous`.
4. **`pad_only` is weaker evidence.** A BIN that Footprints cannot confirm is reported as single-source and does **not** reach building level.
5. **Borough inference checks the source's own borough field** (and the BBL's borough digit), not the text we added to the query. If any per-borough check fails, no unique match is claimed.
6. **24-address benchmark**; the original 20 remain as regression cases.

## 1. Three outcomes

| Outcome | When | What the user sees |
|---|---|---|
| `verified` | Exactly one candidate passes every check, including a confirmed borough. Carries a `resolution_level`: `building` (BIN cross-verified) or `tax_lot` (BBL verified, BIN unresolved or single-source) | The building or lot, why it was accepted, and a confirm button. At tax-lot level the UI says the building number is not confirmed |
| `ambiguous` | More than one building fits, or the only fit needed a correction (typo, missing hyphen, wrong borough), or official sources disagree | The options, what is uncertain about each, and a request to choose |
| `no_reliable_match` | Nothing passes, the house number is missing, or the input is outside NYC | Why it was rejected and what to add; no report is generated |

| `source_unavailable` | GeoSearch failed (timeout, 5xx, 429 after retries, bad response), or a required borough check failed with no other evidence | "We couldn't reach the City's address service; try again." Never reported as "no match" |

GeoSearch's `confidence` and `match_type` are recorded for audit but **never used in decisions**. Measured on 2026-10-21, "99999 Broadway" returned real Broadway buildings with the same 0.8 confidence as a correct match.

## 2. Pipeline

### Step 1: parse the input (pure code, no network)

- **House number.** Keeps Queens hyphens and letter suffixes like `1A`. The house number is never fuzzy-matched.
- **Street.** Normalized deterministically:
  - case and whitespace
  - suffixes (`ST` to `STREET`)
  - directions (`E` to `EAST`)
  - ordinals (`2nd`, `Second` and `2` all become `2`)
- **Borough, if stated.** Recognized names: Manhattan / New York / NY, Bronx, Brooklyn / Kings, Queens, Staten Island / Richmond.
- **ZIP**, if present.
- If there is no house number, the result is `no_reliable_match` before any API call.

### Step 2: fetch candidates

GeoSearch `/v2/search` through the existing bounded client.

### Step 3: collapse aliases

Candidates are merged only when they share a BBL **and** the same assigned BIN. All spellings are kept as aliases, so "100 B'WAY" and "100 BROADWAY" count once. Candidates whose BIN is a placeholder or missing are **never** merged on BBL alone: each keeps its own address identity (BBL plus house number plus street). If two of them, or one of them and a real-BIN candidate, survive the checks, the result is `ambiguous`.

### Step 3b: follow-up query for a stated borough (added during implementation)

If the user named a borough and the main query returned no exact address in that borough, the resolver sends one more query with only house number, street and borough. This was added because GeoSearch weights a ZIP heavily: "530 2nd Street, Brooklyn, NY 10001" (wrong ZIP) returned ten Manhattan results and no Brooklyn ones, which would have produced a false "no match". If the follow-up fails, the result is `source_unavailable`.

### Step 4: check each candidate independently

| Check | Values |
|---|---|
| House number | `exact` · `hyphen_only` (3343 vs 33-43) · `mismatch` |
| Street | `exact` · `alias` (another PAD spelling of the same building) · `near` (edit distance ≤ 2 on the street name only) · `mismatch` |
| Borough | `stated_match` · `inferred` (see step 5) · `stated_conflict` · `unknown` |
| BBL | `valid` · `invalid` |
| BIN | `assigned` · `placeholder` · `missing` (Footprints evidence is added in step 6) |

### Step 5: borough, confirmed or inferred (requirement 1)

- **If the user stated a borough,** a candidate's borough must equal it.
- **If the user did not state one,** the resolver may infer it, but only from authoritative data. It sends one GeoSearch query per borough, five in total, each text plus borough name, and checks for a candidate with an `exact` house number, an `exact` or `alias` street, and that borough.
  - If exactly one borough has such a match, the borough is `inferred` and counts as confirmed. The explanation says so: "No borough given; this address exists only in Brooklyn among PAD records returned for all five boroughs."
  - If two or more boroughs match, the result is `ambiguous`.
  - If none match, the result is `no_reliable_match`.
  - A match only counts when the **candidate's own `borough` field** equals the borough being checked **and** the BBL's first digit agrees. The borough name we added to the query text is never trusted.
  - If any of the five checks fails (timeout, error), no unique borough is claimed. With other matches the result is `ambiguous` (reason `BOROUGH_CHECK_INCOMPLETE`); with none it is `source_unavailable`, because absence was not proven.
- A ZIP is a supporting hint only (some ZIPs cross boroughs). If a ZIP was given and it differs from the candidate's PAD postal code, the result is downgraded to `ambiguous`.

Feasibility was checked live on 2026-10-21 with a throwaway script:

| Input (no borough) | Boroughs with an exact match | Result |
|---|---|---|
| 100 Broadway | Manhattan, Brooklyn | ambiguous |
| 1 Center St | Manhattan, Bronx | ambiguous |
| 530 2nd Street | Brooklyn | inferred |
| 1134 West Farms Rd | Bronx | inferred |
| 1279 First Avenue | Manhattan | inferred |
| 33-43 14th Street | Queens | inferred |
| 99999 Broadway | none | no reliable match |

*Residual risk:* inference depends on GeoSearch returning an address that exists when the borough is named. If PAD is missing an address in one borough, the resolver could infer the wrong borough. That is why the inferred borough is shown to the user, and the benchmark will include inferred-borough rows.

### Step 6: cross-check identifiers with Building Footprints (requirement 3)

For the selected candidate, one Socrata request asks Footprints which lot the PAD BIN stands on, and which BINs stand on the PAD lot.

| Situation | `bin_evidence` | Effect on the outcome |
|---|---|---|
| BIN is on the same lot (base or billing lot, which covers condos like 55 Austin Pl) | `cross_verified` | `verified` at `building` level |
| PAD BIN is a placeholder (`n000000`) | `placeholder` | `verified` at `tax_lot` level; BIN stored as `null` |
| PAD gave no BIN | `missing` | same as above |
| Footprints puts the BIN on a different lot (as with 1A Centre St) | `lot_conflict` | downgraded to `ambiguous`; BIN stored as `null`; both lots are shown with the source of each |
| Footprints has no record of the BIN | `pad_only` (single source, **not** cross-verified) | `verified` at `tax_lot` level only; BIN shown as "PAD-only, not cross-verified" |
| Footprints request failed | `not_checked` | `verified` at `tax_lot` level; reason `FOOTPRINTS_UNAVAILABLE` |

`resolution_level = building` **only** when the BIN is `cross_verified`. Downstream code reads `permitted_join_keys`: `["bbl", "bin"]` at building level, `["bbl"]` at tax-lot level. At tax-lot level any BBL-keyed record describes **the whole lot** (which may hold several buildings) and must be labelled that way.

Two rules hold throughout:

- **Never invent, never overwrite.** The resolver never replaces PAD's BIN with a Footprints BIN, and never fills a missing BIN from Footprints, even when the lot has exactly one footprint. That candidate BIN may appear in the explanation as context ("Footprints shows one building on this lot, BIN 2128717; not confirmed for this address"), but it is not stored as the building's identifier.
- **No joins on placeholders.** Building-level sources keyed by BIN are then reported as "unavailable: building number unresolved".

### Step 7: decide

1. **Strict match** means all of these hold:
   - house number `exact`
   - street `exact` or `alias`
   - borough `stated_match` or `inferred`
   - BBL `valid`
   - BIN evidence not `lot_conflict`
2. Exactly 1 distinct building with a strict match gives `verified`.
3. 2 or more give `ambiguous`.
4. 0 strict but at least one **weak** match gives `ambiguous`. A weak match is one of:
   - house number `hyphen_only`
   - street `near` (typo, requirement 2)
   - borough `stated_conflict`
   - BIN evidence `lot_conflict`

   Weak matches are never auto-selected.
5. Otherwise the result is `no_reliable_match`.

## 3. Explanations (requirement 6)

Every result carries:

- **`explanation`**: one plain-English sentence for the UI. For example: "Rejected: no building numbered 99999 on BROADWAY in Manhattan; GeoSearch's nearest results (1, 5, 23, 98 Broadway) have different house numbers."
- **`reasons`**: machine-readable codes such as `HOUSE_NUMBER_MISMATCH`, `BOROUGH_INFERRED`, `STREET_TYPO`, `BIN_PLACEHOLDER` and `BIN_LOT_CONFLICT`.
- **`checks`**: the full per-candidate check table from step 4, for audit and the evidence panel.
- **`provenance`**: GeoSearch URLs, PAD version, Footprints URL, and the fetch time.

## 4. Output model (sketch)

```
ResolutionResult
  outcome: verified | ambiguous | no_reliable_match | source_unavailable
  resolution_level: building | tax_lot | null          # verified only
  bin_evidence: cross_verified | pad_only | placeholder | missing | lot_conflict | not_checked
  permitted_join_keys: ["bbl","bin"] | ["bbl"] | []
  explanation: str
  reasons: [ReasonCode]
  input: {raw, house_number, street, borough, borough_source: stated|inferred|unknown, zip}
  building: {bbl, bin | null, bin_status, label, aliases, borough}   # verified only
  options: [{building..., checks..., why_uncertain}]                  # ambiguous only
  checks: [per-candidate check table]
  provenance: {...}
```

## 5. Files (when approved)

- `backend/src/nyc_apartment_agent/resolver/`:
  - `normalize.py`: parsing and normalization
  - `models.py`: the result model
  - `checks.py`: per-candidate checks
  - `resolve.py`: the pipeline and decision rules
- `backend/src/nyc_apartment_agent/sources/footprints.py`: the Footprints adapter
- Tests: `backend/tests/unit/test_normalize.py`, `test_checks.py`, `test_resolver.py`, all run offline on saved real responses
- `backend/scripts/evaluate_resolver.py`: runs the benchmark and prints the metrics in section 7
- Saved GeoSearch and Footprints responses for every benchmark input

## 6. Regression tests

- **False match:** "99999 Broadway, New York" gives `no_reliable_match` with `HOUSE_NUMBER_MISMATCH`, even though GeoSearch returned 5 candidates.
- **Unknown BINs:**
  - "1217 West Farms Rd, Bronx" gives `verified` for lot 2029930031, with `resolution_level: tax_lot`, `bin: null` and `bin_evidence: placeholder`. 2128717 is never stored as its BIN.
  - At 873 E 228 St, the GARAGE placeholder record is never chosen over BIN 2130638.
  - 1A Centre St gives `ambiguous` with `lot_conflict`.
  - A test asserts that no BIN-keyed query is ever built from a placeholder or unresolved BIN.
- **Ambiguous:**
  - 100 Broadway and 1 Center St (two boroughs)
  - 3343 14th St, Queens (hyphen)
  - 530 2nd Stret, Brooklyn (typo)
  - 1134 West Farms Rd, Brooklyn (borough conflict)
- **No reliable match:** "Broadway, Manhattan" (no number), a Washington DC address, nonsense text, empty input, oversized input.
- **Valid:** N01 to N06, E12, E15 and E20 give `verified` with the approved BBL/BIN. Alias spellings collapse to one building.
- **Borough inference:** "530 2nd Street" (no borough) gives `verified` with `borough_source: inferred`. A ZIP that conflicts with the candidate gives `ambiguous`.
- **Failures:** a GeoSearch timeout or 5xx gives `source_unavailable`, never `no_reliable_match`. A Footprints failure gives at most `verified` at `tax_lot` level (`not_checked`), never building level.

## 7. Benchmark reporting (requirement 5)

Each metric is reported separately with counts, not one blended accuracy number.

| Metric | Definition | Goal |
|---|---|---|
| **Incorrect automatic matches** | `verified` on the wrong BBL or BIN, **or** `verified` on a row whose expected outcome is `ambiguous` or `no_reliable_match` | **0** |
| Correct automatic matches | `verified` with the correct BBL (and BIN where labelled) ÷ rows expected `verified` | Report as-is |
| Ambiguity handled | `ambiguous` with the correct building among the options ÷ rows expected `ambiguous` | Report as-is |
| Correct abstentions | `no_reliable_match` ÷ rows expected `no_reliable_match` | Report as-is |
| Over-cautious | Expected `verified` but returned `ambiguous` | Report as-is (safe, but costs the user a click) |
| Unresolved BIN handling | Rows with placeholder, missing or conflicting BIN where the BIN stayed `null` and `resolution_level` was not `building` | Must equal all such rows |
| Source unavailable | Rows that ended in `source_unavailable` | Reported; excluded from the other rates, never counted as abstentions |

**Caveat, stated wherever results appear:** 24 hand-picked addresses are a small acceptance test. Zero incorrect auto-matches here does not prove citywide accuracy. A random sample (for example, drawn from PLUTO) would be the next step for a stronger claim.

## 8. Implementation findings and policy changes

- **Stated-borough follow-up query** (step 3b), added after a wrong ZIP caused a false "no match".
- **Queens hyphenation policy (2026-10-21, approved by Sanyam).** In v1, A09 "3343 14th St, Queens" was verified because PAD lists "3343 14 STREET" as an alternate form of 33-43 14 Street. The policy now works like this:
  - When a candidate is in Queens, the typed number has no hyphen and at least 3 digits, and house number and street match exactly, the resolver checks whether PAD has the official hyphenated form (`<all but last two digits>-<last two digits>`, e.g. 7217 becomes 72-17) **on the same lot and street**. It uses a hyphenated alias already among the results, or one extra GeoSearch query.
  - If the hyphenated form exists, the typed number is an alternate form, and the result is `ambiguous` with reason `QUEENS_NUMBER_NEEDS_HYPHEN`. The explanation names the official number.
  - If it doesn't exist, the number is genuine and the normal rules apply. For example, "311 Beach 32nd Street" and "250 Beach 15th Street" in Rockaway are still verified.
  - If the check fails, the result is `ambiguous` with `QUEENS_NUMBER_CHECK_INCOMPLETE`. It never verifies on an unchecked number.
  - Benchmark labels were not changed. The v1 results are preserved in `data/eval/benchmark_results_v1_2026-10-21.md`.
  - *Limit:* only a two-digit right part is considered (the Queens grid convention).

## 9. HTTP API (Phase 1)

`POST /resolve` takes `{"address": "<1-200 chars>"}`. Unknown fields are rejected. It returns the `ResolutionResult` model unchanged: outcome, explanation, reasons, candidates, provenance (including a `request_id`), `resolution_level`, `permitted_join_keys` and `building_specific_findings_allowed`.

| Status | Meaning |
|---|---|
| 200 | `verified`, `ambiguous`, `no_reliable_match` |
| 503 | `source_unavailable`, with `Retry-After: 30` |
| 422 | Invalid input (values not echoed) |
| 500 | Generic error, details only in server logs |

## 10. Costs and trade-offs

- **Requests per lookup:** 1 GeoSearch call plus 1 Footprints call. Add 5 GeoSearch calls only when no borough is given. GeoSearch answered in roughly 0.05 to 0.5 s per call in testing.
- **Strictness:** typos, missing hyphens and borough conflicts always ask the user. That adds clicks but keeps incorrect auto-matches at zero.
- **Edit distance:** limited to 2, on the street name only. A typo bigger than that gives `no_reliable_match` with a suggestion to check the spelling.
- **No Geoclient API:** it needs a key and is not in the roadmap.
