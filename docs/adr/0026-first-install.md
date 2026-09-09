# ADR 0026 — First install: per-command settings, chart install order, schema drift both ways

Status: Accepted — 2026-09-09

## Context

Following the README literally failed at the second command: `hoc model load` wrote only the
OpenFGA ids to `.env`, and every other command demanded the database, OpenFGA and OIDC settings
at once (`hoc journal tail` refused to run without an IdP client secret). A fresh
`helm install` could not succeed either: the migration Job was a `pre-install` hook that
mounted the release's ConfigMap and Secret, which do not exist yet at that point. A database
migrated by a newer version was accepted by older code, although the documentation promised
the opposite. And the chart's defaults (connectors required, sync enabled against
`keycloak.example.com`) meant a release was never ready without overrides.

## Decision

- **Commands ask for what they use.** `Settings.from_env(require=...)` validates only the keys
  a caller names: `REQUIRE_DB` for `hoc db`, `hoc journal`, the sweeps and the listings,
  `REQUIRE_ENGINE` where OpenFGA is asked, `REQUIRE_SERVICE` (everything) for the service and
  the token helpers. A migration Job needs one variable, the DSN.
- **`.env.example` is the workstation's starting point.** It carries the compose stack's dev
  values; `hoc model load` merges the keys it lacks into `.env` before recording the ids, so
  the quickstart runs command after command. The values are dev-only and say so.
- **The migration hook carries its own Secret.** Unless `migrations.secretName` or
  `existingSecret` is given, the chart renders a hook-managed Secret holding the DSN with a
  lower hook weight; the Job reads that key and nothing else. Job and CronJob pods carry the
  chart labels (so the NetworkPolicy applies), the ServiceAccount, no mounted token, and
  resources; the image tag defaults to the chart's `appVersion`; a chart-managed Secret change
  rolls the pods; probes have timeouts.
- **Schema drift is refused in both directions.** `assert_current` and `migrate` refuse a
  database newer than the code; readiness reports `ahead`. A rollback under a newer schema is
  a visible failure, not a silent run.
- **Defaults that come up ready.** `connectorsRequired: false` and `sync.enabled: false` until
  the operator points `sync.connectors` at a real directory; the values say to turn both on for
  production (T8). `networkPolicy.egress` accepts extra rules verbatim for the IdP, the
  collector and webhooks.

## Consequences

- No OpenAPI change. `Settings` fields that used to be required now default to empty strings;
  the service still refuses to start without them.
- Chart `README.md` gives the three-step install; the deployment guide is updated.
