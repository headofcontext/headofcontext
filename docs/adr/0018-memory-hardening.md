# ADR 0018 — Memory hardening: token checks, journaled refusals, configurable backends

Status: Accepted — 2026-09-09

## Context

Three gaps in REMEMBER remained after the 2026-09-09 review:

1. `/v1/memory/*`, `hoc_recall` and `hoc_remember` rebuilt the chain with `inspect()`, so a
   restriction added to a token as an opaque biscuit block was evaluated for ACT (since ADR
   0008) and READ (since the ADR 0010 amendment) but not for REMEMBER nor for memory reads. The
   resources are only known inside the service: the sources on write, the candidates on read.
2. Two denials in `recall` were not journaled: a failing backend search returned `[]` with a
   log line, and a candidate without a ledger row was dropped with `log.info`. "No decision is
   ever returned unlogged" did not hold there.
3. The service's memory backend defaulted to `inmemory`, including in the Helm chart with two
   replicas: content lived per pod and vanished on restart, while the ledger was in PostgreSQL.
   `mem0` was built with no configuration and `zep` read its key from `os.environ` outside
   `Settings`. The flagship feature only worked in demos.

## Decision

### The service asks the token, per resource, inside the memory operations

`MemoryService.remember`, `recall` and `forget` accept an optional `permits` callable,
`(Kind, resources) -> {resource: bool}`, which the callers build from
`TokenService.verify_each` (API: the request token and the verified caller; MCP:
`AgentSession.permits`). The service calls it where the resources become known:

- `remember`: on the sources (`derived_from`), or on the new memory id when there are none,
  with `Kind.REMEMBER`; any refused resource denies the write with `token_check_failed`,
  audited as `memory_written / DENY`.
- `recall`: for each candidate, on its memory ids and every source, with `Kind.READ`; a refusal
  drops the candidate with `token_check_failed`, audited as `memory_read / DENY`.
- `forget`: on the memory id with `Kind.REMEMBER`.

Without `permits` (library use with a chain built elsewhere) nothing changes: the declared trail
remains the ceiling, as before. Tokens attenuated by HeadOfContext itself carry the same
restriction in the trail and in the checks, so the answers agree; the callable matters for
blocks written with third-party tooling.

### Every refusal in `recall` is journaled

A backend search failure records `memory_read / DENY` with reason `backend_unavailable` on
`memory:*`; a candidate with no ledger row records `memory_read / DENY` with `no_provenance`.
The outcome is unchanged (nothing is returned); the journal now says why.

### Backends are configured from `Settings`

- `HOC_MEMORY_BACKEND`: `inmemory` (default, dev only; the service logs a warning at startup),
  `mem0`, `zep`, or a plugin name (ADR 0019).
- `HOC_MEM0_CONFIG`: the Mem0 configuration as a JSON object (vector store, LLM, embedder);
  passed to `Memory.from_config`. Without it, Mem0's defaults apply.
- `HOC_ZEP_API_KEY` (secret), `HOC_ZEP_BASE_URL` (optional, self-hosted Zep).
- The Helm chart defaults to one replica, exposes `mem0Config`, `zepApiKey` and `zepBaseUrl`,
  and refuses to render `memoryBackend: inmemory` with more than one replica unless
  `config.allowInMemoryMemory` is set: two pods with two different memories is a bug, not a
  configuration.

The chart does not deploy a memory tool: that stays a non-goal (`AGENTS.md`). A PostgreSQL
adapter using pgvector, which is already in the stack, would make the self-hosted path complete
without a third tool; parked in `docs/ideas.md` for a decision.

## Consequences

- Threat T21 (attenuation check ignored on REMEMBER / memory READ); adversarial test with a
  real token and an opaque block through the service and the MCP server.
- No OpenAPI change. `permits` is a keyword-only optional argument: existing callers of
  `MemoryService` are unaffected.
- Three new settings, three Helm values, one Helm guard. `docs/ideas.md` entry replaced by this
  ADR.

## Amendment — 2026-09-09: a failed write leaves no content behind

When the ledger write failed after the backend store, the rollback removed the tuples and the
ledger row but not the content: unreadable (dropped as `no_provenance`) yet retained. The
rollback now deletes the backend row first.
