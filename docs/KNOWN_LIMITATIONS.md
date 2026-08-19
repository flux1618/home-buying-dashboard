# Known Limitations

Everything this tool gets wrong, or can't know, stated up front. If a number below is used to make a decision without the caveat attached, the tool has failed at its actual job.

---

## Broadband availability is block-level, not address-level

**The limitation.** Address-level broadband data requires a licensed subscription to the FCC's Broadband Serviceable Location Fabric to map addresses to Location IDs ([FCC bulk challenge guide](https://www.fcc.gov/sites/default/files/Availability-Challenge-Starter-Kit.pdf)). Without it, the achievable chain is address → census block → ISPs reporting service in that block.

**Why it matters more than it sounds.** In FCC data, "available" means a provider claims it can complete a standard installation within 10 business days of a request ([FCC](https://docs.fcc.gov/public/attachments/DOC-400675A1.txt)). It is a self-reported capability claim, not a verified connection. Fiber that stops at the end of the street still shows the block as served.

**How it's handled.** Broadband is always `confidence: estimated`, precision is labeled "census block," and every analysis emits a mandatory task: **call the ISP with the exact street address before making an offer.** This verification step is permanent and is not a stopgap.

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

## Not financial advice

A personal decision-support tool. Every value is real and cited, and none of it is investment advice. Verify against a specific TMS or address, with a licensed inspector, lender, and insurer, before committing capital.
