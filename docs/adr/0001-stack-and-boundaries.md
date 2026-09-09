# ADR 0001 — Stack and non-goals

Status: Accepted — 2026-09-07

## Context

HeadOfContext is an authorization layer, not a platform. Every component we could build
ourselves (policy engine, index, memory, agent framework) already exists and is better maintained
by someone else. Our value is the *composition*: one principal chain, one decision engine, one audit
across READ / ACT / DELEGATE / REMEMBER.

## Decision

Stack is frozen as described in `AGENTS.md`. Notable choices and why:

- **OpenFGA** as the only relationship engine. We never reimplement graph evaluation. Group
  membership, document ACLs, tool invocation rights and memory provenance are all tuples.
- **biscuit** for delegation tokens. Attenuation is cryptographic: a sub-agent can only ever hold a
  token that is a strict restriction of its parent's. Verification is offline; revocation is a set
  of block ids. This gives invariants I1 and I4 for free at the token layer.
- **OIDC over `httpx` + `joserfc`** for identity: discovery, JWKS, JWT verification and the
  RFC 8693 token exchange (a form POST), so that actions run under the *user's* identity and
  never under a technical account. Authlib was the initial choice; see ADR 0004 amendment for why
  it was dropped.
- **PostgreSQL** for the append-only audit journal and connector state. No ORM: a handful of
  parameterized SQL statements through `psycopg`.
- **OpenTelemetry** with GenAI semantic conventions for exported audit spans.

## Non-goals (never build)

Policy engine, RAG platform, memory store, agent framework, LLM gateway, agent registry,
directory, admin console. When a task drifts toward one of these, stop and ask.

## Consequences

- Any deviation from the stack requires a new ADR.
- Everything in the core is Apache 2.0. Premium connectors live in a private repository and plug in
  through entry points (see `docs/boundary.md`).
