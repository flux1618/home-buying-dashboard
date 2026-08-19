# Spec — Analyze a Property

The first vertical slice. One input, one output, six stations. Address in, scored and fully-sourced verdict out.

Deliberately narrow: this is one complete path rather than several partial features. It touches every layer of the system, so building it proves the architecture works end to end.

**Mnemonic — GAFCBS:** Geocode, Assess, FEMA, Commute, Broadband, Score.

---

## Input

```json
{
  "address": "123 Example Rd, Boiling Springs, SC 29316",
  "price": 315000,
  "sqft": 1650,
  "beds": 3,
  "baths": 2,
  "garage_spaces": 2,
  "hoa_monthly": 0,
  "year_built": 2004,
  "roof_age_years": 8,
  "hvac_age_years": 6,
  "water_sewer": "public"
}
```

Only `address` is required. Everything else is optional; missing fields produce a caveat rather than a failure. A bare address still returns tax, hazard, commute, and broadband analysis.

---

## The six stations

Each station is an independent adapter in `sources/`. Each one can fail without failing the analysis.

### 1. Geocode

Address → coordinates + census block GEOID.

- **Primary:** [Census Geocoder](https://geocoding.geo.census.gov/) — free, no key, returns block GEOID directly, which station 5 needs.
- **Fallback:** [Nominatim](https://nominatim.openstreetmap.org/) — coordinates only, no GEOID. Degrades broadband to `unavailable`.
- **Failure:** no geocode means no analysis. This is the only station whose failure is fatal.
- **Confidence:** `measured` on an exact match; `estimated` on an interpolated or ZIP-centroid match, with the match type surfaced.

### 2. Assess — full cost of ownership

The most important station and the one most worth getting exactly right.

**Tax.** South Carolina assesses owner-occupied legal residences at **4%** and other property at **6%**, and the legal-residence classification also removes school operating millage ([SC DOR](https://dor.sc.gov/lgs/property-tax-basics)).

```
assessed_value = sale_price × ratio          # 0.04 owner-occupied, 0.06 otherwise
annual_tax     = assessed_value × (applicable_millage / 1000)
```

Both scenarios are always computed and both are returned. Two reasons:

- A listing currently taxed at 6% shows an inflated tax line that resets *downward* for an owner-occupier — and listing sites display the seller's bill, not the buyer's.
- Assessed value resets to sale price at purchase, so the current bill is not predictive either way.

Millage comes from the Spartanburg County GIS parcel lookup when available (`measured`), otherwise a typical owner-occupied rate (`estimated`). See [known limitations](../KNOWN_LIMITATIONS.md).

**Maintenance reserve.** Three rules of thumb, returned as three values, never averaged. All `estimated`.

| Method | Formula |
|---|---|
| Percent of price | `price × 0.01 / 12` per month |
| Per square foot | `sqft × 1.00 / 12` per month |
| Age-scaled | `price × rate / 12` where rate = 1% if age < 10, 1.5% if 10–30, 2% if > 30 |

The spread between the three is the honest answer. Collapsing them to one number would manufacture precision that doesn't exist.

**Monthly total.** Principal and interest, taxes, insurance, HOA, and the maintenance reserve range — plus front-end DTI against the 22% ceiling from the [customer brief](../CUSTOMER_BRIEF.md).

Insurance uses the SC statewide average ([LendingTree](https://www.lendingtree.com/insurance/state-of-home-insurance/)) until a real quote exists. Rate comes from [Freddie Mac PMMS](https://www.freddiemac.com/pmms).

### 3. FEMA — hazard

- **Flood zone** from the [NFHL](https://msc.fema.gov/portal/home). A Special Flood Hazard Area (A or AE) is a hard fail.
- **National Risk Index** for the census tract — broader hazard context. Tract-level, so it describes the neighborhood, not the lot.
- **Failure:** flood zone `unavailable`, hard fail cannot be evaluated, and a blocking verification task is emitted. The analysis does not silently pass a house whose flood status is unknown.

### 4. Commute — rush hour, not free flow

Anchor: **Spartanburg Medical Center, 101 E Wood St, Spartanburg, SC 29303** (≈ 34.9679, −81.9403), per [Spartanburg Regional](https://www.spartanburgregional.com/maps).

Rush hour is a **weekday 06:30–07:00 arrival** — the 7a hospital shift change. Not generic office peak, which would model the wrong traffic entirely.

- Route via [OSRM](https://project-osrm.org/), which returns free-flow time.
- Apply a congestion penalty weighted toward the I-85 and I-26 corridors.
- Return free-flow and rush-hour separately with the penalty model stated, so the adjustment is inspectable rather than a black box. Rush-hour time is `derived`.
- Anchors are a **list** — adding the Mary Black campus at 1700 Skylyn Dr is configuration, not code.
- **Failure:** commute `unavailable`, hard fail unevaluated, verification task emitted.

Because the 20-minute limit is a hard fail, anything landing between roughly 18 and 22 minutes gets a flag telling the user to drive it at 6:30am before trusting the number in either direction.

### 5. Broadband — block-level, verify by phone

Address-level FCC data requires a licensed Fabric subscription ([FCC](https://www.fcc.gov/sites/default/files/Availability-Challenge-Starter-Kit.pdf)), so the free chain is:

```
address → census block GEOID (station 1) → FCC fixed availability for that block
```

Returns reporting ISPs, technology, and maximum advertised speed. Always `confidence: estimated`, always with `precision: "census_block"`.

"Available" in FCC data means a provider *claims* it can complete a standard install within 10 business days ([FCC](https://docs.fcc.gov/public/attachments/DOC-400675A1.txt)) — a capability claim, not a verified connection. Every result therefore emits a **mandatory** task: call the named ISP with the exact street address. This is permanent, not a stopgap.

### 6. Score

Pure function. No network, no model, fully reproducible.

**Hard fails — score 0, verdict PASS:**

| Condition |
|---|
| FEMA Special Flood Hazard Area (A / AE) |
| Well or septic rather than public water and sewer |
| Rush-hour commute > 20 min |

**Deductions from 100:**

| Condition | Points |
|---|---|
| HOA > $100/mo | −25 |
| Beds < 3 | −20 |
| Sqft < 1,400 | −20 |
| No fiber available | −15 |
| Garage < 2 spaces | −10 |
| Baths < 3 | −8 |

**Caveat flags — no points deducted:**

- Built before 2000
- Roof age ≥ 15 years — expect replacement; escrow or credit
- HVAC age ≥ 12 years — inspect and budget
- Price > 10% above target
- Above $200/sqft — check comps in the same ZIP

**Verdict bands:** ≥ 75 strong fit, take to inspection · 50–74 watch, negotiate or wait · < 50 pass.

Clamped to 0–100.

---

## Output

Every value is an object, never a bare number. This is the contract that makes the provenance claim real.

```json
{
  "input": { "address": "123 Example Rd, Boiling Springs, SC 29316", "price": 315000 },
  "analyzed_at": "2026-08-19T16:45:00Z",
  "engine_version": "0.1.0",

  "location": {
    "lat":   { "value": 35.0421, "source_url": "https://geocoding.geo.census.gov/", "retrieved_at": "2026-08-19T16:45:01Z", "confidence": "measured" },
    "block_geoid": { "value": "450830223011012", "source_url": "https://geocoding.geo.census.gov/", "retrieved_at": "2026-08-19T16:45:01Z", "confidence": "measured" }
  },

  "ownership_cost": {
    "scenario_owner_occupied": {
      "assessment_ratio": { "value": 0.04, "source_url": "https://dor.sc.gov/lgs/property-tax-basics", "retrieved_at": "2026-08-19T16:45:02Z", "confidence": "measured" },
      "annual_tax":       { "value": 1284, "source_url": "https://dor.sc.gov/lgs/property-tax-basics", "retrieved_at": "2026-08-19T16:45:02Z", "confidence": "estimated", "note": "typical owner-occupied millage; parcel-specific district not yet resolved" }
    },
    "scenario_non_owner_occupied": { "annual_tax": { "value": 2891, "confidence": "estimated" } },
    "maintenance_reserve_monthly": {
      "percent_of_price": { "value": 263, "confidence": "estimated", "note": "1% of price / yr" },
      "per_sqft":         { "value": 138, "confidence": "estimated", "note": "$1 / sqft / yr" },
      "age_scaled":       { "value": 394, "confidence": "estimated", "note": "1.5% — built 2004, age 22" }
    },
    "monthly_total_range": { "low": 2180, "high": 2311, "confidence": "derived" },
    "front_end_dti":       { "value": 0.068, "confidence": "derived" }
  },

  "hazard": {
    "flood_zone": { "value": "X", "source_url": "https://msc.fema.gov/portal/home", "retrieved_at": "2026-08-19T16:45:03Z", "confidence": "measured" },
    "nri_rating": { "value": "Relatively Low", "confidence": "measured", "precision": "census_tract" }
  },

  "commute": {
    "free_flow_min":  { "value": 17.2, "source_url": "https://project-osrm.org/", "confidence": "measured" },
    "rush_hour_min":  { "value": 23.8, "confidence": "derived", "note": "weekday 06:30-07:00 arrival; I-85 corridor penalty applied" },
    "anchor": "Spartanburg Medical Center, 101 E Wood St"
  },

  "broadband": {
    "fiber_available": { "value": false, "source_url": "https://www.fcc.gov/BroadbandData", "confidence": "estimated", "precision": "census_block" },
    "providers": [ { "name": "Example Cable", "technology": "cable", "max_down_mbps": 1000 } ]
  },

  "score": {
    "value": 0,
    "verdict": "PASS",
    "hard_fails": ["Rush-hour commute 23.8 min exceeds 20-min limit"],
    "deductions": [],
    "caveats": ["No fiber reported in this census block"]
  },

  "verification_tasks": [
    { "task": "Call Example Cable with the exact street address to confirm fiber serviceability", "blocking": true },
    { "task": "Drive the route at 6:30am on a weekday before trusting the 23.8 min estimate", "blocking": true },
    { "task": "Get an actual insurance quote before the offer", "blocking": false },
    { "task": "Pull parcel tax history at Spartanburg County GIS", "blocking": false }
  ],

  "degraded_sources": []
}
```

`confidence` is one of: `measured` (read from a primary source), `derived` (computed from measured values), `estimated` (rule of thumb or coarser precision than the field implies), `extracted` (from a document by a model, unconfirmed), `unavailable` (source failed).

---

## Failure behaviour

Sources fail routinely. That's the normal case, not the exception.

| Station | On failure |
|---|---|
| Geocode | Fatal. No analysis. |
| Assess | Fall back to typical millage, mark `estimated`, continue. |
| FEMA | `unavailable`, hard fail unevaluated, blocking task emitted. |
| Commute | `unavailable`, hard fail unevaluated, blocking task emitted. |
| Broadband | `unavailable`, treated as "no fiber reported" for scoring, blocking task emitted. |
| Score | Never fails — pure function over whatever is present. |

Every degraded source is listed in `degraded_sources`. A hard fail that could not be evaluated **never silently becomes a pass** — the verdict is downgraded and the reason stated.

---

## Acceptance criteria

1. A bare address with no other fields returns a complete, valid response.
2. Every numeric field carries `source_url`, `retrieved_at`, and `confidence`.
3. Both 4% and 6% tax scenarios are always present.
4. All three maintenance methods are present and individually labeled.
5. Killing any single source except geocode still returns a response, with the degradation named.
6. The same input produces byte-identical scoring output across runs.
7. Assessment-ratio math has unit tests covering owner-occupied, non-owner-occupied, and the school-operating exemption.
8. `analyze --batch addresses.txt` writes the committed artifact for the static site ([ADR 0001](../adr/0001-static-snapshot-plus-local-container.md)).
9. The artifact contains no household financial data — enforced by a CI contract test ([threat model](../THREAT_MODEL.md)).
