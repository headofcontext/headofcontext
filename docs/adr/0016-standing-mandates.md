# ADR 0016 — Standing mandates: agents that work while the human is away

Status: Accepted — 2026-09-08

## Context

The product's rule already covers every ratio of humans to agents: the subject is always a
human (I2, I3), an agent acts with a subset of that human's rights, and sub-agents hold
attenuated copies. What the current implementation assumes is that the human is **present** when
an agent obtains its root token: `POST /v1/tokens/issue` requires the user's live OIDC access
token, and a root biscuit lives at most 24 hours.

That assumption fits an enterprise assistant opened by an employee. It does not fit the other
shape we also serve: one human with a dozen agents that must act overnight, on a schedule, or in
reaction to events, without that human logging in each time. Today they can
only issue a 24-hour token and renew it by hand. Two smaller gaps sit next to it: self-approval
was hard-wired to `False` (now `HOC_ALLOW_SELF_APPROVAL`, ADR 0014 amendment), and nothing lets
a human see, at a glance, what each of their agents is allowed to do.

## Decision

### Mandate

A **mandate** is a standing authorization from a human to an agent, created while the human is
present and valid until it expires or is revoked:

```
mandate_id, subject (user:), agent (agent:), scope, created_at, expires_at,
max_token_ttl, status (ACTIVE | REVOKED | EXPIRED), created_by, revocation_id
```

- Created by the human: `POST /v1/mandates` (human token) with `agent`, `scope`, `expires_at`
  (bounded by `HOC_MANDATE_MAX_DAYS`, default 90) and `max_token_ttl`. The service verifies that
  the directory binds the agent to the human (`agent#can_act_on_behalf_of`), exactly as `issue`
  does today. Persisted in PostgreSQL, audited (`mandate_created`).
- Used by the agent: `POST /v1/tokens/issue` accepts `mandate_id` **instead of** `user_token`.
  The agent authenticates with its own client credentials; the service checks that the mandate
  is `ACTIVE`, unexpired, belongs to this caller, and that the requested scope is a subset of the
  mandate scope (I1 applies to mandates as it applies to delegation), then issues a short biscuit
  whose TTL is bounded by `max_token_ttl`. Each issued token carries the mandate's
  `revocation_id` as an extra revocation id.
- Listed and revoked by the human: `GET /v1/mandates`, `DELETE /v1/mandates/{id}`. Revoking a
  mandate revokes its `revocation_id`, so every token issued under it, and every sub-token
  attenuated from those, dies before the next decision (I4). No sweep needed.
- Expiry is checked on every read (a mandate past its date reads as `EXPIRED`); `hoc sync` and
  `hoc mandates sweep` mark overdue mandates for reporting.
- The mandate id travels as a `mandate(id)` fact in the signed authority block; `verify` and
  `inspect` add `mandate:<id>` to the revocation ids they check, so attenuated tokens die too.

The chain built from a mandate is an ordinary `PrincipalChain.root(subject, agent, scope)`. No
invariant changes, no OpenFGA model change: the mandate is state in the token layer, like an
approval is state in the action layer.

### What a mandate is not

Not a service account: the subject remains the human, decisions are still made for the human's
rights, and the journal names the human. Not a registry of agents: agent identities stay in the
IdP; a mandate references one and expires. Not a widening: the mandate scope is a subset of what
the human may do, and every token under it a subset of the mandate.

### Surface

| Piece | Change |
|---|---|
| `tokens/mandates.py` | `Mandate`, `MandateStore` (in-memory, PostgreSQL), `MandateService.create / list / revoke / issue_from` |
| API | `POST/GET /v1/mandates`, `DELETE /v1/mandates/{id}` (human, body `expires_in_hours` + `max_token_ttl_minutes`); `IssueRequest` takes exactly one of `user_token` / `mandate_id` |
| CLI | `hoc mandates list --subject`, `hoc mandates revoke ID --by`, `hoc mandates sweep`; `hoc sync` expires overdue mandates |
| SDK | `create_mandate(user_token, agent, scope, expires_in_hours=…)`, `list_mandates(user_token)`, `revoke_mandate(user_token, id)`, `issue_from_mandate(mandate_id, scope)` |
| Audit | `mandate_created`, `mandate_revoked`, `mandate_expired`; `token_issued` carries `mandate_id` |
| Threat model | T13 — stolen mandate id: useless without the agent's client credentials; T14 — long-lived over-broad mandate: bounded by `HOC_MANDATE_MAX_DAYS`, listed to the human, revocable in one call |
| Tests | unit + adversarial (issue with someone else's mandate, expired, revoked, wider scope, wrong caller), integration, golden API scenario, SDK contract test |

### Positioning

The site and the documentation speak of "humans and their agents" rather than "users": every
agent holds a subset of one human's rights, whether the human is one of five hundred or the
only one. Mandates are what makes the second case honest: agents that work for days, revocable
in one call, journaled under the human's name.

## Consequences

- Additive contract change (new routes, one optional field): the SDK gains methods, nothing
  breaks. Requires the contract sync in the SDK repository.
- One more table and store, same pattern as approvals. No new dependency.
- `HOC_ALLOW_SELF_APPROVAL=true` and a mandate are the two settings that turn a deployment into
  a "one human, many agents" one; the defaults stay enterprise-safe.

## Amendment — 2026-09-09: revoking a token never revokes the mandate

The 2026-09-09 adversarial review found that `POST /v1/tokens/revoke` revoked
`revocation_ids[-1]`, meant to be the token's last block. For a token issued under a mandate,
the last id in that tuple is `mandate:<id>`, so a sub-agent holding a minimal attenuated copy
could revoke its own token and kill its principal's standing mandate. `MandateService.issue`
also never consulted the revocation store: a mandate whose id had been revoked still read
`ACTIVE` while every token it issued was dead on arrival.

Decided:

- `TokenService.revoke_token(token, caller, reason)` revokes the token's **own** last block id,
  never a `mandate:` id; the route uses it. Revoking a mandate remains the subject's act through
  `DELETE /v1/mandates/{id}` or `hoc mandates revoke`.
- Every read of an `ACTIVE` mandate (`get`, `list_for`, `issue`) consults the revocation store:
  a mandate whose revocation id is revoked reads and is persisted as `REVOKED`, audited
  (`mandate_revoked`, reason `revocation_id_revoked`). An unreachable revocation store refuses
  (`revocation_store_unavailable`, I5).
- Threat model: T17 (a delegate revokes the mandate). Adversarial tests in
  `tests/adversarial/test_mandates.py`, integration test through the API.
