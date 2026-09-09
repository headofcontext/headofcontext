# ADR 0021 — PostgreSQL memory adapter

Status: Accepted — 2026-09-09

## Context

The free tier had no durable memory backend: `inmemory` lives in one process, `mem0` and `zep`
are third tools that bring their own LLM and vector store. Anyone wanting provenance-aware
memory self-hosted had to operate one of those, which contradicts "independent of the memory
tool". The provenance ledger already lives in PostgreSQL, and pgvector is part of the frozen
stack (ADR 0001).

## Decision

`PostgresMemoryAdapter` (`headofcontext.memory.postgres`, backend name `postgres`) is the
reference `MemoryAdapter`: it stores content and searches it, nothing else.

- **Storage**: table `memory_content (namespace, backend_ref, memory_id, content, tsv,
  created_at)`, migration 2 (ADR 0020), in the same database as the ledger, the journal and
  the revocations. One backup, one role, one migration ledger.
- **Search**: PostgreSQL full-text search with the `simple` configuration (language-agnostic,
  ACME is French), `websearch_to_tsquery`, ranked by `ts_rank`, scoped by namespace. This is what
  `InMemoryMemoryAdapter` did, durable and shared between replicas.
- **Optional embeddings**: an embedder, `Callable[[str], Sequence[float]]`, may be given. Then
  `store` also writes `memory_embedding (namespace, backend_ref, embedding vector)` and `search`
  orders by cosine distance, falling back to full text when the query embeds to nothing. The
  migration creates the extension and that table **only where pgvector is available** (a `DO`
  block), so the migration never fails on a plain PostgreSQL and full text works everywhere. An
  adapter given an embedder on a database without the table refuses at construction.
- HeadOfContext embeds nothing itself and calls no model provider: in the service the embedder
  is `HOC_MEMORY_EMBEDDER=package.module:callable`, resolved by import at startup, empty by
  default. The callable is the operator's (their model, their key).

### What it is not

No fact extraction, no consolidation, no deduplication, no graph, no summary, no TTL. Those are
what Mem0 and Zep do; requests for them are answered with "configure `mem0` or `zep`", never with
features on this adapter. The non-goal "not a memory tool" (`AGENTS.md`) is kept by that rule.

### Defaults

`HOC_MEMORY_BACKEND` defaults to `postgres` in compose and in the Helm chart (`inmemory`
remains for tests and the library quickstart). With a shared backend the chart's one-replica
default is no longer forced by memory; the `inmemory` guard stays.

## Consequences

- Migration 2; integration tests against the real PostgreSQL (store, search, namespace
  isolation, delete, the pgvector path with a fake embedder); the API integration suite runs
  with `postgres` as the memory backend.
- No new dependency: psycopg and the pgvector extension already ship with the stack.
- `docs/ideas.md` entry resolved.
