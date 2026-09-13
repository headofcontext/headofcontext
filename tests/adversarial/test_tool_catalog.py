"""Tool catalog attacks (ADR 0030): hiding a tool is never what protects it.

A client that ignores the catalog and calls hidden tools by name, redeems into them, or lists
while the engine or the token is broken must get nothing, and the upstream must never be
reached."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from headofcontext.actions import ActionGate
from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.integrations import AgentSession
from headofcontext.integrations.mcp import REDEEM_TOOL
from headofcontext.tokens.biscuit import TokenService
from tests.unit.integrations import conftest as fixtures
from tests.unit.integrations.test_mcp import proxied, text_of
from tests.unit.memory.conftest import FakeGraph

pytestmark = pytest.mark.adversarial

# The token + gate composition of the integration unit tests, registered under this module.
audit = fixtures.audit
gate = fixtures.gate
graph = fixtures.graph
session = fixtures.session
token_service = fixtures.token_service


async def test_hidden_tool_called_by_name_is_denied_before_upstream(
    session: AgentSession,
) -> None:
    async with proxied(session, []) as (client, calls):
        listed = {t.name for t in (await client.list_tools()).tools}
        assert "hr_export" not in listed
        result = await client.call_tool("hr_export", {"year": 2026})
        assert result.is_error and "denied" in text_of(result)
        assert calls == []


async def test_redeem_cannot_reach_a_hidden_tool(
    session: AgentSession, gate: ActionGate, graph: FakeGraph
) -> None:
    # An approval granted while the tool was invocable does not survive losing the right.
    async with proxied(session, []) as (client, calls):
        pending = await client.call_tool("payment_send", {"amount": 10})
        request_id = text_of(pending).split("request ")[1].split(")")[0]
        await gate.resolve(request_id, approver="user:bob", approved=True, reason="ok")
        graph.revoke("user:alice", "can_invoke", "tool:payment.send")
        assert "payment_send" not in {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool(
            REDEEM_TOOL, {"request_id": request_id, "tool": "payment_send", "args": {"amount": 10}}
        )
        assert result.is_error and calls == []


async def test_engine_down_lists_nothing_and_allows_nothing(
    session: AgentSession, graph: FakeGraph, audit: InMemoryAuditSink
) -> None:
    async with proxied(session, []) as (client, calls):
        graph.down = True
        assert {t.name for t in (await client.list_tools()).tools} == {REDEEM_TOOL}
        result = await client.call_tool("mail_send", {"to": "x", "body": "y"})
        assert result.is_error and calls == []
        listed = [e for e in audit.events if e.kind is EventKind.TOOLS_LISTED]
        assert listed and listed[-1].outcome == "DENY"


async def test_revoked_token_lists_nothing(
    session: AgentSession, token_service: TokenService, audit: InMemoryAuditSink
) -> None:
    async with proxied(session, []) as (client, calls):
        ids = token_service.inspect(session.token, caller=session.caller).revocation_ids
        token_service.revoke_id(ids[0], reason="stolen")
        assert {t.name for t in (await client.list_tools()).tools} == {REDEEM_TOOL}
        assert (await client.call_tool("mail_send", {"to": "x", "body": "y"})).is_error
        assert calls == []
        listed = [e for e in audit.events if e.kind is EventKind.TOOLS_LISTED]
        assert listed[-1].outcome == "DENY" and listed[-1].reason == "token_revoked"


async def test_delegated_session_cannot_list_more_than_its_parent(
    session: AgentSession,
) -> None:
    from headofcontext.core import Capability, Kind, Scope

    child = session.delegate("agent:sub", Scope.of(Capability(Kind.ACT, "tool:mail.*")))
    async with proxied(child, []) as (client, calls):
        assert {t.name for t in (await client.list_tools()).tools} == {"mail_send", REDEEM_TOOL}
        assert (await client.call_tool("payment_send", {"amount": 1})).is_error
        assert calls == []
