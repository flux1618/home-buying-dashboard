# Threat Model

This application holds real household financial data — income, expenses, savings, and eventually documents containing names and property details. I spent my earlier career around PHI, and the reflex carries over: decide where sensitive data is allowed to go *before* building the thing that moves it.

Scope is deliberately proportionate. This is a two-person tool, not a regulated multi-tenant platform. The point is to be explicit about what's protected and what isn't.

---

## What's worth protecting

| Asset | Sensitivity | Where it lives |
|---|---|---|
| Household income, expenses, savings | High | `data.json` today — **see gap below** |
| Candidate addresses and notes | Medium | Local artifact / future backend |
| Uploaded documents (inspection, disclosure, HOA) | High — may contain names, defects, financials | Local only |
| Decision journal | Medium | `ledger.db` (was: repo) |
| Saved shortlist: real addresses, full financial analyses, private notes | High -- this is the one store that accumulates rather than recomputes | `ledger.db`, outside the repo tree |
| Upstream API tokens (FRED, FCC BDC) | Medium | Environment / CI secrets |

---

## Who we're worried about

**Realistic:**
- Anyone browsing a public GitHub repo or the live site. This is the main one, and it's not hypothetical.
- A third-party LLM provider retaining document contents for training.
- Someone on the same LAN reaching an unauthenticated local API.

**Explicitly out of scope:**
- Targeted attackers. Nothing here justifies that assumption.
- Nation-state or supply-chain compromise of upstream government APIs.
- Physical access to the homelab.

---

## Open gap, acknowledged

**Real household financials are currently committed to a public repository** in `data.json` — gross income, monthly expenses, savings balance.

That is a deliberate tradeoff for a portfolio piece that needs plausible numbers to demonstrate anything, but it should not stay this way. Planned remedy:

1. Move real figures into an untracked local config.
2. Ship representative-but-fictional defaults in the committed artifact, clearly labeled as illustrative.
3. Real values are supplied at runtime and never committed.

Recorded here rather than quietly fixed, because an undocumented known issue is worse than a documented one.

---

## Controls

### Before anything reaches a language model

Per [ADR 0004](adr/0004-llm-scope-boundary.md), models only extract from documents. Before a document is sent:

- **Redact** names, SSN-shaped strings, account numbers, phone numbers, and email addresses via deterministic pattern matching. Redaction happens in `core/`, is unit-tested, and cannot be skipped by a caller.
- **Send only what's needed** — the relevant pages, not the whole file.
- **Prefer a local model** where quality allows. A self-hosted model on the existing cluster means documents never leave the network at all.
- **Log every call** — timestamp, document hash, fields requested, provider, and whether redaction fired. This doubles as the eval log.

### The ledger file

- **It lives outside the repository by default** -- `$HBA_DATA_DIR`, else the XDG data
  directory. A default of `./ledger.db` would put real addresses and a real household's
  financial position one `git add -A` away from a public commit.
- **`.gitignore` covers `ledger.db` and `*.db` anyway**, as a second layer, because `--db
  ./scratch.db` while debugging is a normal thing to type.
- **In the container it is a named volume separate from the response cache.** The cache is
  disposable; the ledger is not, and nothing upstream can replay it. Sharing one volume would
  invite treating both as scratch space the first time the cache needs clearing.
- **No content is ever sent anywhere.** The ledger is written by local doors only; no source
  adapter reads from it and nothing uploads it.
- **Back it up by copying one file.** `sqlite3 ledger.db .dump` produces a plain-text export,
  which is also worth knowing before trusting a single file with a year of decisions.

### Documents

- Stored locally, never committed. `.gitignore` covers `documents/` and `*.pdf` at the root.
- Deleted after extraction is confirmed. No document is retained longer than the analysis needs it.
- No document is ever included in the public artifact.

### The local API

Per [ADR 0001](adr/0001-static-snapshot-plus-local-container.md), the API is not exposed to the internet — which removes most of the attack surface by construction rather than by configuration.

- Binds to `127.0.0.1` by default. Binding to `0.0.0.0` is an explicit opt-in.
- Container runs as a non-root user with a read-only root filesystem.
- No secrets baked into the image; injected at runtime.
- If Tier C (public tunnel) is ever adopted, authentication becomes a prerequisite, not a follow-up.

### Secrets

- Never in the repo. `.env` is gitignored; `.env.example` documents the required names with no values.
- CI uses GitHub Actions secrets; cluster deploys use Kubernetes secrets.
- Every upstream token is scoped read-only. None of these APIs offer write access, which limits the damage a leak could do.

### The public artifact

One rule: **the committed artifact contains only aggregate market data and non-sensitive analysis output.** No income, no savings, no document contents, no personal notes. A contract test in CI fails the build if forbidden keys appear in the artifact — enforcement in the pipeline rather than in a reviewer's memory.

---

## Accepted risks

| Risk | Why accepted |
|---|---|
| Local API has no authentication | Bound to localhost, single-user, on a trusted LAN. Auth becomes required if exposure changes. |
| Upstream APIs see the addresses queried | Unavoidable — geocoding requires sending the address. All are government or public sources. |
| Third-party LLM sees redacted document text | Mitigated by redaction and page limiting; eliminated entirely if the local model path is used. |
| Illustrative financials could be mistaken for real ones | Labeled in the UI and in this document. |
| The ledger is unencrypted at rest | Disk-level encryption is the operating system's job and doing it again in the application would mean holding a key somewhere in the same threat boundary. A stolen laptop is not a threat this file can defend against by itself. |
| A mistaken entry in the ledger is permanent | Append-only is the point ([ADR 0011](adr/0011-ledger-is-append-only-and-separate.md)). Corrections are new rows, and the audit value of an uneditable record outweighs tidiness. |

---

## Review

Revisit when any of these changes: the API becomes publicly reachable, a second user is added, document upload ships, a cloud LLM provider is introduced, or the ledger is synced or backed up anywhere off the machine that wrote it. Each of those invalidates an assumption above.
