# ADR 0024 — API surface hygiene: names, routers, plugin environment

Status: Accepted — 2026-09-09

## Context

Three small inconsistencies noted in the 2026-09-09 review, each cheap to fix now and expensive
once the library has users: token operations with mismatched names (`revoke(id)` next to
`revoke_token(token)`, `verify` next to `verify_each`, `verify_read` on the session), the HTTP
layer as one 335-line factory of closures, and plugins receiving a copy of the whole process
environment, unrelated secrets included.

## Decision

### Names

| Before | After | Meaning |
|---|---|---|
| `TokenService.verify_each` | `TokenService.verify_many` | one load, one authorizer run per resource |
| `TokenService.revoke(revocation_id)` | `TokenService.revoke_id(revocation_id)` | revoke one block or mandate id |
| `TokenService.revoke_token(token, caller)` | `TokenService.revoke(token, caller)` | revoke the presented token's own block |
| `AgentSession.verify_read(resources)` | `AgentSession.verify_many(operation, resources)` | the chain and the per-resource answer |
| `filter_items(token_permits=)` | `filter_items(permits=)` | same keyword as `MemoryService` |

`inspect` keeps its name: verification without an operation. Nothing is published yet, so no
alias is kept.

### Routers

`api/app.py` keeps the factory (lifespan, middleware, error handler) and includes one
`APIRouter` per function from `api/routes/`: `system`, `tokens`, `read`, `actions`, `approvals`,
`mandates`, `memory`. Request-scoped dependencies live in `api/deps.py`. Route function names,
paths and models are unchanged, so the OpenAPI contract is byte-identical (verified by
re-export).

### Plugin environment

`Settings.env` is the slice of the environment whose names start with `HOC_` or `OTEL_`
(`settings.plugin_env`). A channel, a memory adapter or an identity provider reads its own
`HOC_*` variables; it never sees the cloud credentials or the tokens of the process around it.

## Consequences

- Library callers rename five identifiers; the SDK and the HTTP contract are unaffected.
- Plugins that read variables outside `HOC_*`/`OTEL_*` must move them under `HOC_`.

## Amendment — 2026-09-09: the core's secrets never reach a plugin

`Settings.env` also strips `HOC_ROOT_KEY_HEX`, `HOC_OIDC_CLIENT_SECRET`, `HOC_POSTGRES_DSN` and
`HOC_ZEP_API_KEY`: a third-party channel or adapter reads its own `HOC_<PLUGIN>_*` variables,
never the signing key or the database.
