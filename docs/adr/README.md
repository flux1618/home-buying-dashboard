# Architecture Decision Records

Short records of consequential choices: what was decided, what else was considered, and what it costs if the decision turns out to be wrong.

Format is deliberately lightweight — context, decision, alternatives, consequences. If a record can't state the cost of being wrong, the decision probably wasn't understood well enough to write down.

| # | Decision | Status |
|---|---|---|
| [0001](0001-static-snapshot-plus-local-container.md) | Public site is a committed snapshot; the API runs locally in a container | Accepted |
| [0002](0002-pure-scoring-core.md) | Scoring engine is a pure Python package with no framework or network | Accepted |
| [0003](0003-no-listing-or-mls-extraction.md) | No Zillow/Redfin extraction and no MLS integration | Accepted |
| [0004](0004-llm-scope-boundary.md) | LLMs do extraction only and never arithmetic | Accepted |
| [0005](0005-capital-expenses-deduct-and-unknowns-pin.md) | Aging roof/HVAC deduct with a dollar range; unknown hard-fail inputs pin the score to 50 | Accepted |
| [0006](0006-source-station-contract.md) | Every external source is a station with one failure contract: never raise, never guess | Accepted |
| [0007](0007-http-service-container-and-split-ci.md) | CLI, batch, and HTTP are three thin doors on one engine; CI splits offline-per-push from live-nightly | Accepted |
| [0008](0008-browser-rules-are-compiled-not-rewritten.md) | The static page is the fourth door; its scoring rules are compiled from the profile, never hand-written | Accepted |
