# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer once released.

## [Unreleased]

First public state. Every decision below has an ADR in `docs/adr/`.

### Added
- Core: principal chain and invariants I1–I5 (0002), biscuit delegation tokens (0003), OIDC and
  RFC 8693 token exchange (0004), append-only hash-chained audit journal (0005), OpenFGA model
  (0006), provenance-aware memory (0007), action gate with single-use approvals (0008),
  LangGraph / CrewAI / PydanticAI / MCP integrations (0009, 0013, 0014), read filter and
  connectors (0010), HTTP service with client-credentials agent authentication (0011), `hoc` CLI
  and sync runner (0012), metrics, root key rotation and rate limiting (0015), standing mandates
  (0016).
- Approver authority through `tool#approver` and atomic approval consumption (0017).
- Memory hardening: per-resource token checks, journaled refusals, configurable backends (0018);
  PostgreSQL memory adapter with full text and optional pgvector embeddings (0021).
- Extension points for connectors, identity providers, memory adapters and approval channels;
  generic agent detection for Entra ID / Okta tokens (0019).
- Readiness probe and versioned schema migrations, `hoc db migrate|status` (0020).
- Pooled PostgreSQL stores with blocking work off the event loop (0022).
- Typed failures (`Unavailable` → 503, `ConfigurationError`) and enforced layer boundaries
  (0023); coherent names and routers (0024).
- Journal verification and export, structured logging with request ids, image extras, chart
  hardening (0025); explicit chain anchor, archive verification and reporting-column checks
  (0025 amendment).
- First-install path: per-command settings, `.env.example`, migration hook Secret, schema
  drift refused in both directions, chart defaults that come up ready (0026).
- Services asynchronous with synchronous stores and token primitives, one `permits` shape
  (0027).
- PostgreSQL roles script (`scripts/db-roles.sql`) and the OpenFGA bootstrap runbook.

### Fixed
- `docker compose up` no longer fails without a `.env`: `HOC_OPENFGA_STORE_ID` is optional at
  interpolation time for the profiled `hoc` and `hoc-sync` services, and the service refuses to
  start without it instead.

### Security
- Threat model rows T1–T21 with an adversarial test each (`docs/threat-model.md`).
- Three adversarial audits on 2026-09-09: approver authority and atomic approval consumption;
  rate limiter eviction and new-bearer budget; readiness probe off the event loop; resolution
  compare-and-set; Keycloak fallback confined; migration lock; memory rollback; real OTLP
  providers; root key required; journal anchor and column checks; locked in-memory stores;
  runtime database role without DELETE on revocations; core secrets stripped from plugin
  environments.
