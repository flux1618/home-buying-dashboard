# Customer Brief

*Written the way I'd write it for a client engagement. The customer here happens to be me and my partner, which makes the requirements unusually well understood — but the discipline is the same.*

---

## The customer

Two-person household relocating from Hope Mills, NC to the Spartanburg, SC area. One works at Spartanburg Medical Center; the other works remotely in infrastructure engineering, which makes connectivity a hard requirement rather than a preference.

## The decision

**Which submarket, which house, and at what point in the rate cycle do we buy?**

Not "should we buy." That's settled. The open questions are *where*, *when*, and *at what price*, and the cost of getting them wrong is measured in tens of thousands of dollars and years of being stuck.

## The deadline

**Apartment lease ends 2027-08-05.** That is a hard, immovable date. Every intermediate milestone works backward from it:

| When | What has to be true |
|---|---|
| Q4 2026 | Submarkets narrowed to 2–3; lender pre-approval in hand |
| Q1–Q2 2027 | Actively touring; property analysis running per-address |
| Q2–Q3 2027 | Under contract |
| 2027-08-05 | Out of the apartment |
| Jan 15 following close | **SC legal-residence 4% classification filed** — miss this and the tax bill is materially higher |

## Hard constraints

Disqualifying. A house that violates any of these is not a candidate at any price.

| Constraint | Why |
|---|---|
| Not in a FEMA Special Flood Hazard Area (A / AE) | Insurance cost and resale risk |
| Public water and sewer — **no well or septic** | Maintenance liability and replacement cost |
| Commute to Spartanburg Medical Center ≤ 20 min | Shift work; this one is quality of life, not convenience |

## Strong preferences

Scored, not disqualifying. A house can fail these and still win on balance.

| Preference | Weight in score |
|---|---|
| 3+ bedrooms | −20 if fewer |
| 1,400+ sqft | −20 if smaller |
| Fiber internet available | −15 if not |
| 2-car garage | −10 if fewer |
| 3+ bathrooms | −8 if fewer |
| HOA ≤ $100/mo | −25 if higher |
| Built 2000 or later | Caveat flag only, no deduction |

Also wanted, tracked but not scored: a dedicated 20A circuit for the homelab.

## Financial envelope

| Input | Value |
|---|---|
| Household gross income | $406,480 |
| Monthly non-housing expenses | $9,400 |
| Down payment saved | $80,000 |
| Target front-end DTI ceiling | 22% |
| Credit tier | 740+ |

The DTI ceiling is deliberately conservative — well under what a lender would approve. The constraint is the life we want, not the loan we could get.

## The commute anchor

**Spartanburg Medical Center, 101 E Wood St, Spartanburg, SC 29303** (approx. 34.9679, −81.9403), per [Spartanburg Regional](https://www.spartanburgregional.com/maps).

Rush hour is defined as a **weekday 06:30–07:00 arrival** — the 7a hospital shift change, not generic office peak. This matters: free-flow routing and 8am office traffic both produce the wrong number for this household.

A second campus exists at **1700 Skylyn Dr, Spartanburg, SC 29307** ([same source](https://www.spartanburgregional.com/maps)). Anchors are modeled as a list so adding it is configuration, not a code change.

## What "done" looks like

1. Enter a street address; get a scored, fully-sourced verdict in under a minute.
2. Every number carries its source URL and retrieval date. No unattributed figures.
3. Estimates are visibly labeled as estimates and never dressed up as measurements.
4. The tool says **pass** when a house fails, without hedging.
5. It works in April 2027 while standing in a driveway on a phone.

## Explicit non-goals

- **Not** a listing search engine. It scores candidates found elsewhere.
- **Not** an investment or appreciation forecaster.
- **Not** a replacement for an inspection, an appraisal, or an insurance quote.
- **Not** a multi-tenant product. One household, with configuration flexible enough to demonstrate a second persona.

## How success gets judged

Retrospectively, and honestly. The decision journal records the assumptions at the time of each call. If we buy well, the question is whether the process caused it or luck did. That distinction is the entire point.
