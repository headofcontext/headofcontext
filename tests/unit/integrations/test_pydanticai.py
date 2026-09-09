"""PydanticAI integration (ADR 0014): every tool call goes through the guard first."""

import pytest

from headofcontext.integrations import AgentSession

pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from headofcontext.core.errors import ActionDenied
from headofcontext.integrations.pydanticai import guard_toolset

TOOL_MAP = {"mail_send": "mail.send", "hr_export": "finance.report", "payment_send": "payment.send"}


def make_toolset(calls: list[str]) -> FunctionToolset[None]:
    toolset: FunctionToolset[None] = FunctionToolset()

    def mail_send(to: str, body: str) -> str:
        """Send an email."""
        calls.append("mail_send")
        return f"sent to {to}"

    def hr_export(year: int) -> str:
        """Export HR data."""
        calls.append("hr_export")
        return f"exported {year}"

    def payment_send(amount: int) -> str:
        """Send a payment."""
        calls.append("payment_send")
        return f"paid {amount}"

    # 2.x: `@toolset.tool` is for context-taking tools; plain functions go through add_function.
    for fn in (mail_send, hr_export, payment_send):
        toolset.add_function(fn)
    return toolset


def tool_returns(agent: Agent[None, str], tool: str) -> list[str]:
    result = agent.run_sync("go")
    return [
        str(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool
    ]


def test_allowed_tool_runs(session: AgentSession) -> None:
    calls: list[str] = []
    agent: Agent[None, str] = Agent(
        TestModel(call_tools=["mail_send"]),
        toolsets=[guard_toolset(make_toolset(calls), session, tool_map=TOOL_MAP)],
    )
    [out] = tool_returns(agent, "mail_send")
    # TestModel filled `to` from the pass-through schema, so the definition survived the wrap.
    assert out.startswith("sent to ") and len(out) > len("sent to ")
    assert calls == ["mail_send"]


def test_denied_tool_returns_message_and_never_runs(session: AgentSession) -> None:
    calls: list[str] = []
    agent: Agent[None, str] = Agent(
        TestModel(call_tools=["hr_export"]),
        toolsets=[guard_toolset(make_toolset(calls), session, tool_map=TOOL_MAP)],
    )
    [out] = tool_returns(agent, "hr_export")
    assert out.startswith("HeadOfContext denied tool 'hr_export'") and calls == []


def test_pending_tool_returns_approval_message(session: AgentSession) -> None:
    calls: list[str] = []
    agent: Agent[None, str] = Agent(
        TestModel(call_tools=["payment_send"]),
        toolsets=[guard_toolset(make_toolset(calls), session, tool_map=TOOL_MAP)],
    )
    [out] = tool_returns(agent, "payment_send")
    assert "requires approval (request " in out and calls == []


def test_raise_mode(session: AgentSession) -> None:
    calls: list[str] = []
    agent: Agent[None, str] = Agent(
        TestModel(call_tools=["hr_export"]),
        toolsets=[guard_toolset(make_toolset(calls), session, tool_map=TOOL_MAP, on_deny="raise")],
    )
    with pytest.raises(ActionDenied):
        agent.run_sync("go")
    assert calls == []
