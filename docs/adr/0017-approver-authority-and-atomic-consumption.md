# ADR 0017 — Approver authority and atomic approval consumption

Status: Accepted — 2026-09-09

## Context

ADR 0008 made approvals single-use and argument-bound, and ADR 0014 made self-approval a
setting. Two gaps were found in the 2026-09-09 adversarial review, both in the ACT function:

1. **Any human could approve anyone's request.** `resolve` checked that the approver is a
   `user:` reference and differs from the subject, nothing else. `GET /v1/approvals` listed every
   pending request in the deployment to every authenticated human. Rule 2 of `AGENTS.md`
   ("verified tokens and OpenFGA only") was applied to the subject and the actor, never to the
   approver. An intern could list and approve the CFO's `tool:payment.send`.
2. **An approval could be redeemed more than once under concurrency.** `redeem` read the request
   (`APPROVED`), awaited the decider (an OpenFGA round trip), then wrote `CONSUMED` with an
   unconditional `UPDATE`. Every redeem that interleaved during the await passed the checks and
   got `ALLOW`. Reproduced with five concurrent `redeem` calls on one request: five `ALLOW`. The
   adversarial suite only covered sequential replay.

## Decision

### The approver is authorized by OpenFGA, for the tool

The model gains one relation:

```
type tool
  relations
    define can_invoke: [user, group#member]
    define approver: [user, group#member]
```

`resolve(request_id, approver, approved, reason)` now:

1. validates that `approver` is a `user:` reference (unchanged);
2. if `approver == subject`: allowed only when self-approval is enabled (unchanged; the subject
   already holds `can_invoke` on the tool, otherwise the decision would have been `DENY`, not
   `REQUIRE_APPROVAL`);
3. otherwise: `engine.check(approver, "approver", request.tool)` must be `True`. A `False`, an
   engine error or a missing engine is a refusal with a typed reason (`approver_not_authorized`,
   `engine_unavailable`), **audited** as `approval_resolved` with outcome `REFUSED` (I5);
4. resolves and audits as before.

`ActionGate.pending_for(user)` replaces the unfiltered listing on the API: a human sees the
requests they are `approver` of (one `BatchCheck`) plus their own requests as subject. The
operator command `hoc approvals list` still reads the store directly: it runs with database
credentials, not with a user identity, and is the operator's audit view.

The CLI command `hoc approvals approve|reject --approver user:x` goes through the same check
against the configured OpenFGA store; there is no operator bypass.

### Consumption is a compare-and-set, claimed before the decision

`ApprovalStore` gains `consume(request_id, now) -> ApprovalRequest | None`: the transition
`APPROVED → CONSUMED`, atomic in the store (`UPDATE … WHERE status = 'approved' RETURNING …` in
PostgreSQL; check-and-set without an `await` in memory). `redeem` claims the request **before**
calling the decider; a `None` means another redeem won and the outcome is `DENY` with
`approval_consumed`. If the decision that follows is a `DENY` (a tuple removed, a revoked block),
the request stays consumed: fail closed, the human approves again if needed. The
`approval_consumed` audit event carries the decision's reason.

## Consequences

- OpenFGA model v1 gains `tool#approver`; `docs/authz-model.fga.yaml` and the ACME fixtures
  (`direction` approves every tool) are updated; golden and integration suites replayed.
- No OpenAPI change: the routes and schemas are the same, only who may call them and what the
  listing returns. The SDK does not change.
- Deployments that relied on "any human approves" must write `tool:<id>#approver` tuples (or a
  group) before upgrading; without them every approval is refused, which is the safe default.
- Threat model: T15 (approval by an unrelated human), T16 (concurrent redeem).

## Amendment — 2026-09-09: resolution is a compare-and-set too

`resolve` read the request, checked `PENDING` in Python and wrote with an unconditional
`UPDATE`: two authorised approvers racing (a rejection, then an approval a millisecond later)
left the request `APPROVED`. `ApprovalStore.transition(request_id, from_statuses, to_status,
**fields)` is the one atomic state change; resolutions, expiries and consumptions all go
through it, and the second resolver gets `approval_not_pending`. A rejection is never
overwritten.
