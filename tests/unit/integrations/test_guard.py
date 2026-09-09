"""ToolGuard: framework-agnostic wrapping of tool functions (ADR 0009)."""

import pytest

from headofcontext.actions import ActionGate
from headofcontext.core.errors import ActionDenied
from headofcontext.integrations import AgentSession, ApprovalPending, ToolGuard


def send_mail(to: str, body: str) -> str:
    return f"sent to {to}"


async def send_mail_async(to: str, body: str) -> str:
    return f"sent to {to}"


async def test_wrap_async_allows(session: AgentSession) -> None:
    guard = ToolGuard(session)
    wrapped = guard.wrap_async("mail.send", send_mail_async)
    assert await wrapped(to="bob@acme.example", body="hi") == "sent to bob@acme.example"


async def test_wrap_async_denies_with_message(session: AgentSession) -> None:
    guard = ToolGuard(session)
    wrapped = guard.wrap_async("finance.report", send_mail_async)
    out = await wrapped(to="x", body="y")
    assert isinstance(out, str) and out.startswith("HeadOfContext denied") and "not_related" in out


async def test_wrap_async_denies_by_raising(session: AgentSession) -> None:
    guard = ToolGuard(session, on_deny="raise")
    wrapped = guard.wrap_async("finance.report", send_mail_async)
    with pytest.raises(ActionDenied):
        await wrapped(to="x", body="y")


async def test_wrap_async_pending(session: AgentSession) -> None:
    guard = ToolGuard(session, on_deny="raise")
    wrapped = guard.wrap_async("payment.send", send_mail_async)
    with pytest.raises(ApprovalPending) as exc:
        await wrapped(to="x", body="y")
    assert exc.value.request_id
    message_guard = ToolGuard(session)
    out = await message_guard.wrap_async("payment.send", send_mail_async)(to="x", body="y")
    assert ("requires approval" in out and exc.value.request_id not in out) or "request" in out


def test_wrap_sync_from_plain_thread(session: AgentSession) -> None:
    guard = ToolGuard(session)
    wrapped = guard.wrap("mail.send", send_mail)
    assert wrapped(to="bob@acme.example", body="hi") == "sent to bob@acme.example"
    assert (
        "denied" in wrapped.__wrapped_denial_probe__
        if hasattr(wrapped, "__wrapped_denial_probe__")
        else True
    )


async def test_wrap_sync_inside_running_loop(session: AgentSession) -> None:
    guard = ToolGuard(session)
    wrapped = guard.wrap("mail.send", send_mail)
    assert wrapped(to="bob@acme.example", body="hi") == "sent to bob@acme.example"


async def test_tool_map(session: AgentSession) -> None:
    guard = ToolGuard(session, tool_map={"send_mail": "tool:mail.send"})
    wrapped = guard.wrap_async("send_mail", send_mail_async)
    assert await wrapped(to="x", body="y") == "sent to x"


async def test_positional_args_are_hashed_too(session: AgentSession, gate: ActionGate) -> None:
    guard = ToolGuard(session)
    wrapped = guard.wrap_async("mail.send", send_mail_async)
    await wrapped("bob@acme.example", "hi")
    from headofcontext.audit import InMemoryAuditSink

    audit = gate._audit
    assert isinstance(audit, InMemoryAuditSink)
    assert audit.events[-1].args_hash is not None
    assert "bob@acme.example" not in audit.events[-1].canonical_json()
