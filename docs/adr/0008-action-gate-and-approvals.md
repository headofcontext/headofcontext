# ADR 0008 — Action gate, policies and persistent approvals

Status: Accepted — 2026-09-07

## Context

READ filters what reaches the model; ACT decides whether a side effect may happen. A tool call is
authorized at execution time, for the *subject*, with the agent's delegated scope, and sometimes
with a human in the loop. The core owns the state of that approval, never the UI.

## Decision

### One decision path, plus policies

`Decider` gains an optional `DecisionPolicy`, evaluated **after** scope and OpenFGA say yes:

```
evaluate(chain, action, resource, args, context) -> Verdict(outcome, reason)
```

A policy can only lower an outcome (ALLOW → REQUIRE_APPROVAL → DENY), never raise it. Policies
see tool arguments as **data** (threat T3): they may constrain them (amount ≤ limit, recipient in
domain) but nothing in the arguments can turn a DENY into an ALLOW. `DecisionContext` carries an
`approval_id` when the call redeems a previously approved request.

Built-in policies (`headofcontext.actions.policies`): `RequireApproval(tool patterns, when=…)`,
`DenyWhen(tool patterns, predicate, reason)`, `CompositePolicy` (most restrictive wins).

### ActionGate

`gate(chain, tool, args) -> GateResult(decision, approval_request | None)`:

1. `decider.decide(chain, Action.invoke(), tool, args)` — scope `act` on the tool, OpenFGA
   `can_invoke` for the subject, then policies.
2. On `REQUIRE_APPROVAL`, an `ApprovalRequest` is persisted (`PENDING`) with the subject, actor,
   delegation depth, tool, sha256 of the canonical arguments, decision id and an expiry. The
   arguments themselves are not stored.
3. The caller gets the pending request; the action is **not** executed.

`resolve(request_id, approver, approved, reason)`: the approver is a `user:` reference, must be
different from the subject unless `allow_self_approval=True`, and can only resolve `PENDING`
requests before expiry. Every resolution is audited.

`redeem(chain, request_id, args) -> Decision`: the request must be `APPROVED`, unexpired, unused,
issued for the **same subject, actor and delegation depth**, the **same tool** and the **same
argument hash**. The decision is then re-run through `Decider` with `DecisionContext(approval_id)`
so that scope and OpenFGA are checked **again** (an approval does not survive a revocation), and
the request is marked `CONSUMED` (single use). Any mismatch is a DENY with a typed reason.

### Approval store

`ApprovalStore` Protocol with `InMemoryApprovalStore` and `PostgresApprovalStore` (table
`approval_requests`). Status transitions: `PENDING → APPROVED | REJECTED | EXPIRED`,
`APPROVED → CONSUMED`. Notification channels (Teams, Slack, ServiceNow) are premium plugins on the
`headofcontext.approval_channels` entry point and only *observe* the store.

### Composition with tokens: `AgentSession`

`AgentSession(token, caller, token_service, gate)` is the unit every integration uses. For every
tool call it verifies the biscuit for `(caller, act, tool)`, rebuilds the chain from the signed
trail, and runs the gate. Nothing is cached between calls: a revoked block or a removed tuple
takes effect on the next call. `delegate(to_actor, scope)` returns a new session holding an
attenuated token.

## Consequences

- Approvals are single-use and argument-bound: "approve once, replay forever" is impossible.
- The gate never executes the tool; frameworks decide what to do with a DENY (raise, or hand the
  model a denial message). Both are safe because the side effect did not happen.
