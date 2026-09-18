"""Tool oracle of the golden set (ADR 0030): for every ACME user, OpenFGA must agree with the
fixture on `can_invoke`, and a proxy fronting one MCP server per service must list exactly the
tools the user may invoke. Real OpenFGA store, in-memory MCP streams."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, types
from mcp.server.lowlevel import Server

from headofcontext.actions import ActionGate, InMemoryApprovalStore
from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Decider
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.integrations import AgentSession
from headofcontext.integrations.mcp import REDEEM_TOOL, GuardedMcpProxy
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from tests.conftest import FrozenClock
from tests.golden.conftest import GOLDEN_TOOLS, GoldenToolCase
from tests.services import FgaStore
from tests.unit.integrations.test_mcp import connected

pytestmark = [pytest.mark.golden, pytest.mark.integration]

ALL_TOOLS = sorted({t for c in GOLDEN_TOOLS for t in (*c.expected_invocable, *c.expected_denied)})


def _service_and_tool(resource: str) -> tuple[str, str]:
    """``tool:hr.export`` is tool ``export`` of the ``hr`` MCP server."""
    service, _, tool = resource.removeprefix("tool:").partition(".")
    return service, tool


SERVICES: dict[str, list[str]] = {}
for _resource in ALL_TOOLS:
    _service, _tool = _service_and_tool(_resource)
    SERVICES.setdefault(_service, []).append(_tool)
# Exposed name -> ACME resource, since the fixtures predate the tool:<server>/<tool> default.
TOOL_MAP = {
    f"{_service_and_tool(r)[0]}__{_service_and_tool(r)[1]}": r.removeprefix("tool:")
    for r in ALL_TOOLS
}


def static_server(name: str, tools: list[str]) -> Server[Any]:
    async def list_tools(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=t, description=f"{name} {t}", input_schema={"type": "object"})
                for t in tools
            ]
        )

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=params.name)])

    return Server(name, on_list_tools=list_tools, on_call_tool=call_tool)


@pytest.fixture
async def engine(openfga_store: FgaStore) -> AsyncIterator[OpenFgaEngine]:
    engine = OpenFgaEngine.connect(
        openfga_store.url,
        openfga_store.store_id,
        AlwaysFresh(),
        authorization_model_id=openfga_store.model_id,
    )
    yield engine
    await engine.close()


@pytest.fixture
def gate(engine: OpenFgaEngine) -> ActionGate:
    audit = InMemoryAuditSink()
    clock = FrozenClock()
    return ActionGate(Decider(engine, audit, clock), InMemoryApprovalStore(), audit, clock)


@pytest.fixture
def token_service() -> TokenService:
    return TokenService(
        KeyRing.generate(1),
        InMemoryRevocationStore(),
        InMemoryAuditSink(),
        FrozenClock(),
        ttl=timedelta(hours=1),
    )


async def test_engine_matches_tool_oracle(engine: OpenFgaEngine, tool_case: GoldenToolCase) -> None:
    tools = [*tool_case.expected_invocable, *tool_case.expected_denied]
    results = await engine.batch_check(tool_case.subject, [("can_invoke", t) for t in tools])
    granted = {t for t, ok in zip(tools, results, strict=True) if ok}
    leaked = sorted(granted & set(tool_case.expected_denied))
    missing = sorted(set(tool_case.expected_invocable) - granted)
    assert leaked == [], f"{tool_case.subject} must not invoke {leaked}"
    assert missing == [], f"{tool_case.subject} should invoke {missing}"


async def test_proxy_catalog_matches_tool_oracle(
    gate: ActionGate, token_service: TokenService, tool_case: GoldenToolCase
) -> None:
    session = AgentSession(
        token=token_service.issue(tool_case.chain()).token,
        caller=tool_case.actor,
        token_service=token_service,
        gate=gate,
    )
    async with AsyncExitStack() as stack:
        upstreams: dict[str, ClientSession] = {
            service: await stack.enter_async_context(connected(static_server(service, tools)))
            for service, tools in SERVICES.items()
        }
        proxy = GuardedMcpProxy(session, upstreams, tool_map=TOOL_MAP)
        client = await stack.enter_async_context(connected(proxy.server))
        listed = {t.name for t in (await client.list_tools()).tools} - {REDEEM_TOOL}
    expected = {
        f"{_service_and_tool(r)[0]}__{_service_and_tool(r)[1]}"
        for r in tool_case.expected_invocable
    }
    assert listed == expected, f"{tool_case.subject}: listed {sorted(listed)}"
