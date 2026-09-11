"""AgentSession.visible_tools: the tool catalog a session may be shown (ADR 0030)."""

from __future__ import annotations

import pytest

from headofcontext.actions import ActionGate
from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Kind, Outcome, PrincipalChain, Scope
from headofcontext.core.errors import AuditUnavailable
from headofcontext.core.events import AuditEvent
from headofcontext.integrations import AgentSession, visible_tools
from headofcontext.tokens.biscuit import TokenService
from tests.unit.integrations.conftest import MAIL_ONLY
from tests.unit.memory.conftest import FakeGraph

TOOLS = ["tool:mail.send", "tool:hr.export", "tool:payment.send", "tool:crm.export"]


async def test_visible_is_what_the_subject_may_invoke(
    session: AgentSession, audit: InMemoryAuditSink
) -> None:
    catalog = await visible_tools(session, TOOLS)
    assert catalog.visible == ["tool:mail.send", "tool:hr.export", "tool:payment.send"]
    assert catalog.hidden == ["tool:crm.export"]
    assert catalog.outcomes["tool:payment.send"] is Outcome.REQUIRE_APPROVAL
    assert catalog.outcomes["tool:crm.export"] is Outcome.DENY


async def test_names_are_accepted_as_given_and_kept_in_order(session: AgentSession) -> None:
    catalog = await visible_tools(session, ["hr.export", "mail.send", "hr.export"])
    assert catalog.visible == ["hr.export", "mail.send", "hr.export"]


async def test_invalid_names_are_hidden_never_sanitized(session: AgentSession) -> None:
    catalog = await visible_tools(session, ["mail.send", "bad name!", "", "tool:"])
    assert catalog.visible == ["mail.send"]
    assert catalog.hidden == ["bad name!", "", "tool:"]


async def test_scope_hides_before_the_engine_is_asked(
    token_service: TokenService, gate: ActionGate, graph: FakeGraph
) -> None:
    narrow = token_service.issue(
        PrincipalChain.root("user:alice", "agent:assistant", MAIL_ONLY)
    ).token
    session = AgentSession(
        token=narrow, caller="agent:assistant", token_service=token_service, gate=gate
    )
    catalog = await visible_tools(session, TOOLS)
    assert catalog.visible == ["tool:mail.send"]
    assert catalog.token_refused == 3


async def test_sub_agent_sees_fewer_tools_than_its_parent(session: AgentSession) -> None:
    child = session.delegate("agent:sub", Scope.of(Capability(Kind.ACT, "tool:hr.*")))
    assert (await visible_tools(session, TOOLS)).visible == [
        "tool:mail.send",
        "tool:hr.export",
        "tool:payment.send",
    ]
    assert (await visible_tools(child, TOOLS)).visible == ["tool:hr.export"]


async def test_one_event_per_listing_with_counts_and_digests(
    session: AgentSession, audit: InMemoryAuditSink
) -> None:
    before = len(audit.events)
    await visible_tools(session, TOOLS)
    events = audit.events[before:]
    assert [e.kind for e in events] == [EventKind.TOOLS_LISTED]
    event = events[0]
    assert event.subject == "user:alice" and event.actor == "agent:assistant"
    assert event.action == "act:can_invoke" and event.resource == "tool:*"
    assert event.outcome == "ALLOW"
    assert "visible=3" in event.reason and "hidden=1" in event.reason
    assert "visible_sha256=" in event.reason and event.args_hash is not None


async def test_nothing_visible_is_a_deny_event(
    session: AgentSession, audit: InMemoryAuditSink
) -> None:
    catalog = await visible_tools(session, ["tool:crm.export"])
    assert catalog.visible == []
    assert audit.events[-1].outcome == "DENY"


async def test_empty_input_lists_nothing_and_journals_nothing(
    session: AgentSession, audit: InMemoryAuditSink
) -> None:
    before = len(audit.events)
    catalog = await visible_tools(session, [])
    assert catalog.visible == [] and catalog.hidden == []
    assert len(audit.events) == before


async def test_engine_down_hides_everything(session: AgentSession, graph: FakeGraph) -> None:
    graph.down = True
    catalog = await visible_tools(session, TOOLS)
    assert catalog.visible == [] and catalog.hidden == TOOLS


async def test_revoked_token_hides_everything_and_is_journaled(
    session: AgentSession, token_service: TokenService, audit: InMemoryAuditSink
) -> None:
    ids = token_service.inspect(session.token, caller=session.caller).revocation_ids
    token_service.revoke_id(ids[0], reason="test")
    before = len(audit.events)
    catalog = await visible_tools(session, TOOLS)
    assert catalog.visible == [] and catalog.hidden == TOOLS
    event = audit.events[-1]
    assert len(audit.events) == before + 1
    assert event.kind is EventKind.TOOLS_LISTED and event.outcome == "DENY"
    assert event.subject == "-" and event.actor == "agent:assistant"


class DownSink(InMemoryAuditSink):
    def record(self, event: AuditEvent) -> None:
        raise AuditUnavailable("journal down")


async def test_audit_down_returns_no_catalog(token_service: TokenService, graph: FakeGraph) -> None:
    from headofcontext.actions import InMemoryApprovalStore
    from headofcontext.core import Decider
    from tests.conftest import FrozenClock
    from tests.unit.integrations.conftest import ALICE

    clock = FrozenClock()
    sink = DownSink()
    gate = ActionGate(Decider(graph, sink, clock), InMemoryApprovalStore(), sink, clock)
    session = AgentSession(
        token=token_service.issue(ALICE).token,
        caller="agent:assistant",
        token_service=token_service,
        gate=gate,
    )
    with pytest.raises(AuditUnavailable):
        await visible_tools(session, TOOLS)
