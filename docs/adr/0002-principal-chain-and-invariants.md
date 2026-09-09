# ADR 0002 — PrincipalChain, Scope, Decision and invariants I1–I5

Status: Accepted — 2026-09-07

## Context

Every call into HeadOfContext must carry *who we act for*, *who is acting*, and *what is
allowed*, so that READ, ACT, DELEGATE and REMEMBER can share one decision path and one audit.

## Decision

### Scope

A `Scope` is a frozen set of `Capability(kind, resource)`:

- `kind ∈ {read, act, remember}`
- `resource` is an OpenFGA-style object reference `type:id`, where `id` may end with a single `*`
  wildcard (`document:*`, `document:hr/*`, `tool:mail.send`).

`Scope.covers(capability)` is true if some capability in the scope has the same kind and a
resource pattern that matches. `Scope.is_subset_of(other)` is true if every capability in `self`
is covered by `other`. Patterns are compared syntactically (prefix match on the wildcard); there is
no lookup into OpenFGA at this level. OpenFGA decides whether the *subject* has the right; the scope
decides whether the *actor* is allowed to ask.

### PrincipalChain

```
subject    : UserId            # the human, never absent, never an agent
actor      : AgentId           # the agent executing the current call
delegation : tuple[Delegation] # ordered (from_actor, to_actor, scope)
scope      : Scope             # effective scope of the current actor
```

Construction rules (enforced in `__post_init__` / model validators, raising `InvariantViolation`):

- `subject` must be a `user:` reference. Any other type is rejected (I3).
- `actor` must be an `agent:` reference.
- If `delegation` is empty, `actor` is the root actor and `scope` is the root scope.
- If `delegation` is non-empty: the first link's `from_actor` is the root actor, each link's
  `from_actor` equals the previous link's `to_actor`, the last link's `to_actor` equals `actor`,
  and each link's scope is a subset of the previous scope (I1). `scope` equals the last link's scope.
- `PrincipalChain.delegate(to, scope)` returns a new chain or raises `ScopeEscalation`.

The chain is a pure value object. The token layer (ADR 0003) proves it; the chain never proves
itself.

### Decision

`Decision(outcome, reason, chain, resource, action, timestamp, engine_latency_ms, decision_id)`
with `outcome ∈ {ALLOW, DENY, REQUIRE_APPROVAL}`. A decision is never created without going through
the audit sink (ADR 0005): the `Decider` protocol returns a decision only after `AuditSink.record`
has succeeded. If the audit write fails, the call raises `AuditUnavailable`, which callers must treat
as DENY.

### Fail closed (I5)

Every error path in engines, tokens, identity and connectors maps to `DENY` with a typed reason
(`engine_unavailable`, `token_invalid`, `token_revoked`, `connector_stale`, `audit_unavailable`).
No code path returns `ALLOW` on exception.

## Invariants and how they are tested

| # | Invariant | Test |
|---|---|---|
| I1 | Monotonicity: `scope(n+1) ⊆ scope(n)` | Hypothesis: random chains, random escalations always rejected; random attenuations always accepted |
| I2 | Subject is immutable along the chain | Hypothesis: delegation never changes `subject`; construction with mismatched subject rejected |
| I3 | No orphan agent | Chain without a user subject cannot be built; engine denies any check without subject |
| I4 | Revocation propagates downstream | Token tests: revoking block *n* invalidates every token containing it |
| I5 | Fail closed | Engine and audit tests: every raised error yields DENY with a reason |

## Consequences

- Scope matching is deliberately simple (prefix wildcard). Richer patterns would need an ADR.
- `PrincipalChain` is immutable; delegation always creates a new value.
