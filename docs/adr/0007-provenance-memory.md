# ADR 0007 — Provenance-aware agent memory

Status: Accepted — 2026-09-07

## Context

Agent memory tools (Mem0, Zep/Graphiti) store what an agent learned and hand it back on later
turns, for anyone who asks the same agent. Nothing in them knows that a memory was learned from an
HR document that only HR may read. This is the leak that permissioned retrieval alone does not close, and
the product's priority.

## Decision

### Invariant

A memory inherits the rights of the documents it derives from. Reading a memory for a subject is
allowed only if the subject is `viewer` on **every** source document **at read time**. A memory
without any source is readable only by the subject it was written for.

### Split of responsibilities

| Concern | Where | Why |
|---|---|---|
| Content storage and similarity search | `MemoryAdapter` (Mem0, Zep, in-memory) | that is what memory tools are good at |
| Provenance ledger: `memory_id -> derived_from, written_for, written_by, content_hash, backend refs` | `ProvenanceLedger` (Postgres, in-memory) | provenance must not live where a memory tool (or someone with its credentials) can rewrite it |
| Relationship truth: `memory:X#derived_from@document:Y`, `memory:X#writer@agent:A` | OpenFGA tuples through `TupleStore` | one engine, one graph |
| Decision and audit | `Decider.decide_all` | one decision path shared with READ and ACT |

At read time the source set is the **union** of what the ledger records and what OpenFGA holds
under `derived_from`. Tampering with either store can only add constraints, never remove them. A
search hit whose backend reference is unknown to the ledger is dropped: it has no provenance we can
vouch for.

### Scope semantics

- `Capability(REMEMBER, "document:hr/*")`: the agent may create memories derived from those
  documents. Checked per source document at write time.
- `Capability(REMEMBER, "memory:*")`: the agent may store memories that derive from no document
  (conversation-only), and may forget memories. Checked on the memory id.
- `Capability(READ, "memory:*")`: the agent may recall memories. Checked per memory at read time.

### Write path (`MemoryService.remember`)

1. `Decider.decide_all(chain, Action.remember(), derived_from)`: scope covers REMEMBER on each
   source, subject is `viewer` on each source now. Any DENY → nothing is stored.
2. Tuples are written to OpenFGA, then the ledger row, then the content in the adapter. If the
   adapter fails, tuples and ledger row are removed (best effort) and the error propagates.
3. Audit `memory_written` with the content hash, never the content.

Writing a memory with an empty `derived_from` is allowed (the agent learned something from the
conversation itself). It is readable by `written_for` only.

### Read path (`MemoryService.recall`)

1. Adapter search returns candidates `(content, backend_refs)`.
2. Each candidate resolves to one or more ledger rows; sources = union of their `derived_from`
   from the ledger and from OpenFGA.
3. `Decider.decide_all(chain, Action.read_memory(), sources, scope_resources=[memory_id])`, or the
   `written_for` rule when there is no source. DENY → the candidate is dropped, its content never
   leaves the service.
4. One audit `memory_read` event per candidate, kept or dropped.

### Forget path

`MemoryService.forget` removes the adapter content, the ledger row and the tuples. Only the
subject the memory was written for may forget it, and only through an actor holding
`REMEMBER` on `memory:<id>`.

## Consequences

- The OpenFGA model is unchanged: `memory#derived_from`, `memory#writer` already exist (ADR 0006).
  `memory#viewer` in the model is "viewer of some source"; the ALL rule is the service's job.
- Mem0 and Zep clients are optional extras. Their adapters are typed against the subset of their
  APIs we use and tested against fakes; the real backends are exercised in the demo, not in CI.
- Recall cost is one BatchCheck per candidate. Candidate lists are small (`limit` ≤ 20 by default).
