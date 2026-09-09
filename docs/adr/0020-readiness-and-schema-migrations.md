# ADR 0020 — Readiness probe and versioned schema migrations

Status: Accepted — 2026-09-09

## Context

`GET /v1/health` answered `200 ok` as long as the process ran: with connectors stale, OpenFGA
unreachable or PostgreSQL down, a pod that would deny every decision was "Ready" for Kubernetes
and "healthy" for Docker. That is the right liveness answer and the wrong readiness answer.

The schema was created by six stores at startup, each running `CREATE TABLE IF NOT EXISTS` on
boot: every replica and every CronJob needed DDL rights, a changed column had no upgrade path
(`connector_state_v2` was versioned by renaming the table), and nothing said which schema a
database was at.

## Decision

### Two probes with two meanings

- `GET /v1/health` stays liveness: `200` when the process serves requests.
- `GET /v1/ready` is readiness: it runs the dependencies the next decision needs and answers
  `200 {"status": "ready", "checks": {...}}` or `503 {"status": "not_ready", "checks": {...}}`
  with one entry per check: `postgres` (`SELECT 1` and the schema version), `openfga` (read the
  authorization models of the store), `connectors` (`FreshnessGuard.assert_fresh`, so a required
  connector that never synced keeps the pod out of rotation until the first sync), `schema`
  (`current == latest`). Both probes are exempt from rate limiting. The chart's readiness probe
  and the image's `HEALTHCHECK` use `/v1/ready`; the liveness probe keeps `/v1/health`.

Readiness never caches: a check that fails now is reported now, exactly like a decision.

### One migration ledger, applied on purpose

`headofcontext.db` owns the schema: an ordered list of `Migration(version, name, sql)` and a
`schema_migrations` table recording what was applied and when. Version 1, `baseline`, is the
union of the six store schemas as they are today, all idempotent, so an existing database
adopts the ledger without change. `migrate(dsn)` applies pending versions under
`pg_advisory_xact_lock`, one transaction each; `current_version(dsn)` reads the ledger;
`assert_current(dsn)` raises `SchemaOutOfDate` when the database is behind.

- `hoc db migrate` and `hoc db status` are the operator commands.
- The service applies migrations at startup when `HOC_DB_AUTO_MIGRATE` is true (default, for
  compose and single-node installs) and otherwise **refuses to start** when the schema is behind:
  a service running against a schema it does not know is a fail-closed condition, not a warning.
- The Helm chart runs `hoc db migrate` as a `pre-install,pre-upgrade` hook Job with the same
  secret and sets `HOC_DB_AUTO_MIGRATE=false`, so replicas and the sync CronJob run with a role
  that has no DDL rights.
- The stores keep `ensure_schema()` for library users and the CLI; the service no longer calls
  it. A future column change is a new `Migration`, never an edit of an applied one.

## Consequences

- Additive OpenAPI change (`/v1/ready`, `ReadyResponse`): contract re-exported, SDK gains
  `ready()`.
- A fresh Helm install with `connectorsRequired: true` is visibly not ready until the first
  sync ran, instead of silently denying everything.
- The audit trigger and function are part of the baseline; the append-only guarantee (ADR 0005)
  is unchanged.

## Amendment — 2026-09-09: the probe cannot stall the worker; migrations lock first

- `/v1/ready` opened a synchronous PostgreSQL connection on the event loop per call, anonymous
  and exempt from rate limiting: a flood while PostgreSQL was slow serialised every decision
  behind it (reproduced: 800 probes moved `/v1/health` from 4 ms to 39 ms). `ReadinessProbe`
  runs the PostgreSQL check in a thread, coalesces concurrent probes into the one in flight (a
  coalescer, never a cache: a check that fails now is reported now), and the route is no longer
  exempt from the per-address bucket. Answers are states (`ok`, `error`, `stale`, `behind`,
  `unknown`); connector names and exception classes go to the log, not to anonymous callers.
- `migrate` created the ledger table before taking its lock, so two replicas booting with
  auto-migrate on an empty database could race on `CREATE TABLE`. A session-level advisory
  lock now serialises the whole migration run, ledger creation included; tested with four
  concurrent migrations on a fresh database.

## Amendment — 2026-09-09 (second): roles and the OpenFGA bootstrap

- `scripts/db-roles.sql` creates the runtime role the ADR assumed: data rights on the state
  tables, INSERT and SELECT only on `audit_events`, default privileges for tables a later
  migration creates. The chart's migration Job can take its own secret
  (`migrations.secretName`, the schema owner's DSN) so replicas never hold DDL rights.
- The OpenFGA store and model are bootstrapped from a workstation or a one-off Job with
  `hoc model load --url <openfga> --store-name <name> --no-write-env`, which creates or reuses
  the store by name, writes the model and prints both ids; `openfga.storeId` (and, to pin,
  `openfga.modelId`) go into the chart values. Reloading the model with a pinned id is a
  rolling change: load, then update `modelId`; without a pinned id every replica follows the
  latest model at once.

## Amendment — 2026-09-09 (third): the roles script, corrected

The first `scripts/db-roles.sql` granted `DELETE` on revocations and mandates (an I4 bypass
for a leaked runtime DSN), set default privileges for the superuser instead of the migrator
(tables created by a separate migrator role gave the runtime role nothing), was not idempotent
and hard-coded the database name. The script is now pure SQL, idempotent, runs after
`hoc db migrate` on the target database, creates `hoc_migrator` (owner of every table, default
privileges `FOR ROLE hoc_migrator`, no `DELETE` by default) and `hoc_runtime` with exactly what
the code does per table. An integration test applies it twice and proves what the runtime role
cannot do.
