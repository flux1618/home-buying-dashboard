# Testing

Two suites, deliberately separated, because they answer different questions.

```bash
pytest              # 385 offline tests, ~1.2s — is the code correct?
pytest -m live      # 6 contract tests — is the world still shaped the way we assumed?
```

The web tests need the optional API extra (`pip install '.[api,dev]'`). Without it they
skip rather than fail, so the suite stays green on a stdlib-only install — which is itself
part of what [ADR 0002](adr/0002-pure-scoring-core.md) promises.

## The offline suite is the one that runs constantly

Sockets are blocked. An autouse fixture in `tests/conftest.py` replaces `socket.socket`, so
any test that tries to reach the network fails loudly instead of quietly making a real
request. The disk cache is redirected to a temp directory, and `FCC_API_KEY` is deleted from
the environment, so a developer who happens to have a key does not get different results
than CI.

The reason for the strictness: a suite that fails when a county server has a bad afternoon
teaches you nothing about your code, and you stop believing it. Fast and deterministic
means you actually run it.

### Fixtures are recorded, never hand-written

Every response in `tests/fixtures/responses/` came off the live API:

```bash
python tools/record_fixtures.py "606 Andre Ct, Spartanburg, SC 29301"
```

This matters more than it looks. A hand-written fixture encodes what you *assumed* the API
returns, and that assumption is exactly what breaks in production. Recorded fixtures caught
that `LivingArea` is often `0` rather than absent, and that the `Garage` field has no bay
count in it at all.

### What the offline tests actually check

The interesting tests are the failure ones. Any parser handles a good response; what makes
the report trustworthy is what happens when a source misbehaves.

| Guarantee | Why it would be a real bug |
|---|---|
| An unmapped FEMA area is `None`, not zone X | Would silently pass a house whose flood status is unknown |
| A missing FCC key writes **no** fact | Would deduct 15 points from every house in the county for having no key |
| `PUBLIC WATER` alone leaves sewer unknown | Sewer is the hard fail; assuming public would pass a septic house |
| `FullBaths = 0` is unknown, not zero baths | County records blanks as zero |
| A garage *type* never becomes a bay *count* | Inventing data to satisfy a scoring rule |
| A station cannot write facts it did not declare | Keeps data flow readable, fails in-process if violated |
| Only geocode is fatal | Everything else degrades and continues |
| A degradation names the facts it cost us | "FCC is down" is useless; "so fiber is unknown" is actionable |
| Buffered parcel matches are disambiguated by street number | Otherwise a neighbour's house gets analysed as yours |
| The core imports nothing networked, and never imports `sources/` | Structural half of [ADR 0002](adr/0002-pure-scoring-core.md) |

## The live suite is news about the world, not about the code

`tests/test_live_sources.py` hits the real endpoints and asserts the fields the parsers read
still exist. It is excluded from the default run and skips rather than fails when a source
is merely down, because a failure there should mean something *changed*, not that something
is temporarily unavailable.

One test asserts that the county's own ArcGIS server is **still unreachable**. That looks
backwards until you consider what a failure means: the certificate chain got fixed, the
stale-2021-mirror caveat can be dropped, and the docs need updating. Encoding a known
limitation as a test is how you find out the day it stops being true.

Run the live suite before trusting a report on a specific house, and on a schedule in CI.

## The CI split mirrors the suite split

Two workflows, divided by what a failure *means* ([ADR 0007](adr/0007-http-service-container-and-split-ci.md)):

| Workflow | Trigger | A red run means |
|---|---|---|
| `.github/workflows/tests.yml` | every push and PR | a commit broke something |
| `.github/workflows/live.yml` | nightly, 09:12 UTC | a public data source changed |

`tests.yml` has to stay boring, and it has four jobs:

- **offline** — the full suite on Python 3.11 (the floor, because the core reads TOML with
  stdlib `tomllib`) and 3.13.
- **core-is-still-pure** — installs with *no* extras, asserts `fastapi`, `pydantic`,
  `httpx`, and `uvicorn` are genuinely unimportable, then runs a complete scoring analysis.
  The `ast`-based import ban would still pass if a dependency had quietly become
  load-bearing at runtime; this job proves the stronger claim by executing it.
- **cli-and-batch** — runs the entry points with `--dry-run`, which makes no requests.
  Catches the class of break unit tests miss: a console script that no longer starts, an
  example file that drifted out of sync with the parser, or a malformed shortlist that
  exits zero when it should exit non-zero.
- **container** — builds `linux/amd64` and `linux/arm64`, then loads the amd64 image and
  boots it. The smoke test calls `/profile`, not just `/health`, because `/health` would
  pass even if the rulebook were unreachable — which was the actual bug this caught.

`live.yml` has no retries and asserts no exact score. Retries would hide the flakiness the
workflow exists to surface, and pinning a score would fail every time a millage rate or an
insurance figure moved. On a scheduled failure it opens or comments on a single
`source-drift` issue rather than filing a new one every night.

## The tests that guard the seams

Three additions worth calling out, because each one exists because of a bug that had
already happened rather than one that was imagined:

**Cross-door agreement** (`TestDoorsAgree`). The CSV and Markdown bodies returned over HTTP
are compared line-for-line against the files the CLI writes for the same input. The shared
helpers were introduced so the API would not grow a parallel implementation; the danger is
someone later finding a shared signature inconvenient and writing a local variant. Each
door then looks correct in isolation, which is why output review would never catch it.

**Late-bound runners.** `analyse_shortlist` once had `runner=run` as a default argument,
which binds the function at *import* time. A test that believed it had stubbed the pipeline
was making real Census requests — and passing. The socket guard is what surfaced it, and
the runner is now resolved at call time.

**A network guard that knows what a network is.** The guard originally blocked every socket,
including the `AF_UNIX` pair asyncio creates internally for its self-pipe. Every web test
failed with a confident message about the internet that had nothing to do with the internet.
It now blocks by address family: `AF_INET` and `AF_INET6` are still refused, local pairs
allowed. A guard whose error message points at the wrong cause is worse than no guard.
