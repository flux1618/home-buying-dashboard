# Known Limitations

Everything this tool gets wrong, or can't know, stated up front. If a number below is used to make a decision without the caveat attached, the tool has failed at its actual job.

---

## Broadband availability is block-level, not address-level

**The limitation.** Address-level broadband data requires a licensed subscription to the FCC's Broadband Serviceable Location Fabric to map addresses to Location IDs ([FCC bulk challenge guide](https://www.fcc.gov/sites/default/files/Availability-Challenge-Starter-Kit.pdf)). Without it, the achievable chain is address → census block → ISPs reporting service in that block.

**Why it matters more than it sounds.** In FCC data, "available" means a provider claims it can complete a standard installation within 10 business days of a request ([FCC](https://docs.fcc.gov/public/attachments/DOC-400675A1.txt)). It is a self-reported capability claim, not a verified connection. Fiber that stops at the end of the street still shows the block as served.

**How it's handled.** Broadband is always `confidence: estimated`, precision is labeled "census block," and every analysis emits a mandatory task: **call the ISP with the exact street address before making an offer.** This verification step is permanent and is not a stopgap.

**Additional constraint discovered when wiring it up.** The FCC National Broadband Map API has **no anonymous tier** — requests without credentials return HTTP 401. Without `FCC_API_KEY` set in the environment, the broadband station reports itself degraded and writes **no fact at all**. It specifically does not report "no fiber," which would deduct 15 points from every house in the county because of a missing key. For most users, fiber is therefore a manual input.

---

## The county's own parcel server is unreachable, so parcel data is from February 2021

**The limitation.** The authoritative source is Spartanburg County's ArcGIS server at `maps.spartanburgcounty.org`. It cannot be reached: the host serves an **incomplete TLS certificate chain**, so every request fails with `SSL: CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate`. This is not a timeout and not rate limiting; the connection cannot be verified.

The fallback is a public ArcGIS mirror of the county's **Parcel and CAMA extract dated February 1, 2021** — 29,402 records. Every field read from it (year built, bedrooms, baths, utilities, assessment-ratio code) reflects the property as of that date.

**Why it matters.** A house renovated, subdivided, re-roofed, or reclassified since early 2021 will read wrong. The 4%/6% assessment-ratio code in particular describes the **owner at that time**, which may not be the current owner.

**How it's handled.** Mirror values are `confidence: estimated` with the 2021 vintage in the note, and the fallback always emits a **blocking** task to pull the current parcel card from the [Spartanburg County Assessor](https://www.spartanburgcounty.gov/168/Assessor) by hand. Disabling certificate verification to reach the county directly was considered and rejected ([ADR 0006](adr/0006-source-station-contract.md)). A live contract test asserts the county server is still unreachable, so the day it is fixed shows up as a test failure.

---

## Parcel matching buffers the geocoded point, and can pick the wrong lot

**The limitation.** The Census geocoder returns a point interpolated along the **street centreline**, which routinely lands a metre or two outside the parcel it belongs to — for 606 Andre Ct it sat roughly 3×10⁻⁵ degrees past the eastern edge, so a strict point-in-polygon query returned nothing for a house that plainly exists. The query is therefore buffered by 40 metres.

**Why it matters.** On a dense street, a 40-metre buffer can return several neighbouring lots. Silently taking the first would attach a neighbour's bedroom count, year built, and utility type to your analysis.

**How it's handled.** Candidates are disambiguated by matching the street number in the address. When nothing matches, the chosen record is flagged and a **blocking** task lists the alternatives by address. The flag depends on the input address containing a street number, so a lot without one is a known blind spot.

---

## Garage bay count is never read from county data

**The limitation.** The CAMA `Garage` field takes exactly six values: `CARPORT ATT`, `CARPORT DET`, `GARAGE ATT`, `GARAGE DET`, `GARAGEBSMT`, `NONE`. **No bay count exists anywhere in the dataset.**

**How it's handled.** `garage_spaces` stays a user input, and the analysis emits a task to confirm the count. Reading "GARAGE ATT" as two bays to satisfy the two-bay preference rule would be inventing data.

Similarly, `LivingArea` is frequently `0`, meaning *not recorded* rather than zero square feet. Zero is treated as unknown, which means square footage often has to come from the listing or an appraisal.

---

## The FEMA National Risk Index is not wired in

**The limitation.** The National Risk Index API endpoint was unreachable during development, so broader hazard context (wildfire, wind, heat) is absent. Only the regulatory NFHL flood zone is used.

**How it's handled.** Nothing pretends to cover it. Flood is scored; other natural hazards are not assessed at all.

---

## Millage rates are typical, not parcel-specific

**The limitation.** Total millage depends on the exact combination of county, municipal, school, and special-purpose districts a parcel sits in. Until the Spartanburg County GIS parcel lookup is wired in, the tool applies a typical owner-occupied millage.

**Impact.** Estimated tax can be off by a few hundred dollars a year — enough to matter in a close comparison, not enough to change a shortlist.

**Status.** Fixed by the parcel adapter. Until then, taxes are `confidence: estimated`.

---

## Assessment resets to sale price, and the ratio may change

SC assesses owner-occupied legal residences at **4%** and other property at **6%**, and the legal-residence classification also removes school operating millage ([SC DOR](https://dor.sc.gov/lgs/property-tax-basics)).

Two consequences the tool models but that still deserve a human check:

- A listing currently taxed at 6% (rental or second home) shows an inflated tax line that will reset *downward* for an owner-occupier.
- Assessed value resets to the sale price at purchase. **The seller's current tax bill is not your future tax bill,** and listing sites routinely display the former.

**The deadline that isn't a modeling problem.** The legal-residence classification must be filed with the county. Missing it costs real money and no software prevents that — it's on the calendar, not in the code.

---

## Rush-hour routing is a modeled estimate

**The limitation.** Commute times use [OSRM](https://project-osrm.org/), which routes on free-flow speeds. A rush-hour penalty is applied for a weekday 06:30–07:00 arrival, concentrated on the I-85 and I-26 corridors.

**What it can't capture.** Incidents, construction, weather, or the specific misery of a single interchange at a single moment. The number is a planning estimate, not a promise.

**How it's handled.** `confidence: derived`, with the penalty model stated inline. The 20-minute hard limit is evaluated against this estimate — so a house at 19 or 21 minutes should be driven in person at 6:30am before it's trusted either way.

---

## Insurance is a state average until quoted

The tool uses a South Carolina statewide average ([LendingTree](https://www.lendingtree.com/insurance/state-of-home-insurance/)). Actual premiums vary substantially by roof age, construction, claims history, distance to a fire station, and carrier appetite.

**Every analysis emits a task to get a real quote before the offer.** For a specific house this can move the monthly payment by a hundred dollars or more.

---

## Maintenance reserve is three guesses, shown as three guesses

Three industry rules of thumb are presented side by side — 1% of price per year, $1 per sqft per year, and an age-scaled rule (1% under 10 years, 1.5% for 10–30, 2% over 30). All three are `confidence: estimated`.

They are deliberately **not averaged.** Averaging three rules of thumb produces a single number that looks precise and isn't. The spread between them is the honest answer.

---

## Crime and school quality are proxies

School *district* boundaries come from [Census TIGERweb](https://tigerweb.geo.census.gov/) and are geographically accurate. School *quality* is not modeled at all — it's judgment, and encoding a test-score ranking as a quality score would be false precision.

Crime figures are agency-reported and not comparable across jurisdictions with different reporting practices.

---

## Market data is a rolling snapshot with a lag

ZIP-level pricing is a three-month rolling window from [Redfin Data Center](https://www.redfin.com/news/data-center/); metro summaries come from [SAR](https://scr.stats.showingtime.com/docs/mmi/x/MarketActivityfortheSpartanburgAssociationofREALTORS). Both lag the market by weeks. In a fast-moving market, the trend direction is more reliable than the level.

The public site is a **committed snapshot** ([ADR 0001](adr/0001-static-snapshot-plus-local-container.md)), so its data is as of the last commit — shown as a date in the UI, not implied to be live.

---

## FEMA flood zones are the regulatory map, not a risk model

[FEMA NFHL](https://msc.fema.gov/portal/home) zones drive the flood hard-fail. But properties outside a Special Flood Hazard Area still flood, and FEMA maps are known to be out of date in places. The National Risk Index adds broader hazard context but is tract-level, so it describes a neighborhood rather than a lot.

---

## The score is a rule encoder, not a model

The 0–100 score has no statistical backing. It encodes one household's stated preferences with hand-assigned weights, and it is not trained on or validated against anything.

That's intentional. Its value is consistency and transparency — the same house always scores the same way, and every deduction is inspectable. Treating it as a prediction of satisfaction or resale value would be a category error.

---

## No appreciation forecast

Rent-vs-buy breakeven takes appreciation as a **user-adjustable input**, never a prediction. Nobody credibly forecasts local home appreciation, and a tool that pretended to would be the least trustworthy thing here.

---

## The service is unauthenticated, so it binds to localhost only

The HTTP layer ([ADR 0007](adr/0007-http-service-container-and-split-ci.md)) has no login,
no API key, and no rate limit. That is a reasonable trade for a tool running on one machine
on a home network, and it is the reason `docker-compose.yml` publishes `127.0.0.1:8000`
rather than `0.0.0.0:8000` — the responses contain real household financial figures, so
reaching it from the rest of the LAN should be a deliberate change. Exposing it to the
internet would require auth first. See [THREAT_MODEL.md](THREAT_MODEL.md).

---

## Batch mode is sequential, and that is not an accident

A ten-property shortlist takes roughly ten times as long as one property. These are free
public endpoints — OSRM's demo server and Nominatim both ask for polite use, and the HTTP
layer throttles per host. Firing ten concurrent requests at a county GIS server to save
nine seconds on a weekend shortlist is how a useful public service ends up locked down.

Batch output files are named by **CSV line number, not by rank**. Re-running a shortlist
after a price change therefore overwrites the same filenames instead of shuffling them,
which keeps week-over-week diffs readable. Ranking lives in `summary.csv` and `shortlist.md`.

---

## A property with no county record is capped, not trusted

Two addresses with no parcel match once scored a perfect 100 and outranked a house with
complete data, because every deduction is guarded against missing values — silence read as
perfection. Scores are now capped below the TAKE threshold when facts are unknown, with the
unknowns listed ([ADR 0005](adr/0005-capital-expenses-deduct-and-unknowns-pin.md)).

The cap is one-directional: it can only lower a score, never raise one. A capped score means
"not enough is known to recommend this", not "this is a worse house".

One cosmetic artifact remains: when square footage is unknown, the true-monthly range
collapses to a single number, because all three maintenance-reserve methods need sqft.

---

## The container image was first built in CI, not locally

Docker was unavailable in the environment where the `Dockerfile` was written, so its first
real execution is the CI build. What *was* verified locally is the failure mode that
mattered: installing the package into a clean virtualenv and running it the way the image
does, which is how `load_profile()` was found to resolve into `site-packages` once installed
and how `HBA_PROFILE` came to exist. The CI smoke test calls `/profile` specifically to keep
that regression caught.

---

## The public page scores only what you type, and can look nothing up

The snapshot at `index.html` has no backend by design (ADR 0001), so it cannot geocode an
address, read the county parcel record, query the FEMA flood layer, or measure a drive time.
Everything its scorer knows, a visitor typed in. That makes its hard-fail checks as reliable
as the person filling the form — the engine *looks up* flood zone, water and sewer, and
commute; the page takes your word for them.

It is also missing the entire financial model. No millage, no 4%/6% assessment reset, no
amortisation, no insurance proration, no cash-to-close, and none of the three
maintenance-reserve methods. The sliders model affordability generically; only the local
engine prices a specific parcel. The page says this in its "Run the full engine" section
rather than leaving it to be discovered.

## The page's rules are generated, so a stale snapshot is possible

The browser cannot import the Python engine, so its thresholds and weights are compiled from
`buyer_profile.toml` into `data.json` by `tools/build_snapshot.py` (ADR 0008). Edit the profile
without rebuilding and the published page keeps answering with the old numbers.

This is not hypothetical. It is what happened before the compiler existed: the hand-written
JavaScript treated an HOA over $100/mo as disqualifying while the engine deducted 25 points,
and ignored roof and HVAC age entirely while the engine deducted up to 57. On 606 Andre Ct the
page showed a confident TAKE where the engine said 52 WATCH, and a house with a $150/mo HOA and
nothing else wrong was rejected outright by the page and scored 75 TAKE by the engine.

`python tools/build_snapshot.py --check` now runs in CI and fails the build when the two
diverge, so the drift is loud rather than silent. The residual risk is a page published from a
working tree that was never pushed.

## Only a subset of the rules is expressible in the browser

The compiled rule block holds threshold comparisons, capex tiers, caveat limits, and verdict
bands — the parts that are genuinely declarative. Anything requiring real computation stays in
Python. If a future rule cannot be written as a comparison against a number, the page will not
be able to apply it, and the correct response is to point the user at the local engine rather
than to grow the compiler into a second implementation.

---

## Not financial advice

A personal decision-support tool. Every value is real and cited, and none of it is investment advice. Verify against a specific TMS or address, with a licensed inspector, lender, and insurer, before committing capital.
