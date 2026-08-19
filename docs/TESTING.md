# Testing

Two suites, deliberately separated, because they answer different questions.

```bash
pytest              # 272 offline tests, ~0.5s — is the code correct?
pytest -m live      # 6 contract tests — is the world still shaped the way we assumed?
```

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
