# ADR 0019 — Extension points and identity providers

Status: Accepted — 2026-09-09

## Context

`docs/boundary.md` promised four entry point groups for premium plugins; only
`headofcontext.approval_channels` was discovered in code (ADR 0014). `build_connectors` was an
`if/elif` on four types, the memory adapter a fixed choice among three, and the identity provider
hard-wired to `KeycloakProvider`. Agent detection lived in `caller_from_identity` as Keycloak
conventions (`clientId` claim, `service-account-` username prefix): a client-credentials token
from Entra ID or Okta would have been classified as a human. The private connector repository
had no public seam to plug into, and no test proved that a plugin loads and composes.

## Decision

### One discovery mechanism, four groups

`headofcontext.plugins.discover(group)` loads every entry point of a group and returns
`{name: factory}`. A plugin that fails to import raises `PluginError` at startup: a broken
authorization plugin must never degrade into "not configured". The groups and their factory
signatures:

| Group | Factory | Returns | Selected by |
|---|---|---|---|
| `headofcontext.approval_channels` | `(env) -> ApprovalChannel` | notifies pending approvals | `HOC_APPROVAL_CHANNELS` (ADR 0014) |
| `headofcontext.source_connectors` | `(args) -> SourceConnector` | permissions as tuples | `"type"` in `hoc.connectors.json`; `args` has secrets resolved and `type` removed |
| `headofcontext.identity_providers` | `(settings) -> IdentityProvider` | verifies bearer tokens, exchanges tokens | `HOC_IDENTITY_PROVIDER` |
| `headofcontext.memory_adapters` | `(env) -> MemoryAdapter` | stores and searches memory content | `HOC_MEMORY_BACKEND` |

Built-in names come first (`log`, `webhook`; `files`, `nextcloud`, `pipeshub`,
`keycloak_groups`; `keycloak`, `oidc`; `inmemory`, `mem0`, `zep`) and a plugin cannot shadow
them. `IdentityProvider` gains `aclose()`. Every builder takes an optional `plugins` mapping so
tests inject factories without installing a distribution; `tests/unit/test_plugins.py` loads a
fake plugin through real `importlib.metadata.EntryPoint` objects and composes it into each
builder: that is the contract a premium plugin is written against.

### Agent detection is configuration, not Keycloak folklore

`caller_from_identity(identity, detection)` decides "agent or human" from `AgentDetection`:

- `HOC_OIDC_AGENT_CLAIM` (default `clientId`): a claim whose presence marks an agent token, or
  `claim=value` when the claim exists on human tokens too (Entra ID: `idtyp=app`);
- `HOC_OIDC_AGENT_ID_CLAIM` (default `clientId`, falling back to `azp`): the claim carrying the
  client id that becomes `agent:<id>` (Entra ID: `azp` or `appid`).

The Keycloak `service-account-` username fallback stays. A token that matches the marker but
carries no usable id is refused (`IdentityError`), never downgraded to a human.

`HOC_IDENTITY_PROVIDER` selects `keycloak` (default; group paths like `/rh`), `oidc` (generic:
groups taken verbatim from `HOC_OIDC_GROUPS_CLAIM`), or a plugin. A plugin for Entra ID
therefore only has to implement the Protocol; the service, the CLI and the API do not change.

## Consequences

- `docs/boundary.md` becomes true. The private repository declares its entry points in its own
  `pyproject.toml`; the public one never lists paid names.
- Four new settings (`HOC_IDENTITY_PROVIDER`, `HOC_OIDC_AGENT_CLAIM`, `HOC_OIDC_AGENT_ID_CLAIM`,
  `HOC_OIDC_GROUPS_CLAIM`), exposed by compose and the chart. Defaults reproduce today's
  behaviour exactly.
- Documentation gains an "Extending HeadOfContext" page with the four contracts and the fake
  plugin as the worked example.

## Amendment — 2026-09-09: the `service-account-` fallback is Keycloak's only

Any human whose `preferred_username` started with `service-account-` became
`agent:<rest>` whatever the provider. The fallback now applies only when
`HOC_IDENTITY_PROVIDER` is `keycloak` and `azp` names the same client, which is what a genuine
Keycloak service-account token carries; a self-registered human cannot pick their way into an
agent identity.
