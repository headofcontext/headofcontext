# ADR 0005 — Append-only audit journal and OpenTelemetry export

Status: Accepted — 2026-09-07

## Context

"Never an unlogged decision" is a product-level promise. The audit journal must be tamper-evident,
must not contain secrets or document content, and must be exportable to whatever observability
stack the customer runs.

## Decision

- `AuditSink` Protocol: `record(event: AuditEvent) -> None`. Raises `AuditUnavailable` on failure.
- `AuditEvent` fields: `event_id`, `timestamp`, `kind` (`decision`, `token_issued`,
  `token_delegated`, `token_revoked`, `memory_written`, `memory_read`), `subject`, `actor`,
  `delegation_depth`, `action`, `resource`, `outcome`, `reason`, `engine_latency_ms`,
  `token_fingerprint` (sha256 hex, never the token), `prev_hash`, `hash`.
- `PostgresAuditSink` writes to table `audit_events` whose role has `INSERT` and `SELECT` only.
  Triggers reject `UPDATE` and `DELETE`. Each row's `hash = sha256(prev_hash || canonical_json)`
  forms a hash chain; `verify_chain()` recomputes it.
- `OtelAuditExporter` emits one span per event with GenAI semantic attributes
  (`gen_ai.agent.id`, `gen_ai.operation.name`) plus `hoc.*` attributes for chain and decision.
- `InMemoryAuditSink` for unit tests, same hash chain.

## Consequences

- Audit writes are on the decision's critical path. An unavailable journal means DENY (I5). This is
  deliberate: silent decisions are worse than unavailable ones.
- Document content, tool arguments and tokens are never stored; arguments are stored as a sha256
  of their canonical JSON.
