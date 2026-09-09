# ADR 0027 — Services are asynchronous, stores and token primitives are synchronous

Status: Accepted — 2026-09-09

## Context

After ADR 0022 the blocking work moved off the event loop, but the rule for *who* offloads was
implicit. `MandateService` had async `create`/`issue` next to sync `get`/`list_for`/`revoke`,
`AgentSession.chain()` was sync while `authorize()` was async, and callers (routes, MCP tools)
had to know which method to wrap in `offload`; one of them was missed (`hoc_recall`), and the
connector reconciliation wrote its audit event on the loop. `permits` meant a callable for the
memory service and a mapping for the read filter. Compatibility shims for the modules moved in
ADR 0023 were still imported by tests, `Services` carried four fields nothing read, and the
decider kept a legacy approval hook next to the policy that replaced it.

## Decision

- **Rule.** A *service* (`MemoryService`, `MandateService`, `ActionGate`, `Decider`,
  `AgentSession`'s operations, `filter_items`, `reconcile`) is asynchronous and offloads its own
  blocking calls; a caller never wraps a service in `offload`. A *store* and the *token
  primitives* (`TokenService`, `PrincipalChain`, `AgentSession.delegate`, `AgentSession.permits`)
  are synchronous library building blocks: the CLI and library users call them directly, and
  services offload them. `MandateService.get/list_for/revoke/expire` and `AgentSession.chain()`
  become asynchronous accordingly; `AgentSession.verify_many` disappears.
- **One `permits` shape.** `core.permits.ResourcePermits` is the callable
  `(Kind, resources) -> {resource: bool}`; `filter_items` takes it too and asks it off the loop
  for the candidate references, exactly like the memory service. `AgentSession.permits` and
  `api.deps.permits_of` are that callable.
- **Dead weight removed.** The `api.*` shims, `Services.revocations/approvals/ledger/mandate_store`
  and the no-op `close()` calls, `Decider(approval_policy=)` (superseded by `DecisionPolicy`),
  the duplicate lazy imports and `logging.basicConfig` calls in the CLI, the second
  `API_VERSION` per router, and the remaining avoidable `type: ignore`s.
- **Small coherence fixes.** `TokenResponse.from_issued` / `InspectResponse.from_verified`;
  `cli.connectors` raises `ConfigurationError`; every database command runs `_prepare_schema`;
  `Settings.flag()` and `Settings.oidc_config()`; `tests/support/fakes.py` hosts `FakeGraph`
  and `FrozenClock` for every suite.

## Consequences

- Library callers of `MandateService` and `AgentSession.chain` now `await`; `TokenService` is
  unchanged. No OpenAPI change.
- The layering test can forbid `api` imports outside the HTTP layer with no exception left.
