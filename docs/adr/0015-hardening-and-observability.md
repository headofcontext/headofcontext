# ADR 0015 — Hardening and observability

Status: Accepted — 2026-09-07

## Context

The service runs end to end (ADR 0011–0014). Before it faces real traffic, four things are
missing that the brief's success criteria assume: numbers on decision latency that an operator
can watch, a way to rotate the root key without cutting every agent off, protection of the
HTTP surface against a runaway client, and a repeatable load measurement.

## Decision

### Metrics

`headofcontext.audit.metrics.DecisionMetrics` records, through the OpenTelemetry metrics API
(global `MeterProvider`, so the deployment chooses the exporter):

| Instrument | Type | Attributes |
|---|---|---|
| `hoc.decisions` | counter | `hoc.outcome`, `hoc.reason`, `hoc.action` |
| `hoc.decision.duration` | histogram, ms | `hoc.outcome`, `hoc.action` |
| `hoc.engine.duration` | histogram, ms | `hoc.action` |
| `hoc.approvals.notify_failed` | counter | `hoc.channel` |

`Decider` records on every decision it audits; batch paths record once per item. Attributes
carry outcomes and reasons, never subjects, resources or arguments (cardinality and secrecy).
Without an SDK configured the API is a no-op, so the core keeps no dependency on the SDK.

### Root key rotation

Tokens already carry the root key id. Rotation is a configuration change, not a code path:

- `HOC_ROOT_KEY_HEX` + `HOC_ROOT_KEY_ID` is the signing key.
- `HOC_ROOT_PUBLIC_KEYS="<id>=<hex>,<id>=<hex>"` adds verify-only keys (previous signers, or
  the next one while other replicas still sign with the old key).
- `hoc keys generate` prints a private key; `hoc keys public --key-hex <hex>` prints the public
  key to distribute. `docs/key-rotation.md` gives the four-step procedure: publish the new public
  key everywhere, switch the signer, wait one token TTL, drop the old key.

A token signed by a key that is no longer in the ring fails verification (`unknown root key
id`), which is the intended end of the old key's life. Revocation ids are unaffected.

### Rate limiting

A per-caller token bucket in front of the routes (`HOC_RATE_LIMIT_PER_MINUTE`, default 600, `0`
disables), keyed by the bearer token's fingerprint before it is even verified, falling back to
the client address. A bucket runs per process; the limit is a safety valve against a looping
agent, not a fairness scheme: multi-replica fairness belongs to the ingress. Over the limit the
service answers `429` with `Retry-After` and does not touch the IdP, OpenFGA or the database.
`/v1/health` is exempt.

### Load test

`scripts/load_test.py` drives `/v1/actions/gate` (and optionally `/v1/read/filter`) with a
configurable concurrency against a running service, prints p50/p95/p99 and the error count, and
exits non-zero when p95 exceeds a threshold. It is a tool for operators and CI, not a test in the
suite: the brief's `p95 < 20 ms` is measured on `Decider` without the network by
`tests/integration/test_latency.py`, which reports rather than asserts on shared machines.

## Consequences

- No new required dependency: `opentelemetry-api` is already present; the SDK is used in tests
  through an in-memory reader.
- The rate limit changes the OpenAPI contract only by documenting `429`; the SDK's error type
  already surfaces the status.

## Amendment — 2026-09-09: the limiter cannot be evaded with fresh bearers

The bucket was keyed on the fingerprint of the unverified `Authorization` header alone, so a
client that varied the bearer on every request (any garbage) opened a fresh, full bucket each
time and was never throttled, while each forged token forced a JWKS refetch from the IdP: the
opposite of the goal. And once 10 000 buckets existed, eviction cleared every bucket, legitimate
callers included.

Decided:

- A bearer that has no bucket yet must first take one token from a **new-bearer bucket keyed
  by client address** (same capacity, `HOC_RATE_LIMIT_PER_MINUTE`). Established bearers keep
  their own bucket; spraying distinct bearers from one address is refused after `per_minute`
  attempts. Behind a proxy, run uvicorn with `--proxy-headers` and `--forwarded-allow-ips` so
  the address is the client's; without them every new bearer shares one bucket, which is the
  safe direction.
- Eviction drops fully refilled buckets first, then the least recently used half. Never all.
- A forced JWKS refresh after a signature failure happens at most once per
  `OidcConfig.jwks_min_refresh` (30 s); a second failure inside that window is refused
  without a round trip. A genuine key rotation is picked up by the first forced refresh and
  waits at most one interval if forged tokens used it up.

Threat T18, tests in `tests/unit/api/test_rate_limit.py` and `tests/unit/identity/test_oidc.py`.

## Amendment — 2026-09-09 (second): eviction, telemetry providers, root key

- **Eviction kept exhausted buckets only by accident.** "Fully refilled" was computed as
  `3600 / per_minute` seconds instead of the 60 s a bucket actually needs, so at the default
  600/min any bucket idle for 6 s was dropped under table pressure and its owner got a fresh
  600. Eviction now drops refilled buckets (level computed from the rate), then the half with
  the most tokens left, oldest first: an exhausted bucket is the state the limiter exists to
  remember. Tested at 600/min.
- **Telemetry was inert.** `HOC_OTEL_ENABLED` wrapped the journal in an exporter bound to the
  API's no-op providers; nothing configured an OTLP exporter. `audit.telemetry.build_telemetry`
  builds a `TracerProvider` with a batch OTLP/HTTP span exporter and a `MeterProvider` with a
  periodic OTLP/HTTP metric reader (`OTEL_EXPORTER_OTLP_ENDPOINT` or `HOC_OTEL_ENDPOINT`, the
  service name as resource), hands them to `OtelAuditExporter` and `DecisionMetrics`, and shuts
  them down with the services. The dev collector gets a metrics pipeline.
- **No silent ephemeral root key.** Without `HOC_ROOT_KEY_HEX` the service refuses to start
  unless `HOC_ALLOW_EPHEMERAL_ROOT_KEY=true`, which compose sets for its throwaway instance and
  the chart never does. A key regenerated at every restart invalidated every token silently.
