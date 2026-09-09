# ADR 0011 — Service API and agent authentication

Status: Accepted — 2026-09-07

## Context

Phases 0–3 delivered HeadOfContext as a Python library. Agent teams will not install biscuit,
OpenFGA and PostgreSQL clients to call it; they need an HTTP service and a thin SDK. The SDK
lives in its own repository (`headofcontext-sdk-python`) and only ever talks to the contract
described here. Premium connectors are out of scope for this ADR.

## Decision

### Who calls the service, and how they prove it

- **Agents** authenticate with **OIDC client credentials** issued by the IdP (Keycloak in dev,
  Entra ID through the premium connector). Each agent is a confidential client; the access token
  it presents in `Authorization: Bearer …` must have the service in its audience. The service
  derives the caller from the token: `agent:<clientId>` (claim `clientId`, or the
  `service-account-<clientId>` username). This caller is what holder binding (ADR 0003) checks
  against the biscuit. No mTLS: certificates would duplicate what the IdP already asserts.
- **Humans** authenticate with their own user access token, only on the endpoints that need a
  human: issuing a root biscuit (the subject proves they are present) and resolving an approval.
- The **biscuit** travels in the JSON body (`token`) of every delegated call. It proves the
  chain; the bearer token proves the caller. Both are verified on every request; nothing is
  cached across requests.

### Endpoints (`/v1`, JSON)

| Method and path | Caller | What it does |
|---|---|---|
| `POST /tokens/issue` | agent + user token in body | verifies the user, checks `agent#can_act_on_behalf_of@user` in OpenFGA, issues a root biscuit for `(user, agent, scope)` |
| `POST /tokens/attenuate` | agent | attenuates a biscuit to `to_actor` with a narrower scope (I1) |
| `POST /tokens/inspect` | agent | returns the chain proven by a biscuit for its holder |
| `POST /tokens/revoke` | agent | revokes the presented biscuit's own block: everything downstream dies (I4) |
| `POST /read/filter` | agent | `filter_items` over `[{"id": …}]`; returns kept and dropped references |
| `POST /actions/gate` | agent | `ActionGate.gate`; returns the decision and a pending approval when needed |
| `POST /actions/redeem` | agent | `ActionGate.redeem` |
| `GET /approvals` | user | pending requests for the caller as approver (all pending in v1) |
| `POST /approvals/{id}/resolve` | user | approve or reject; the approver is the user token's subject |
| `POST /memory/remember` `/recall` `/forget` | agent | `MemoryService` |
| `GET /health` | anyone | liveness, plus connector freshness |

Every response that embodies a decision carries `outcome`, `reason`, `decision_id`,
`timestamp`. Denials are HTTP 200 with `outcome: DENY` (the decision succeeded); token, caller
and identity failures are 401/403 with a `reason` code; an unavailable journal is 503 and
nothing is decided.

### Configuration

Environment variables, all `HOC_*`: root key (hex Ed25519 private key, generated at start in
dev when absent), PostgreSQL DSN, OpenFGA URL and store, OIDC issuer / audience / client,
memory backend (`inmemory`, `mem0`, `zep`), approval tool patterns, connector max staleness.
Secrets come from the environment or mounted files, never from the repository.

### Packaging

A container image (`Dockerfile`, uvicorn), a `docker compose --profile api` service, and a Helm
chart under `deploy/helm/headofcontext` with values for the three external services. The
OpenAPI document is exported to `docs/openapi.json` and is the contract the SDK is written
against; contract tests replay the golden set through HTTP.

## Consequences

- The SDK depends on `httpx` only. It never imports the core.
- `agent#can_act_on_behalf_of` becomes load-bearing: an agent cannot obtain a root biscuit for a
  user it was not bound to, whatever the user token says.
- Later: MCP auth and WIMSE workload identities can replace client credentials without changing
  the biscuit layer.

## Amendment — 2026-09-09: `user_token` must belong to a human

`POST /v1/tokens/issue` took the subject from `identity.subject` without checking that the
identity is a human; a service-account JWT produced the pseudo-user
`user:service-account-<client>`. Exploiting it needed a `can_act_on_behalf_of` tuple for that
pseudo-user, so this is I3 hardening rather than a live bypass. Decided: the route derives the
caller kind exactly as the bearer dependency does and refuses with `user_token_not_human`,
audited, when the token is an agent's. Threat T20.
