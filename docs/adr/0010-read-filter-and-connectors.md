# ADR 0010 — Read filter and source connectors

Status: Accepted — 2026-09-07

## Context

Permissioned retrieval exists (Onyx, PipesHub, Glean). HeadOfContext does not index anything; it
takes whatever the index returns and lets through only what the *subject* may view, before the
model sees a byte. For that, OpenFGA must know the source's ACLs: connectors turn them into
tuples and keep them fresh.

## Decision

### `read.filter_items`

```
filter_items(chain, items, *, engine, audit, id_of=None, relation="viewer") -> FilterResult
```

- Each item resolves to a `document:` reference through `id_of` (default: `item["id"]` or
  `item.id`). An item whose reference is missing or malformed is **dropped**, never guessed.
- The actor's scope must cover `READ` on every reference; uncovered items are dropped before
  the engine is asked.
- The subject must be `viewer` on each item: one BatchCheck per chunk of 50 (the OpenFGA batch
  limit). Above `LIST_OBJECTS_THRESHOLD` items, `ListObjects` is used once and intersected; both
  paths yield the same set and are tested to agree.
- Any engine or connector failure empties the result (I5).
- Ordering of kept items is the index's ordering; the filter never re-ranks.
- Audit: a filter is one decision over a set. It is journaled as one `read_filtered` event with
  the counts and the sha256 of the sorted kept and dropped references. `per_item_audit=True`
  adds one `decision` event per item for deployments that want it.

`Decider.decide_each` is the underlying primitive: one engine batch, one `Decision` per resource,
policies applied per resource.

### Connectors

`SourceConnector` Protocol: `name`, `async snapshot() -> Snapshot`. A snapshot is the desired
set of tuples for that source (`source#viewer/editor`, `document#parent`, `document#viewer/editor`)
plus document metadata (id, title, path). `reconcile(connector, tuples, state)` diffs the snapshot
against the previous one stored in `ConnectorStateStore`, writes the additions, deletes the
removals, and records the sync time. The state store is also the `FreshnessGuard`: a connector
older than `max_staleness` turns every decision into DENY (T8).

Connectors never decide; they translate. They run with the least privilege the source allows:

| Connector | Source of truth | Identity mapping |
|---|---|---|
| `files` | a directory tree and a JSON ACL manifest | refs are literal in the manifest |
| `nextcloud` | WebDAV listing + OCS share API of a service account owning the tree | `user:<uid>`, `group:<gid>` |
| `pipeshub` | PipesHub records and their permission entries | configurable, defaults to `user:<email local part>` / `group:<name>` |
| `keycloak_groups` | Keycloak admin API, group membership | `user:<username>`, `group:<path>` |

Document references: `document:<id>` where `<id>` is the file stem by default for `files` and
`nextcloud` (`id_strategy="stem"`), or the backend's own id (`id_strategy="backend"`).

Group hierarchy, tool rights and agent bindings are organization policy, not source ACLs; they
are loaded from a policy file (`fixtures/acme/generated/policy-tuples.json` for ACME) by
`scripts/load_policy.py`.

### End to end

The golden set is green end to end when: Keycloak provides the groups, Nextcloud provides the
document ACLs, both connectors have synced into an empty store, and `filter_items` over the
question's candidate documents returns exactly the expected visible set for 300 questions.

## Consequences

- PipesHub's own permission-aware retrieval is not bypassed: HeadOfContext filters *after* the
  index, so the two compose. PipesHub exposes per-record permission entries only through
  `GET /api/v1/knowledgeBase/record/{recordId}` (`relationship` OWNER / WRITER / READER, `type`
  USER / GROUP); the connector enumerates records through the knowledge-hub nodes API and reads
  each one. It is validated against the SDK 1.x response shapes in unit tests; running PipesHub is
  not part of CI and the principal mapping (`principal_of`) must be confirmed on a real tenant.
- Nextcloud shares are read with the service account that owns the tree: link, mail and
  federated shares carry no principal HeadOfContext can vouch for and are ignored.
- Connector snapshots are full, not incremental; real-time sync is a premium concern.

## Amendment — 2026-09-09: the read filter evaluates the token's checks

`/v1/read/filter` and the MCP `hoc_filter` tool rebuilt the chain with `inspect()`, which
verifies signature, revocation, expiry and holder binding but evaluates **no** datalog check. The
decider then relied on the declared trail (`chain.scope`) only. Tokens attenuated through
`TokenService.attenuate` were unaffected, since the trail and the check are written together;
but a restriction added as an opaque block with standard biscuit tooling (`check if
resource($r), $r == "document:public"`) was ignored for READ, while `verify()` honoured it for
ACT.

Decided: `TokenService.verify_each(token, caller, operation, resources)` loads the token once and
runs the authorizer per distinct resource (about 25 µs each; 5 000 resources in ~130 ms). The
read filter calls it for `Kind.READ` before asking the engine; resources the token refuses are
dropped with `token_check_failed` and counted in the summary audit event. The memory paths
(`/v1/memory/*`, `hoc_recall`, `hoc_remember`) still rebuild the chain with `inspect()`: their
resources are only known after the search; parked in `docs/ideas.md`.

Threat T19, tests in `tests/adversarial/test_read_attacks.py` and `tests/integration/test_api.py`.
