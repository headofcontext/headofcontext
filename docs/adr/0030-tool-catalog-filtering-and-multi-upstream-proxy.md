# ADR 0030 — Tool catalog filtering and the multi-upstream MCP proxy

Status: Accepted — 2026-09-11

## Context

The guarded proxy (ADR 0013) gates every `tools/call`, but `tools/list` forwards the upstream
catalog verbatim. The model therefore sees every tool of every server it is connected to, pays
for each description in its context, and only learns at call time that most of them are out of
reach. In an organization with one MCP server per department, an accounting user's agent carries
the logistics and communication tools in every prompt, for nothing: they will be denied.

Two things follow. A tool the subject may not invoke should not be in the catalog at all, the
way a document the subject may not view never reaches the model (ADR 0010). And a single proxy
must be able to sit in front of several upstream servers, or the filtering changes nothing for a
client that connects to each server separately.

## Decision

### 1. The catalog is filtered before the model, on both sources of truth

`tools/list` answers with the upstream tools the session's chain may invoke, and nothing else.
For each listed tool the proxy resolves the tool resource (`tool_map`, default `tool:<name>`)
and asks:

1. the biscuit, per resource, through `AgentSession.permits(Kind.ACT, resources)` (the same
   per-resource evaluation the read filter and the memory service use, ADR 0018 / ADR 0027);
   a resource the token refuses is hidden before the engine is asked;
2. the decision path of the gate, `Decider.decide_each(chain, Action.invoke(), resources)`:
   scope coverage, `can_invoke` for the **subject** in OpenFGA by BatchCheck, then the approval
   policy. `ALLOW` and `REQUIRE_APPROVAL` are visible (the tool can be asked for, possibly with
   a human in the loop); `DENY` is hidden.

Using the gate's own decider guarantees that the catalog and the gate never disagree: a visible
tool is one the gate would at least consider. An attenuated sub-agent lists fewer tools than its
parent (I1 shows in the catalog). The proxy's own `hoc_redeem` is always listed.

Fail closed (I5): an invalid or revoked token, an engine failure or a malformed tool name hides
every tool. An audit sink failure aborts the listing with an error; no catalog is ever returned
unjournaled.

The primitive is `integrations.visible_tools(session, tools) -> ToolCatalog`
(`integrations/catalog.py`), so the framework integrations can prune their tool lists the same
way later. ListObjects is not used: catalogs are small and BatchCheck is chunked by the decider;
the read filter's threshold strategy can be reused if that changes.

### 2. Visibility is not authorization

The gate still runs on every `tools/call`. A client may call a tool it was never shown, the
catalog may be stale, and the arguments are only known at call time. Hiding is a courtesy to
the context window; denying is the security function. The adversarial tests prove that a
hidden tool called by name is refused by the gate without reaching the upstream, and that
`hoc_redeem` cannot forward to a hidden tool.

### 3. Audit: one event per listing

Listing 200 tools must not write 200 decisions. Each `tools/list` journals one `tools_listed`
event, like `read_filtered`: subject, actor, depth, `act:can_invoke` on `tool:*`, outcome
`ALLOW` if anything is visible, the counts (`visible`, `hidden`, `token_refused`) and the
sha256 of the sorted visible set in the reason, the sha256 of the hidden set in `args_hash`.
Tool names are identifiers, never content; the digests let an operator prove what a session
was shown without listing it.

### 4. One proxy, several upstreams

`GuardedMcpProxy(session, upstreams)` accepts either one `ClientSession` (unchanged behaviour,
tool names verbatim) or a mapping `name -> ClientSession`. With a mapping, every tool is exposed
as `<name>__<tool>` and resolves by default to `tool:<name>/<tool>`, so scopes and tuples can
target a whole server (`tool:finance/*`) or one tool (`tool:finance/report`). Calls are routed
on the first `__`; upstream names are `[a-z0-9][a-z0-9_-]*` and may not contain `__`, so the
split is unambiguous whatever the upstream tool is called: a tool `a__x` on server `b` is
`b__a__x`, resource `tool:b/a__x`, and can never be mistaken for server `a`. A name that
matches no upstream is refused, never forwarded. `tool_map` keys are the exposed names.

Each `tools/list` fetches every page of every upstream and returns one page; the proxy does
not paginate.

CLI: `hoc mcp proxy --upstream NAME=TARGET` repeated, where `TARGET` is a streamable HTTP URL
when it starts with `http://` or `https://` and a stdio command line otherwise. The single-server
flags `--upstream-stdio` / `--upstream-http` stay as they are.

### 5. Golden set

ACME gains a tool oracle: for every user, the tools they may invoke and the ones they may not,
derived from `TOOLS` and the group hierarchy. The golden suite checks it at the engine level
and through a multi-upstream proxy over the real OpenFGA store, one session per user: the
catalog must equal the oracle for all 50 users, no leak, nothing missing.

## Not decided here

- Pushing `notifications/tools/list_changed` on revocation, so a client drops tools without
  re-listing. The SDK supports it; it needs a refresh trigger and is a follow-up.
- A `mcp_server` type in the OpenFGA model with inheritance to `tool`, to grant a whole server
  with one tuple. Prefix scopes and `group#member` tuples cover the need for now.
- Progressive disclosure (`hoc_search_tools`): ergonomics, not authorization.
- A configuration file for upstreams; the repeated flag is enough until a design partner asks.

## Consequences

- Tests: unit (catalog, proxy listing, routing), adversarial (hidden tool called by name,
  redeem to a hidden tool, name squatting across upstreams, engine and token failures), golden
  (oracle at the engine and through the proxy).
- `EventKind.TOOLS_LISTED` is new; the journal schema is unchanged (kinds are strings).
- The programmatic proxy keeps its single-upstream signature; the mapping form is additive.
