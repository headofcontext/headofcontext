# Architecture Decision Records

One decision per file, numbered, never edited after acceptance (superseded by a new ADR instead).

| # | Title | Status |
|---|---|---|
| [0001](0001-stack-and-boundaries.md) | Stack and non-goals | Accepted |
| [0002](0002-principal-chain-and-invariants.md) | PrincipalChain, Scope, Decision and invariants I1–I5 | Accepted |
| [0003](0003-biscuit-delegation-tokens.md) | Biscuit tokens for delegation, attenuation and revocation | Accepted |
| [0004](0004-identity-and-token-exchange.md) | OIDC identity and RFC 8693 token exchange | Accepted |
| [0005](0005-append-only-audit.md) | Append-only audit journal and OpenTelemetry export | Accepted |
| [0006](0006-openfga-authorization-model.md) | OpenFGA authorization model v1 | Accepted |
| [0007](0007-provenance-memory.md) | Provenance-aware agent memory | Accepted |
| [0008](0008-action-gate-and-approvals.md) | Action gate, policies and persistent approvals | Accepted |
| [0009](0009-framework-integrations.md) | Framework integrations (LangGraph, CrewAI) | Accepted |
| [0010](0010-read-filter-and-connectors.md) | Read filter and source connectors | Accepted |
| [0011](0011-service-api-and-agent-authentication.md) | Service API and agent authentication | Accepted |
| [0012](0012-cli-and-sync-runner.md) | `hoc` CLI and the connector sync runner | Accepted |
| [0013](0013-mcp-integration.md) | MCP integration: guarded proxy and HeadOfContext MCP server | Accepted |
| [0014](0014-pydanticai-and-approval-channels.md) | PydanticAI toolset and approval channels (log, webhook, entry points) | Accepted |
| [0015](0015-hardening-and-observability.md) | Metrics, root key rotation, rate limiting, load test | Accepted |
| [0016](0016-standing-mandates.md) | Standing mandates: agents that work while the human is away (amended 2026-09-09) | Accepted |
| [0017](0017-approver-authority-and-atomic-consumption.md) | Approver authority (`tool#approver`) and atomic approval consumption | Accepted |
| [0018](0018-memory-hardening.md) | Memory hardening: token checks, journaled refusals, configurable backends | Accepted |
| [0019](0019-extension-points-and-identity-providers.md) | Extension points (four entry point groups) and identity providers | Accepted |
| [0020](0020-readiness-and-schema-migrations.md) | Readiness probe and versioned schema migrations | Accepted |
| [0021](0021-postgres-memory-adapter.md) | PostgreSQL memory adapter (full text, optional pgvector embeddings) | Accepted |
| [0022](0022-async-postgres-stores.md) | PostgreSQL stores: one pool, one base, blocking work off the event loop | Accepted |
| [0023](0023-typed-failures-and-layer-boundaries.md) | Typed failures (`Unavailable`, `ConfigurationError`) and layer boundaries | Accepted |
| [0024](0024-api-surface-hygiene.md) | API surface hygiene: names, routers, plugin environment | Accepted |
| [0025](0025-operating-the-journal-and-the-deployment.md) | Operating it: journal tooling, logging, image extras, chart hardening (amended 2026-09-09) | Accepted |
| [0026](0026-first-install.md) | First install: per-command settings, chart install order, schema drift both ways | Accepted |
| [0027](0027-services-async-stores-sync.md) | Services are asynchronous, stores and token primitives are synchronous; one `permits` shape | Accepted |
| [0028](0028-versioning-and-releases.md) | Versioning and releases from Conventional Commits (release-please, squash merges) | Accepted |
