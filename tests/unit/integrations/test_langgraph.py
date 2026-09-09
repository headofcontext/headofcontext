import pytest

from headofcontext.integrations import AgentSession

langchain_core = pytest.importorskip("langchain_core")
from langchain_core.tools import tool  # noqa: E402

from headofcontext.integrations.langgraph import guard_tools  # noqa: E402


@tool
def mail_send(to: str, body: str) -> str:
    """Send an email."""
    return f"sent to {to}"


@tool
def hr_export(year: int) -> str:
    """Export HR data."""
    return f"exported {year}"


@tool
def payment_send(amount: int) -> str:
    """Send a payment."""
    return f"paid {amount}"


def test_guarded_tools_keep_name_and_schema(session: AgentSession) -> None:
    [guarded] = guard_tools([mail_send], session, tool_map={"mail_send": "mail.send"})
    assert guarded.name == "mail_send"
    assert guarded.args == mail_send.args
    assert guarded.description == mail_send.description


def test_sync_invoke_allow_and_deny(session: AgentSession) -> None:
    mail, finance = guard_tools(
        [mail_send, hr_export],
        session,
        tool_map={"mail_send": "mail.send", "hr_export": "finance.report"},
    )
    assert mail.invoke({"to": "bob@acme.example", "body": "hi"}) == "sent to bob@acme.example"
    out = finance.invoke({"year": 2026})
    assert out.startswith("HeadOfContext denied") and "exported" not in out


async def test_async_invoke(session: AgentSession) -> None:
    [mail] = guard_tools([mail_send], session, tool_map={"mail_send": "mail.send"})
    assert (
        await mail.ainvoke({"to": "bob@acme.example", "body": "hi"}) == "sent to bob@acme.example"
    )


async def test_pending_message(session: AgentSession) -> None:
    [pay] = guard_tools([payment_send], session, tool_map={"payment_send": "payment.send"})
    out = await pay.ainvoke({"amount": 10})
    assert "requires approval" in out


async def test_raise_mode(session: AgentSession) -> None:
    from headofcontext.core.errors import ActionDenied

    [finance] = guard_tools(
        [hr_export], session, on_deny="raise", tool_map={"hr_export": "finance.report"}
    )
    with pytest.raises(ActionDenied):
        await finance.ainvoke({"year": 2026})


def test_tool_node_runs_guarded_tools(session: AgentSession) -> None:
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode

    tools = guard_tools(
        [mail_send, hr_export],
        session,
        tool_map={"mail_send": "mail.send", "hr_export": "finance.report"},
    )
    from langgraph.graph import END, START, MessagesState, StateGraph

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "mail_send", "args": {"to": "bob@acme.example", "body": "hi"}, "id": "c1"},
            {"name": "hr_export", "args": {"year": 2026}, "id": "c2"},
        ],
    )
    result = app.invoke({"messages": [message]})
    outputs = {m.tool_call_id: m.content for m in result["messages"] if hasattr(m, "tool_call_id")}
    assert outputs["c1"] == "sent to bob@acme.example"
    assert outputs["c2"].startswith("HeadOfContext denied")


def test_rejects_non_tools(session: AgentSession) -> None:
    with pytest.raises(TypeError):
        guard_tools([lambda: None], session)
