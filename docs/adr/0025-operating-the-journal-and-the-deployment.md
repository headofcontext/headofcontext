# ADR 0025 — Operating it: journal tooling, logging, image extras, chart hardening

Status: Accepted — 2026-09-09

## Context

The 2026-09-09 readiness review found that the product's central promise, a hash-chained
append-only journal, shipped with no way to verify or export it from the outside
(`verify_chain` existed but was reachable from nowhere, and read the whole table in memory),
no retention statement, and a service that logged with the root logger's defaults: no level
setting, no structured output, no request correlation. The image installed no optional extra
although the chart offered `mem0` and `zep` and the CLI advertised `hoc mcp`, and the chart had
no ServiceAccount, no PodDisruptionBudget and no NetworkPolicy.

## Decision

### The journal can be verified, exported and pruned without breaking the chain

- `verify_rows(rows) -> ChainReport` (`audit.chain`) is the pure verification: every row's
  hash must equal `compute_hash(prev_hash, event)` and every `prev_hash` must equal the
  previous row's hash. The **first row's `prev_hash` is the anchor** and is accepted as is:
  `GENESIS` for a journal that was never pruned, the hash of the last archived row otherwise.
  The report carries the anchor, the range checked and the first break, if any.
- `PostgresAuditSink.verify_chain_report()` streams the table with a server-side cursor in
  batches (`verify_chain()` keeps returning the boolean). `export(since, until)` streams rows
  as JSON lines carrying `seq`, `prev_hash`, `hash` and the canonical payload, so an archive
  file is verifiable on its own with the same function.
- `hoc journal verify` exits 1 on a break; `hoc journal export [--since] [--until] [--out]`
  writes the JSON lines.
- **Retention** is a documented procedure, not a feature that weakens ADR 0005: export the
  range, verify the file, then let a DBA delete the rows below a `seq` in a maintenance window
  with the append-only trigger disabled for that statement. Verification after pruning still
  passes because the anchor is the pruned head's hash, which the archive holds.

### Logging

`HOC_LOG_LEVEL` (default `INFO`) and `HOC_LOG_FORMAT` (`text` or `json`) configure the root
logger when the service starts from `app_from_env` or the CLI runs. The JSON formatter emits
`ts`, `level`, `logger`, `message`, `request_id`; a middleware reads `X-Request-ID` from the
request or generates one, echoes it on the response and binds it to a context variable the
formatter reads. Secrets and content never enter log lines (unchanged rule).

### Image and chart

- `Dockerfile` takes `HOC_EXTRAS` (default `mcp`), a space-separated list of optional extras
  installed into the image, so `hoc mcp`, `mem0` or `zep` work in the shipped container when
  asked for. Base images stay tagged, not digest-pinned, until a CI pins them.
- The chart gains a ServiceAccount with `automountServiceAccountToken: false`, a
  PodDisruptionBudget when `replicaCount > 1`, an opt-in NetworkPolicy (ingress from a
  selector, egress to PostgreSQL, OpenFGA and the IdP left to the operator's CIDRs), and
  `terminationGracePeriodSeconds`.

## Consequences

- No OpenAPI change. `X-Request-ID` is a response header, not part of the contract.
- Two new settings, two Helm sections, one image build argument.
- The "chain broken" branch is now exercised by a unit test on tampered rows.

## Amendment — 2026-09-09: the anchor is explicit, columns are checked, exports follow `seq`

The first version accepted whatever `prev_hash` opened the chain as its anchor. A journal whose
head was removed, or a history rewritten from a foreign hash, therefore verified clean: T10
weakened by the tooling meant to serve it. Decided:

- `verify_rows(rows, anchor=GENESIS)`: the first row's `prev_hash` must equal the anchor, which
  is `GENESIS` unless the operator passes the archived head (`hoc journal verify --anchor`).
  The report carries the `head` hash; an operator records it out of band at each export, which
  is what makes a tail truncation detectable. `hoc journal verify --archive FILE` verifies an
  export on its own and prints its head.
- Rows carry their reporting columns (`kind`, `subject`, `actor`, `action`, `resource`,
  `outcome`, `reason`) and they must agree with the hashed payload: `hoc journal tail` shows
  what was hashed, or verification breaks with `column mismatch`.
- `export` selects by `seq` (`--since-seq`, `--until-seq`), the axis a prune uses, in batches
  that hold no connection between yields. Migration 3 adds the `TRUNCATE` guard to the journal.
- The request-id middleware is outermost, so a `429` carries the id too; ids are ASCII only.
