# ADR 0023 — Typed failures and layer boundaries

Status: Accepted — 2026-09-09

## Context

Two habits had accumulated. Twelve `HocError` subclasses defined outside `core/errors.py`
(store outages, schema drift, plugin failures) fell to the API's default status, `403`: a
PostgreSQL outage answered "forbidden" instead of "unavailable", which misleads both clients and
operators. Eighteen configuration errors were plain `RuntimeError`, and the CLI caught only
`KeyboardInterrupt`, so every operator mistake ended in a traceback.

The layers had also started to lean on each other the wrong way: `core` imported `audit`
(the event model and the metrics class), an integration (`integrations/mcp`) imported the HTTP
layer's Pydantic schemas, and the CLI imported `api.settings` and `api.services`, although
settings and the composition root are not HTTP concerns.

## Decision

### Two error families, one status each

- `Unavailable(HocError)` is the base of every "a dependency did not answer" error:
  `EngineUnavailable`, `AuditUnavailable`, `ApprovalStoreUnavailable`,
  `MandateStoreUnavailable`, `RevocationStoreUnavailable`, `LedgerUnavailable`,
  `ConnectorStateUnavailable`, `MemoryContentUnavailable`, `SchemaOutOfDate`, the connector
  and index clients (`NextcloudUnavailable`, `PipesHubUnavailable`, `PipesHubIndexUnavailable`).
  The API maps `Unavailable` to `503` with the typed reason; the outcome for the caller is still
  a denial (I5), the status now says why.
- `ConfigurationError(HocError)` replaces `RuntimeError` for every startup or wiring mistake
  (missing setting, unknown backend or provider, malformed `HOC_*` value, missing optional
  extra, broken plugin: `PluginError` is one). It never reaches an HTTP response: it stops the
  process, and the CLI prints `reason: message` and exits 1 on any `HocError`.

### Layers, enforced by a test

`tests/unit/test_layering.py` parses every module and refuses:

- `core` importing anything but `core`. The audit event model (`AuditEvent`, `AuditSink`,
  `EventKind`, hashing) moves to `core/events.py`; `audit/events.py` re-exports it. The decider
  types its metrics against a `MetricsSink` Protocol in `core`; `audit.DecisionMetrics`
  satisfies it.
- anything outside `api` importing `api`. Settings, the composition root and the readiness
  probe move to the package root (`headofcontext.settings`, `headofcontext.services`,
  `headofcontext.readiness`); caller derivation moves to `identity/callers.py`; the wire models
  that integrations share with the HTTP layer (`ChainModel`, `DecisionModel`,
  `ApprovalModel`, `MemoryModel` and their parts) move to `headofcontext/models.py`. The old
  `api.*` modules remain as re-exports so existing imports keep working.
- `db` importing anything but `core` and `db`.

## Consequences

- OpenAPI unchanged: the schemas keep their names and shapes, only their home module moves.
- Callers catching `RuntimeError` for configuration mistakes must catch `ConfigurationError`
  (a `HocError`, reason `configuration_error`).
- A store outage is now a `503` on every route, consistently with `EngineUnavailable`.
