# ADR 0012 — `hoc` CLI and the connector sync runner

Status: Accepted — 2026-09-07

## Context

`reconcile()` (ADR 0010) keeps OpenFGA in line with the sources, and the freshness guard (T8)
turns every decision into DENY when a connector has not synced in time. Nothing ran connectors
on a schedule: a deployment would go dark after `max_staleness`. Operators also needed scripts
scattered under `scripts/` to create the store, load fixtures or read the journal.

## Decision

One command, `hoc`, shipped with the core package (`[project.scripts]`), built on `argparse` so
that it adds no dependency:

| Command | What it does |
|---|---|
| `hoc model load` | creates or reuses the OpenFGA store, writes the model, records `HOC_OPENFGA_STORE_ID` / `HOC_OPENFGA_MODEL_ID` in `.env` |
| `hoc fixtures load` | ACME tuples and policy (dev only) |
| `hoc sync --config hoc.connectors.json [--once] [--interval 300]` | runs every configured connector through `reconcile()` with the Postgres state store, then sweeps expired approvals |
| `hoc journal tail [-n 50] [--follow]` | reads the audit journal |
| `hoc approvals list \| approve \| reject` | operator-side approval management, audited |
| `hoc keys generate` | prints a new Ed25519 root key (hex) for `HOC_ROOT_KEY_HEX` |
| `hoc token issue` | dev helper: password grant + `/v1/tokens/issue`, prints a biscuit |

### Connector configuration

`hoc.connectors.json` is a list of connectors, each with a `type` (`files`, `nextcloud`,
`pipeshub`, `keycloak_groups`) and its constructor arguments. Any key ending in `_env` is
resolved from the environment (`"password_env": "NEXTCLOUD_PASSWORD"`), so secrets never sit
in the file. The service reads the same `HOC_*` variables as the API; the CLI loads a `.env`
file from the working directory when present.

### Runner

`hoc sync` is the runner: one process, sequential connectors, a report per run in the journal
(`connector_synced`), failures logged and retried at the next interval. It is packaged as the
`hoc-sync` compose service (profile `api`) and as a Kubernetes `CronJob` in the Helm chart
(`sync.schedule`, default every five minutes, below the default staleness of one hour).

### Approvals sweep

`ApprovalStore.expire_pending(now)` marks overdue `PENDING` requests `EXPIRED`; the runner calls
it after each sync. Resolution logic moves to `actions.approvals.resolve_request` so the gate and
the CLI share it.

## Consequences

- `scripts/load_openfga.py` and `scripts/load_policy.py` become thin wrappers over the CLI.
- The API gains a `/` redirect to `/docs` (a bare 404 confused first-time visitors).
