# ADR 0022 — PostgreSQL stores: one pool, one base, blocking work off the event loop

Status: Accepted — 2026-09-09

## Context

Seven stores (audit journal, revocations, approvals, mandates, provenance ledger, connector
state, memory content) each held one synchronous psycopg connection and reimplemented the same
forty lines of connection handling, three different ways. Every one of them was called from
inside `async` request handlers, so each decision blocked the worker's event loop for its round
trips: `gate` for two, `redeem` for six, `recall` for up to three per candidate. The first load
test (ADR 0015) measured the consequence, about 130 requests per second per worker with the
decider itself at 4 ms; `docs/ideas.md` had parked it. A single connection per store also meant
no concurrency at all inside a process, and a store that lost its connection had to notice on
the next call.

## Decision

### One pool per process and DSN

`headofcontext.db.pool.get_pool(dsn)` returns the process-wide `psycopg_pool.ConnectionPool`
for that DSN (autocommit connections, `min_size=1`, `max_size=HOC_DB_POOL_SIZE`, default 8,
connection health checked on checkout). `psycopg-pool` is the pool of the driver already in the
stack, same maintainers, same licence; no other dependency is added.

### One base for every store

`headofcontext.db.store.PostgresStore` owns the connection checkout, the error mapping and the
three helpers the stores need: `_run(sql, params)`, `_query(sql, params) -> rows`,
`_one(sql, params) -> row | None`, plus `_transaction()` for the audit chain and the memory
write. A subclass declares its `unavailable` error type (`AuditUnavailable`,
`ApprovalStoreUnavailable`, ...) so I5 keeps its typed reasons. Every `SCHEMA` moves to
`headofcontext.db.schema`, which the migration ledger imports: the lowest layer no longer
depends on every module above it.

### Blocking work runs in a thread

The store APIs stay synchronous: the CLI, the library user and the in-memory fakes call them
directly, and `AuditSink`, `ApprovalStore`, `RevocationStore`, `ProvenanceLedger` keep their
Protocols. In asynchronous code, every call into a store or into the synchronous `TokenService`
goes through `headofcontext.core.blocking.offload`, which is `asyncio.to_thread`: the decider's
audit writes, the gate's approval reads and writes, the memory service's ledger calls, the
mandate service's store calls, the freshness check inside the engine guard, the token
operations behind the API routes and `AgentSession`, and the PostgreSQL memory adapter's own
statements. The pool is thread-safe; the default executor bounds the threads.

Nothing is cached and no store call is skipped: the order of checks and journal writes is the
one ADR 0005 and ADR 0008 describe. What changes is that the worker keeps serving other
requests while PostgreSQL answers.

## Consequences

- `psycopg-pool` added (justification above). `HOC_DB_POOL_SIZE` setting, chart and compose
  value.
- Load test before/after in the ADR's commit message; the ceiling moves from the loop to
  PostgreSQL and the pool size.
- Unit tests prove the loop keeps turning while a store sleeps (decider, gate, memory);
  the integration suite runs unchanged on the pooled stores.
- `close()` on a store is now a no-op kept for compatibility; `db.pool.close_pools()` closes the
  process pools at service shutdown.

## Amendment — 2026-09-09: the in-memory stores lock

Offloading store calls to threads made the in-memory stores' read-modify-write sequences
(`transition`, `consume`, `expire_*`, the audit sink's chain append) racy: two winners for one
approval, a forked chain. They now hold a `threading.Lock` around every such sequence;
adversarial tests race eight threads with a tiny switch interval. A pool whose every connection
is busy fails typed within the checkout timeout instead of hanging (tested).
