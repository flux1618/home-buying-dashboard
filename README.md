# Home-Buying Decision Dashboard

A decision-support system that turns a high-stakes, messy, real-world question — *where and when do we buy a house in Spartanburg County, SC* — into a structured, source-cited answer. Every number on the page points back to a primary source with a retrieval date.

**Live:** [spartanburg.pplx.app](https://spartanburg.pplx.app)

> This is a working tool for a real purchase happening in 2027, not a demo app. It doubles as my portfolio piece for Forward Deployed Engineer work, because the shape of the problem is identical: ingest untrustworthy external data, normalize it, expose it to a non-engineer, and own it in production.

---

## Table of contents

| Category | What lives here |
|---|---|
| [1. Home-Buying Decision Tools](#1-home-buying-decision-tools) | The domain logic — affordability, tax mechanics, scoring, rent-vs-buy |
| [2. Data Pipeline & Integration](#2-data-pipeline--integration) | Where numbers come from, how they're normalized, how they refresh |
| [3. Platform & Reliability Engineering](#3-platform--reliability-engineering) | Deploy targets, containers, CI/CD, observability, security |
| [4. Discovery & Communication](#4-discovery--communication) | ADRs, known limitations, customer brief, demo |
| [Roadmap](#roadmap) | What's next, in build order |

---

## Use case: why I built this

I'm moving from Hope Mills, NC to the Spartanburg, SC area, and my partner and I plan to buy within 1–2 years — roughly Q2–Q3 2027, with the apartment lease as the hard deadline. Because I need data to make an informed decision, I needed a tool that would:

1. Compare 10 submarkets on the same axes without opening 40 tabs.
2. Keep every number sourced.
3. Separate empirical data from estimation.
4. Challenge my intuition with an explicit invalidation case before I commit to a submarket.

Zillow and Redfin optimize for engagement. I wanted their underlying data feeding an actual **decision**, not their UI.

### The framework

The dashboard borrows a discipline from trading: **define invalidation before entry.** Every submarket carries a specific signal that would prove it's a bad buy. The top two picks have explicit walk-away conditions. Decisions are ordered by *logical sequence*, not by priority.

Buying a house is a one-time, high-stakes, hard-to-reverse transaction. Apartment → house is easy. House → house means selling, timing two closings, and moving a full household. So the priority is selectivity, structure, and risk control — in that order.

---

## 1. Home-Buying Decision Tools

The domain layer. Deterministic math only — **no LLM ever touches a dollar figure.**

| Tool | What it does |
|---|---|
| **Affordability engine** | PITI, front-end DTI, cash to close. Applies SC's 4% owner-occupied assessment ratio and the Act 388 school-operating millage exemption. |
| **Assessment-ratio reset** | SC taxes owner-occupied legal residences at **4%** and everything else at **6%**. A listing currently taxed at 6% (rental/second home) will show an inflated tax line that resets *down* for us — and an owner-occupied listing resets *up* if we ever rent it out. The tool models both sides instead of assuming the owner-occupied path. |
| **Rent vs. buy breakeven** | Includes opportunity cost on the down payment. Sensitivity sliders for appreciation and investment return. |
| **Submarket scorecard** | 10 submarkets scored on price, leverage, commute, safety, fiber. Reweightable in the sidebar; ranking recomputes live. |
| **Timing signals** | Nine market watch-items with better/worse thresholds, each linked to its primary source. |
| **Property scorer** | Enter a candidate listing, get a 0–100 fit score against hard rules plus a red-flag checklist. |
| **Cash-flow runway** | Whether we actually hit the down-payment target on schedule, from real household financials. |
| **Interactive map** | Median price, 15/30/45-min commute isochrones, FEMA flood zones, school districts, grocery / hospital / highway-ramp POIs. |

### Buyer profile as configuration

Hard constraints live in configuration, not in code: commute anchor, HOA ceiling, fiber requirement, price target, 20-minute drive rule. The goal is a `buyer_profile.yaml` so the same engine can be pointed at a different buyer — hospital employee, fully-remote engineer, school-district-first family — without editing logic. A decision engine that serves three personas is a meaningfully different artifact than a tool that serves one person.

### Decision journal

Assumptions get recorded with a date, and outcomes get recorded when they land. The point is to show whether the *process* was sound, separately from whether the outcome was lucky.

---

## 2. Data Pipeline & Integration

Every value has a pointer to a real source, or it doesn't ship.

### Current sources

| Layer | Source |
|---|---|
| ZIP-level median price, $/sqft, DoM, inventory | [Redfin Data Center](https://www.redfin.com/news/data-center/) (3-mo rolling ending 2026-05-31) |
| Metro summary (months supply, inventory, YoY) | [Spartanburg Association of Realtors](https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS) |
| 30-yr mortgage rate | [Freddie Mac PMMS](https://www.freddiemac.com/pmms) |
| County population | [FRED SCSPAR0POP](https://fred.stlouisfed.org/data/SCSPAR0POP.txt) |
| Residential permits | [FRED BPPRIV045083](https://fred.stlouisfed.org/data/BPPRIV045083.txt) |
| SC insurance average | [LendingTree](https://www.lendingtree.com/insurance/state-of-home-insurance/) |
| Tax mechanics (Act 388) | [SC DOR](https://dor.sc.gov/lgs/property-tax-basics) |
| Property assessment / reappraisal | [Spartanburg County Auditor](https://www.spartanburgcounty.gov/171/Auditor) |
| Flood zones | [FEMA NFHL](https://msc.fema.gov/portal/home) |
| School district boundaries | [Census TIGERweb](https://tigerweb.geo.census.gov/) |
| ZIP boundaries | [OpenDataDE SC ZCTAs](https://github.com/OpenDataDE/State-zip-code-GeoJSON) |
| Drive-time routing | [OSRM public router](https://project-osrm.org/) |
| POI (grocery, hospital, ramps) | [OpenStreetMap Overpass](https://overpass-api.de/) |
| BMW employment | [BMW Press](https://www.press.bmwgroup.com/usa/) |
| SC WARN filings | [SC Dept of Employment & Workforce](https://www.dew.sc.gov/) |

Estimates (typical millage, generic fiber coverage, forecast paths) are labeled inline as estimates.

### Provenance contract

Every field in `data.json` carries `value`, `source_url`, `retrieved_at`, and `confidence` (`measured` / `derived` / `estimated`). The UI renders that provenance rather than hiding it. If a source is stale past its threshold, the UI says so instead of silently showing an old number.

### Refresh model

Scheduled ETL emits a versioned artifact; the frontend stays a static consumer of that artifact. Global market data (rates, inventory, permits, parcels) refreshes on a schedule. Per-user state (saved properties, notes, price-change history) is a separate concern and gets a real backend only when house tours actually begin.

### Graceful degradation

External sources fail — that's the normal case, not the exception. The pipeline is built so that a dead OSRM router yields a stamped stale route rather than a blank card, an unreachable broadband API yields "unverified" rather than "no coverage," and each degradation path has a test.

### On listing data

**Listing extraction from Zillow / Redfin is a permanent stretch goal, not a roadmap item.** Their terms prohibit it and real MLS access requires an agent relationship. The system builds on county records, FEMA, FRED, Census, and OSM — sources that are actually open. Listings enter the system by manual paste or by a future document analyzer, not by extraction.

---

## 3. Platform & Reliability Engineering

### Current shape

- Single static site: `index.html` + `app.js` + `data.json`. No build step.
- Leaflet 1.9.4 (map) and Chart.js 4.4.4 (charts), both from CDN.
- ~297 KB `data.json` precompiled from primary sources.
- Fontshare Satoshi + General Sans, dark-mode default with light toggle.
- Loads in under 1s over cable; works offline once loaded (tiles cache).

### Two deploy targets, on purpose

| Target | Why it exists |
|---|---|
| **Static** (S3 / pplx.app / any CDN) | Zero cost, zero ops, survives indefinitely, works offline |
| **Containerized** (multi-arch Docker on K3s) | Where the scheduled ETL, the API, and the observability stack live |

This is a deliberate architectural choice with an ADR behind it, not Kubernetes for its own sake. The dashboard must remain deployable by someone with no cluster; the pipeline needs somewhere real to run. I already run a multi-node K3s cluster on Raspberry Pi 5 hardware, so `linux/arm64` images are a requirement, not a flex.

### CI/CD and observability

- GitHub Actions: lint, unit tests on the financial math, multi-arch image build, deploy.
- Contract tests on `data.json` — schema, required provenance fields, staleness bounds.
- Prometheus metrics and a Grafana panel on the app and the ETL: run duration, source success rate, artifact age.

### Security and privacy

This app holds real household income and expense figures. Coming from healthcare, I treat that the way I'd treat PHI:

- Redact personal financials before anything is sent to an external model.
- Explicit retention policy on uploaded documents.
- Auth on any private per-user record; nothing sensitive in the public static artifact.
- Secrets in GitHub Actions / cluster secrets, never in the repo.

See `docs/THREAT_MODEL.md`.

---

## 4. Discovery & Communication

The category most portfolio projects skip. Documents live in `docs/`.

| Artifact | Purpose |
|---|---|
| **Customer brief** | Who the user is, the decision they face, the deadline, hard constraints, what "done" means |
| **ADRs** (`docs/adr/`) | Every consequential choice, with alternatives considered and the cost of being wrong — including *why Kubernetes* and *why not a backend yet* |
| **Known limitations** | What the tool gets wrong, which numbers are estimates, which sources are unverified — stated up front, not buried |
| **Architecture diagram** | Sources → ETL → artifact → UI, one page |
| **Demo video (~5 min)** | Address in, scored decision out, with provenance shown |
| **Field-visit checklist** | Mobile-friendly page for actual house tours — bridges the analysis to the in-person workflow |

### Client mode vs. engineer mode

A UI toggle between a stakeholder-facing view (conclusions, confidence, next actions) and a debug view (raw values, source URLs, retrieval timestamps, calculation traces). Translating engineering output for a non-technical stakeholder is the core FDE skill; this makes it a feature instead of a claim.

---

## Roadmap

Built in this order, deliberately. One coherent vertical slice before any feature breadth.

### Phase 1 — "Analyze a Property" vertical slice

One endpoint / one path. Input: a street address. Output: a scored, fully-provenanced result.

1. Geocode the address.
2. Compute **full cost of ownership**, not just PITI — corrected 4% vs. 6% assessment ratio, actual district millage, insurance, maintenance reserve, HOA.
3. Pull **FEMA National Risk Index** for the tract.
4. Compute a **rush-hour commute** to the work anchor, not a free-flow estimate — isochrone polygons over the I-85 / I-26 pinch points.
5. Verify **broadband** at the address against the SC broadband map.
6. Return a 0–100 score where **every value is stamped with its source and retrieval date.**

This single slice touches all four categories and is genuinely useful the moment we start touring.

### Phase 2 — LLM document analyzer

Paste or upload an inspection report, HOA bylaws, or a seller's disclosure; get back validated, cited, structured JSON. Strict boundary: **AI does unstructured → structured extraction only. It never performs the mortgage arithmetic.** Redaction before send, eval logging, and human confirmation on every extracted field.

### Phase 3 — Saved-property backend

Per-user state: shortlist, notes, price-change history, decision journal entries. Only built once there are real houses to save.

### Phase 4 — Ongoing intelligence

- Amortization calculator.
- Scenario solver: down payment / rate / insurance / HOA / fees in, monthly PITI and max price under a DTI ceiling out.
- Rate sensitivity band, 5.0%–7.5% against the same house, quantifying the buy-vs-wait payment delta.
- Nightly `data/parcels.parquet` from the Spartanburg County GIS REST service — tax PIN, assessed value, tax district, school attendance zone, last sale.
- FRED `MORTGAGE30US` weekly pull (Thursdays).
- Market velocity metrics.
- Discord webhook alerts on price drops and new matches — event-driven, not just a dashboard.
- Natural-language query over the dataset.

### Permanent stretch goals (not planned)

- Zillow / Redfin listing extraction — terms prohibit it.
- MLS integration — requires an agent relationship.

---

## How I use AI, and how I keep my judgment in the loop

**What I own:**
- The constraints — commute anchor, HOA ceiling, fiber requirement, price target, 20-minute drive rule.
- The framework — define invalidation, rank then invalidate, order decisions stepwise.
- Which sources to trust and which submarkets to include.
- The framing of the five decisions, specifically so the tool challenges my intuition instead of an AI making the call.

**What AI is genuinely good at here:**
- Pulling primary-source data across 10 submarkets in parallel.
- Computing commute isochrones from an OSRM grid.
- Normalizing Redfin, crime, broadband, and routing data into one `data.json`.
- Writing HTML/JS scaffolding, which I then review and edit.
- Screenshot-testing the layout at multiple viewports before publishing.

Setting constraints and making decisions keeps me in charge. AI handles the repetitive and error-prone work.

---

## The transferable workflow

This is the same loop I'd run on vendor selection, tool choice, or migration planning — any decision with a high noise-to-signal ratio:

1. Define the decision.
2. Define requirements and explicit no-gos.
3. Define data retrieval, avoiding secondhand error (opinions, over-aggregated data).
4. Normalize, with every value sourced.
5. Score candidates with adjustable weights.
6. Set invalidation criteria.
7. Order next steps by logical sequence.

---

## License

MIT. Adapt it freely — message me if you want help, and I'd genuinely like to see what you build on top of it.

---

## Not financial advice

A personal decision-support tool and portfolio piece. Every value is real and cited, but nothing here is investment advice or a recommendation to buy or sell real estate. Verify every estimate against a specific TMS or address before committing capital.
