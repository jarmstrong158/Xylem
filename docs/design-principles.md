# Design principles

Five decisions recur across context-keeper, agentsync, and cambium. They aren't a
style guide imposed from above — they're the same answers arrived at independently
because the tools face the same constraints: multiple agents, long time horizons, no
infrastructure anyone wants to babysit. This document distills them from each repo's
README and DESIGN.md.

## 1. Rationale-first records

Capturing *what* was decided is cheap and nearly useless; the expensive, perishable
thing is *why*. So the records are shaped to force the why.

- context-keeper's `record_decision` **requires** a problem statement and rationale,
  not just a conclusion. Deprecated decisions stay retrievable as a ranking signal, so
  "why did we change from X?" has an answer instead of a hole.
- cambium's `distill()` bridges agentsync (which records *what happened*) and
  context-keeper (which records *why*) precisely because neither is sufficient alone.

The test: could a fresh agent reconstruct the reasoning, or only the outcome? These
tools keep the reasoning.

## 2. No central server

None of the coordination or knowledge layers stand up a service. The substrate is git,
which every agent already has and which no one has to operate.

- agentsync coordinates through a `claims.json` file on a dedicated branch — "git as
  coordination bus" — so agents work asynchronously and never need to be online at the
  same time.
- cambium stores knowledge in git at every scope (`.cambium/knowledge.json` locally, a
  `cambium` branch for team, a separate repo for org) rather than "reintroducing the
  side system — infra to run, a place knowledge goes to be forgotten."

A server is a thing that can be down, can lose data, and needs credentials and
babysitting. The absence of one is a feature.

## 3. Compare-and-swap over locks

Mutual exclusion without a lock manager, by leaning on the atomicity primitive the
substrate already provides.

- agentsync uses a single shared `claims.json` specifically to create write contention,
  then resolves it with optimistic concurrency: fetch, read, validate overlap, write,
  commit, **`git push` as the compare-and-swap**. A rejected push means someone claimed
  first — the agent re-syncs to the remote tip and re-evaluates.
- agentsync-remote gets the same semantics from the GitHub Contents API, where the
  blob-sha check is a CAS that maps 1:1 onto push-based CAS. That equivalence is what
  lets a phone and a desktop share one coordination file with no arbiter between them.
- cambium's team scope promotes knowledge using the same git compare-and-swap pattern.

Locks require a lock holder. CAS requires only that writes are atomic and losers retry —
which is exactly what git already guarantees.

## 4. Local-first, with remote transports (not remote-first)

Every capability works fully offline first; the remote is an additive transport over the
*same* data and protocol, never a different system.

- context-keeper runs entirely from human-editable JSON with zero required
  dependencies; semantic retrieval is strictly additive and falls back to lexical search
  if the embedding service is unreachable.
- Each `-remote` sibling (context-keeper-remote, agentsync-remote) is a Cloudflare
  Worker speaking the identical protocol and writing the identical files — D1 mirrors the
  local store, the Worker's `claims.json` *is* the local `claims.json`. Transport
  differences are transparent to the logic above them.
- cambium keeps lexical scoring as the default so tests are deterministic and there are
  no runtime dependencies, with embeddings able to swap in "behind the same contract."

The payoff is the stack's headline: because remote is the same protocol, not a
reimplementation, claude.ai mobile is a full peer — it can claim, survey, and answer,
not merely observe.

## 5. Fail closed, abstain over confabulate

When the system is unsure — about authorization or about an answer — it declines rather
than guesses.

- The remote connectors embed the auth token in the URL and are explicit that it must be
  treated like a password; rotating the token immediately invalidates every prior URL.
  Access you can't prove is access you don't get.
- agentsync-remote scopes its GitHub token to Contents read/write on the coordination
  repo only — least privilege by construction.
- cambium's `recall()` returns `no_confident_match: true` below a relevance threshold,
  with explicit guidance against presenting weak results as fact. context-keeper applies
  the same instinct with a relevance abstention floor that prevents confabulation.

And the honesty extends to limits: cambium states outright that claims completed and
re-claimed between distill runs can be lost, and agentsync that it is "textual, not
semantic" — it flags files that won't merge, not contracts that broke. A tool that names
what it doesn't do is one you can actually reason about.
