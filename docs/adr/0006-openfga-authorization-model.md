# ADR 0006 — OpenFGA authorization model v1

Status: Accepted — 2026-09-07

## Context

All rights are relationships in OpenFGA. The model must express: group hierarchies, document ACLs
with inheritance from a source, tool invocation rights, agents acting on behalf of users, and
memory provenance with *intersection* semantics.

## Decision

Types and relations, see `docs/authz-model.fga` (commented, authoritative):

- `user`
- `group#member: [user, group#member]` — nested groups.
- `agent#can_act_on_behalf_of: [user]` — an agent must be bound to a user before any check.
  `agent#delegate: [agent]` — declared structural delegation; attenuation lives in the biscuit.
- `source#viewer/editor: [user, group#member]` — a source (Nextcloud share, PipesHub connector).
- `document#parent: [source]`, `document#viewer: [user, group#member] or viewer from parent`,
  `document#editor` similarly. `viewer` also includes `editor`.
- `tool#can_invoke: [user, group#member]`.
- `memory#derived_from: [document]`, `memory#writer: [agent]`,
  `memory#viewer: viewer from derived_from` — OpenFGA computes "viewer of *some* derived document";
  the *all* semantics is enforced by the memory layer with a `BatchCheck` over every source
  document, as documented in `AGENTS.md` (provenance memory). A memory without provenance has no `viewer`.

The subject checked is always the **user** (`user:alice`), never the agent. The agent's right to ask
is the biscuit scope (ADR 0003).

## Consequences

- Any change to the model is a new ADR plus updated golden tests.
- The model is tested with `fga model test` (`docs/authz-model.fga.yaml`), run through the
  `openfga/cli` container so that no local install is required.
