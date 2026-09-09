# ADR 0013 — MCP integration

Status: Accepted — 2026-09-07

## Context

Agents increasingly reach their tools through the Model Context Protocol. Two things follow:
a tool exposed over MCP must be gated like any other tool, and HeadOfContext's own operations
(filter, gate, recall, remember) should be callable from an MCP-native agent without an SDK.
ADR 0011 left MCP auth as a later step.

## Decision

Both pieces use the official `mcp` package (2.x) as an optional extra (`mcp`) and the in-process
`AgentSession` (ADR 0008), so they inherit biscuit verification and the decision path unchanged.

### 1. Guarded proxy: `hoc mcp proxy`

A stdio MCP server that connects to an upstream MCP server (stdio command or streamable HTTP
URL), re-exposes its tools verbatim (name, description, input schema), and on every `call_tool`
runs `AgentSession.authorize(name, arguments)` before forwarding. A DENY or an approval
requirement comes back as an MCP error result (`isError: true`, with the same wording the other
integrations use: `HeadOfContext denied tool '<name>': <reason>` / `requires approval (request
<id>)`); the upstream never sees the call.

The proxy adds one tool of its own, `hoc_redeem(request_id, tool, args)`: once a human approved
the request, the agent calls it with the same tool and arguments and the proxy redeems the
request then forwards. An upstream tool named `hoc_redeem` is hidden and unreachable.

Only tools are exposed. Resources and prompts are not forwarded: the proxy cannot map an
arbitrary URI to a document it could check, and default deny wins over convenience. Document
reads go through `filter_items` (ADR 0010) upstream of the model. Input-required tool flows are
not enabled either: the upstream cannot re-enter with arguments other than the ones authorized.

Programmatic form: `headofcontext.integrations.mcp.GuardedMcpProxy(session, upstream,
tool_map=...)`, which exposes a low-level `mcp` `Server` runnable over any transport.

### 2. HeadOfContext as an MCP server: `hoc mcp serve`

An `MCPServer` exposing `hoc_whoami()`, `hoc_filter(items, relation)`, `hoc_gate(tool, args)`,
`hoc_redeem(request_id, tool, args)`, `hoc_recall(query, limit)` and `hoc_remember(content,
derived_from)`, all bound to one `AgentSession` given at start. The memory tools are only
registered when a memory service is configured. Tools return the JSON shapes of the HTTP
contract (ADR 0011); typed denials become MCP tool errors carrying the reason. Transport: stdio
by default, streamable HTTP with `--http`.

Programmatic form: `build_hoc_server(HocMcpBinding(session, engine, audit, memory))`.

### Identity

MCP has no user identity of its own here. Both commands run in-process next to the agent, like
the LangGraph and CrewAI integrations: the biscuit (`HOC_MCP_TOKEN`) proves the chain and the
caller (`HOC_MCP_AGENT`, `agent:<id>`) is configuration. The biscuit is bound to its holder, so a
wrong caller fails verification rather than acting for someone else. They connect to OpenFGA and
Postgres directly through the service settings (`HOC_*`, same as `hoc sync`).

Per-request MCP authorization (OAuth resource server on the streamable HTTP transport, one
session per user) is a follow-up once the `mcp` auth API settles; until then a proxy or server
instance serves exactly one user and must not be shared.

## Consequences

- Tests use the in-memory client/server streams of the SDK; no subprocess, no network.
- The `mcp` extra pulls `starlette`/`httpx`/`anyio`, already present through FastAPI.
