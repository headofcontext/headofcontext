"""MCP integration (ADR 0013).

Two pieces, both built on :class:`AgentSession` so the decision path is the one every other
integration uses:

* :class:`GuardedMcpProxy` — an MCP server that re-exposes the tools of one or several upstream
  MCP servers, lists only the ones the subject may invoke (ADR 0030) and authorizes every call
  before forwarding it. Denials and approval requests are returned as MCP error results; the
  upstream never sees them. Only tools are exposed: resources and prompts are not forwarded,
  because the proxy cannot map an arbitrary URI to a document it could check.
* :func:`build_hoc_server` — HeadOfContext itself as an MCP server (filter, gate, redeem, recall,
  remember, whoami) for agents that speak MCP and nothing else.

Requires the optional ``mcp`` extra.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from mcp import ClientSession, types
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.stdio import stdio_server

from headofcontext.audit import AuditSink
from headofcontext.core import AuthzEngine, Outcome
from headofcontext.core.errors import ActionDenied, ConfigurationError, HocError
from headofcontext.integrations.catalog import visible_tools
from headofcontext.integrations.guard import ApprovalPending, ToolGuard, refusal_message
from headofcontext.integrations.session import AgentSession
from headofcontext.memory import MemoryService
from headofcontext.models import ApprovalModel, ChainModel, DecisionModel, MemoryModel
from headofcontext.read import filter_items

log = logging.getLogger(__name__)

REDEEM_TOOL = "hoc_redeem"
UPSTREAM_SEPARATOR = "__"
# No ``__`` in a server name, so ``<server>__<tool>`` splits on the first separator whatever
# the upstream tool is called: ``b__a__x`` is tool ``a__x`` of server ``b``, never server ``a``.
_UPSTREAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REDEEM_DESCRIPTION = (
    "Run a tool call that HeadOfContext put on hold for human approval. Pass the request id "
    "from the approval message, the tool name and exactly the same arguments as the original "
    "call. Fails until a human has approved the request."
)
_REDEEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "object"},
    },
    "required": ["request_id", "tool"],
    "additionalProperties": False,
}


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


# -- guarded proxy -------------------------------------------------------------------------------


class GuardedMcpProxy:
    """An MCP server whose tools are the upstreams', each call gated for the session's chain.

    ``upstream`` is one ``ClientSession`` (tool names verbatim) or a mapping ``name ->
    ClientSession``: tools are then exposed as ``<name>__<tool>`` and gated as
    ``tool:<name>/<tool>`` unless ``tool_map`` (keyed by exposed name) says otherwise.
    """

    def __init__(
        self,
        session: AgentSession,
        upstream: ClientSession | Mapping[str, ClientSession],
        *,
        tool_map: Mapping[str, str] | None = None,
        name: str = "headofcontext-proxy",
    ) -> None:
        if isinstance(upstream, Mapping):
            for server in upstream:
                if not _UPSTREAM_NAME.match(server) or UPSTREAM_SEPARATOR in server:
                    raise ConfigurationError(
                        f"invalid upstream name {server!r}: [a-z0-9][a-z0-9_-]*, no '__'"
                    )
            self._upstreams: dict[str | None, ClientSession] = {
                server: client for server, client in upstream.items()
            }
        else:
            self._upstreams = {None: upstream}
        self._guard = ToolGuard(
            session,
            on_deny="raise",
            tool_map=tool_map,
            default_resource=_default_resource if isinstance(upstream, Mapping) else None,
        )
        self.server: Server[Any] = Server(
            name, on_list_tools=self._list_tools, on_call_tool=self._call_tool
        )

    async def run(self, read_stream: Any, write_stream: Any) -> None:
        await self.server.run(
            read_stream, write_stream, self.server.create_initialization_options()
        )

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.run(read_stream, write_stream)

    async def _list_tools(
        self, _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        candidates: list[types.Tool] = []
        for server, client in self._upstreams.items():
            for tool in await _all_tools(client):
                exposed = tool if server is None else _prefixed(server, tool)
                # The redeem tool is ours; an upstream tool with the same name is hidden, never
                # reachable.
                if exposed.name != REDEEM_TOOL:
                    candidates.append(exposed)
        # Only what the subject may invoke reaches the model (ADR 0030). The gate still runs on
        # every call: a client may name a tool it was never shown.
        catalog = await visible_tools(
            self._guard.session, [self._guard.resource_for(t.name) for t in candidates]
        )
        tools = [
            tool
            for tool in candidates
            if catalog.outcomes.get(self._guard.resource_for(tool.name)) is not Outcome.DENY
        ]
        tools.append(
            types.Tool(
                name=REDEEM_TOOL, description=_REDEEM_DESCRIPTION, input_schema=_REDEEM_SCHEMA
            )
        )
        return types.ListToolsResult(tools=tools)

    async def _call_tool(
        self, _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        args = dict(params.arguments or {})
        if params.name == REDEEM_TOOL:
            return await self._redeem(args)
        try:
            await self._guard.authorize(params.name, args)
        except (ActionDenied, ApprovalPending) as exc:
            return _error(refusal_message(params.name, exc))
        return await self._forward(params.name, args)

    async def _redeem(self, args: dict[str, Any]) -> types.CallToolResult:
        request_id = args.get("request_id")
        tool = args.get("tool")
        tool_args = args.get("args") or {}
        if not isinstance(request_id, str) or not isinstance(tool, str) or tool == REDEEM_TOOL:
            return _error(f"HeadOfContext: '{REDEEM_TOOL}' needs a request_id and a tool name.")
        if not isinstance(tool_args, dict):
            return _error(f"HeadOfContext: '{REDEEM_TOOL}' args must be an object.")
        try:
            decision = await self._guard.session.redeem(
                request_id, self._guard.resource_for(tool), tool_args
            )
        except HocError as exc:
            return _error(f"HeadOfContext refused to run request '{request_id}': {exc.reason}.")
        if decision.outcome is not Outcome.ALLOW:
            return _error(
                f"HeadOfContext refused to run request '{request_id}': {decision.reason}."
            )
        return await self._forward(tool, tool_args)

    async def _forward(self, name: str, args: dict[str, Any]) -> types.CallToolResult:
        routed = self._route(name)
        if routed is None:
            return _error(f"HeadOfContext: unknown tool '{name}'.")
        client, tool = routed
        # Input-required flows are never enabled: the upstream cannot re-enter with arguments
        # other than the ones that were authorized.
        return await client.call_tool(tool, args)

    def _route(self, name: str) -> tuple[ClientSession, str] | None:
        if None in self._upstreams:
            return self._upstreams[None], name
        server, sep, tool = name.partition(UPSTREAM_SEPARATOR)
        if not sep or not tool or server not in self._upstreams:
            return None
        return self._upstreams[server], tool


def _default_resource(exposed: str) -> str:
    server, _, tool = exposed.partition(UPSTREAM_SEPARATOR)
    return f"{server}/{tool}"


def _prefixed(server: str, tool: types.Tool) -> types.Tool:
    return tool.model_copy(update={"name": f"{server}{UPSTREAM_SEPARATOR}{tool.name}"})


async def _all_tools(client: ClientSession) -> list[types.Tool]:
    """Every page of the upstream catalog; the proxy answers with one page."""
    tools: list[types.Tool] = []
    cursor: str | None = None
    while True:
        params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await client.list_tools(params=params)
        tools.extend(page.tools)
        if not page.next_cursor:
            return tools
        cursor = page.next_cursor


# -- HeadOfContext as an MCP server --------------------------------------------------------------


@dataclass(slots=True)
class HocMcpBinding:
    """What the server needs: one session (the biscuit + caller) and the core services."""

    session: AgentSession
    engine: AuthzEngine
    audit: AuditSink
    memory: MemoryService | None = None


_INSTRUCTIONS = (
    "HeadOfContext authorizes what this agent may read, do and remember on behalf of one user. "
    "Call hoc_filter before showing documents, hoc_gate before running a tool, hoc_redeem once "
    "an approval is granted, hoc_recall/hoc_remember for memory. Denials are final: do not retry."
)


def build_hoc_server(binding: HocMcpBinding, *, name: str = "headofcontext") -> MCPServer:
    server = MCPServer(name, instructions=_INSTRUCTIONS)
    session = binding.session

    async def hoc_whoami() -> dict[str, Any]:
        """The principal chain proven by the session token: subject, actor, scope, delegation."""
        return _dump(ChainModel.from_core(await session.chain()))

    async def hoc_filter(
        items: list[str], relation: Literal["viewer", "editor"] = "viewer"
    ) -> dict[str, Any]:
        """Keep only the document references the user may see. Call before showing any result."""
        result = await filter_items(
            await session.chain(),
            items,
            engine=binding.engine,
            audit=binding.audit,
            id_of=lambda item: item,
            relation=relation,
            permits=session.permits,
        )
        return {
            "kept": list(result.kept),
            "dropped": list(result.dropped),
            "strategy": result.strategy,
        }

    async def hoc_gate(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ask whether the user may run a tool with these arguments: ALLOW, DENY or approval."""
        result = await session.authorize(tool, args or {})
        return {
            "decision": _dump(DecisionModel.from_core(result.decision)),
            "approval": _dump(ApprovalModel.from_core(result.approval))
            if result.approval
            else None,
        }

    async def hoc_redeem(
        request_id: str, tool: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Consume an approved request. Same tool and arguments as the gated call."""
        return _dump(DecisionModel.from_core(await session.redeem(request_id, tool, args or {})))

    async def hoc_recall(query: str, limit: int = 10) -> dict[str, Any]:
        """Memories the user may still see; each source document is re-checked on every call."""
        memory = _memory(binding)
        found = await memory.recall(
            await session.chain(), query, limit=limit, permits=session.permits
        )
        return {"memories": [_dump(MemoryModel.from_core(m)) for m in found]}

    async def hoc_remember(content: str, derived_from: list[str]) -> dict[str, Any]:
        """Store a memory with its provenance; it inherits the rights of the source documents."""
        memory = _memory(binding)
        return _dump(
            MemoryModel.from_core(
                await memory.remember(
                    await session.chain(),
                    content,
                    derived_from=derived_from,
                    permits=session.permits,
                )
            )
        )

    tools: list[Callable[..., Awaitable[dict[str, Any]]]] = [
        hoc_whoami,
        hoc_filter,
        hoc_gate,
        hoc_redeem,
    ]
    if binding.memory is not None:
        tools += [hoc_recall, hoc_remember]
    for fn in tools:
        server.add_tool(_as_tool_error(fn))
    return server


def _memory(binding: HocMcpBinding) -> MemoryService:
    if binding.memory is None:  # pragma: no cover - tools are not registered without memory
        raise ToolError("memory is not configured on this server")
    return binding.memory


def _as_tool_error(
    fn: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Typed denials become MCP tool errors with the reason, never a stack trace."""

    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except HocError as exc:
            raise ToolError(f"HeadOfContext: {exc.reason}: {exc}") from exc

    return wrapped


def _dump(model: Any) -> dict[str, Any]:
    return dict(model.model_dump(mode="json"))
