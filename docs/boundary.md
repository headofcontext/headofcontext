# Free / paid boundary

**Free (this repository, Apache 2.0)**: the complete core — OpenFGA model, biscuit tokens,
provenance-aware memory with PostgreSQL / Mem0 / Zep adapters, read filter, action gate and delegation,
framework integrations (LangGraph, CrewAI, PydanticAI, MCP), open connectors (OIDC, Keycloak, PipesHub,
Nextcloud, files), local audit with OTel export, the full test bench including the ACME fixtures.

**Paid (private repository `headofcontext-enterprise`, plugins)**: Entra ID, SharePoint, Google
Workspace, Confluence Cloud, SAP, Salesforce, Workday connectors; real-time sync; approval via
Teams / Slack / ServiceNow; GDPR / AI Act / DORA compliance reports; multi-tenant and HA; audit
console; SLA support.

## Extension points

Premium code plugs in through Python entry points discovered by the core (ADR 0019); the
contract is `tests/unit/plugins/`, a fake plugin composed into every builder:

| Entry point group | Factory | Protocol | Selected by |
|---|---|---|---|
| `headofcontext.identity_providers` | `(settings)` | `IdentityProvider` | `HOC_IDENTITY_PROVIDER` |
| `headofcontext.source_connectors` | `(args)` | `SourceConnector` | `"type"` in `hoc.connectors.json` |
| `headofcontext.memory_adapters` | `(env)` | `MemoryAdapter` | `HOC_MEMORY_BACKEND` |
| `headofcontext.approval_channels` | `(env)` | `ApprovalChannel` | `HOC_APPROVAL_CHANNELS` |

Rule: nothing ever moves from free to paid. Anything in this repository stays Apache 2.0 forever.
