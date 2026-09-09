"""AgentSession: biscuit verification + gate on every call, delegation, revocation (ADR 0008)."""

import pytest

from headofcontext.actions import ActionGate
from headofcontext.core import Outcome
from headofcontext.core.errors import ActionDenied, ScopeEscalation
from headofcontext.integrations import AgentSession
from headofcontext.tokens.biscuit import TokenService
from tests.conftest import FrozenClock
from tests.unit.integrations.conftest import MAIL_ONLY
from tests.unit.memory.conftest import FakeGraph


async def test_allow_through_token_and_gate(session: AgentSession) -> None:
    result = await session.authorize("mail.send", {"to": "bob@acme.example"})
    assert result.allowed
    assert result.decision.chain.subject == "user:alice"
    assert result.decision.resource == "tool:mail.send"


async def test_tool_name_is_normalized(session: AgentSession) -> None:
    assert (await session.authorize("tool:mail.send", {})).allowed
    with pytest.raises(ActionDenied):
        await session.authorize('mail.send"; drop', {})


async def test_chain_from_token(session: AgentSession) -> None:
    chain = await session.chain()
    assert chain.subject == "user:alice" and chain.actor == "agent:assistant" and chain.depth == 0


async def test_wrong_caller_is_denied_and_audited(
    session: AgentSession, token_service: TokenService, gate: ActionGate
) -> None:
    from headofcontext.audit import InMemoryAuditSink

    impostor = AgentSession(
        token=session.token, caller="agent:mallory", token_service=token_service, gate=gate
    )
    with pytest.raises(ActionDenied) as exc:
        await impostor.authorize("mail.send", {})
    assert exc.value.reason == "token_invalid"
    audit = gate._audit
    assert isinstance(audit, InMemoryAuditSink)
    assert audit.events[-1].reason == "token_invalid" and audit.events[-1].actor == "agent:mallory"


async def test_delegation_narrows_at_both_layers(session: AgentSession) -> None:
    mailer = session.delegate("agent:mailer", MAIL_ONLY)
    assert mailer.caller == "agent:mailer"
    assert (await mailer.authorize("mail.send", {})).allowed
    with pytest.raises(ActionDenied) as exc:
        await mailer.authorize("hr.export", {})
    assert exc.value.reason == "token_invalid"  # the biscuit check fails before the gate runs
    assert (await session.authorize("hr.export", {})).allowed


async def test_delegation_cannot_widen(session: AgentSession) -> None:
    mailer = session.delegate("agent:mailer", MAIL_ONLY)
    with pytest.raises(ScopeEscalation):
        mailer.delegate("agent:sub", (await session.chain()).scope)


async def test_multi_hop_and_revocation(session: AgentSession, token_service: TokenService) -> None:
    mailer = session.delegate("agent:mailer", MAIL_ONLY)
    sub = mailer.delegate("agent:sub", MAIL_ONLY)
    assert (await sub.authorize("mail.send", {})).allowed
    assert (await sub.chain()).depth == 2 and (await sub.chain()).root_actor == "agent:assistant"
    # Revoke the mailer hop: sub goes dark, the root keeps working.
    token_service.revoke_id(mailer.revocation_ids[-1], reason="mailer compromised")
    with pytest.raises(ActionDenied) as exc:
        await sub.authorize("mail.send", {})
    assert exc.value.reason == "token_revoked"
    with pytest.raises(ActionDenied):
        await mailer.authorize("mail.send", {})
    assert (await session.authorize("mail.send", {})).allowed


async def test_expiry_takes_effect_on_next_call(session: AgentSession, clock: FrozenClock) -> None:
    assert (await session.authorize("mail.send", {})).allowed
    clock.tick(hours=2)
    with pytest.raises(ActionDenied):
        await session.authorize("mail.send", {})


async def test_tuple_removal_takes_effect_on_next_call(
    session: AgentSession, graph: FakeGraph
) -> None:
    assert (await session.authorize("mail.send", {})).allowed
    graph.revoke("user:alice", "can_invoke", "tool:mail.send")
    result = await session.authorize("mail.send", {})
    assert result.decision.outcome is Outcome.DENY


async def test_approval_roundtrip_through_session(session: AgentSession, gate: ActionGate) -> None:
    result = await session.authorize("payment.send", {"amount": 10})
    assert result.pending and result.approval is not None
    await gate.resolve(result.approval.request_id, approver="user:carol", approved=True)
    decision = await session.redeem(result.approval.request_id, "payment.send", {"amount": 10})
    assert decision.outcome is Outcome.ALLOW
