"""Hypothesis properties for I4 (revocation) and I5 (fail closed) on real components."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from headofcontext.actions import ActionGate, InMemoryApprovalStore
from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, Outcome, PrincipalChain, Scope
from headofcontext.core.errors import EngineUnavailable, TokenRevoked
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

ROOT_SCOPE = Scope.of(Capability(Kind.ACT, "tool:*"))


def _chain_of_tokens(depth: int) -> tuple[TokenService, list[str], list[str]]:
    clock = FrozenClock()
    tokens = TokenService(
        KeyRing.generate(1),
        InMemoryRevocationStore(),
        InMemoryAuditSink(),
        clock,
        ttl=timedelta(hours=1),
    )
    issued = tokens.issue(PrincipalChain.root("user:alice", "agent:a0", ROOT_SCOPE))
    chain_tokens, actors = [issued.token], ["agent:a0"]
    for i in range(1, depth + 1):
        actor = f"agent:a{i}"
        issued = tokens.attenuate(chain_tokens[-1], to_actor=actor, scope=ROOT_SCOPE)
        chain_tokens.append(issued.token)
        actors.append(actor)
    return tokens, chain_tokens, actors


@settings(max_examples=60, deadline=None)
@given(data=st.data())
def test_i4_revoking_a_link_kills_everything_downstream_and_nothing_upstream(
    data: st.DataObject,
) -> None:
    depth = data.draw(st.integers(min_value=1, max_value=5))
    revoked = data.draw(st.integers(min_value=0, max_value=depth))
    tokens, chain_tokens, actors = _chain_of_tokens(depth)
    tokens.revoke(chain_tokens[revoked], caller=actors[revoked], reason="property")
    for i, (token, actor) in enumerate(zip(chain_tokens, actors, strict=True)):
        try:
            tokens.verify(token, caller=actor, operation=Kind.ACT, resource="tool:mail.send")
            alive = True
        except TokenRevoked:
            alive = False
        assert alive == (i < revoked), f"token {i} alive={alive} with link {revoked} revoked"


class FlakyGraph(FakeGraph):
    """An engine that fails on a chosen subset of calls."""

    def __init__(self, failing: set[int]) -> None:
        super().__init__()
        self.failing = failing
        self.calls = 0

    async def check(self, subject: str, relation: str, obj: str) -> bool:
        self.calls += 1
        if self.calls in self.failing:
            raise EngineUnavailable("openfga down")
        return await super().check(subject, relation, obj)


@settings(max_examples=60, deadline=None)
@given(
    failing=st.sets(st.integers(min_value=1, max_value=8), max_size=8),
    tools=st.lists(st.sampled_from(["tool:mail.send", "tool:hr.export"]), min_size=1, max_size=8),
)
async def test_i5_every_engine_failure_is_a_journaled_deny(
    failing: set[int], tools: list[str]
) -> None:
    graph = FlakyGraph(failing)
    graph.grant("user:alice", "can_invoke", "tool:mail.send")
    graph.grant("user:alice", "can_invoke", "tool:hr.export")
    audit = InMemoryAuditSink()
    gate = ActionGate(Decider(graph, audit, FrozenClock()), InMemoryApprovalStore(), audit)
    chain = PrincipalChain.root("user:alice", "agent:a", ROOT_SCOPE)
    for call, tool in enumerate(tools, start=1):
        result = await gate.gate(chain, tool, {})
        expected = Outcome.DENY if call in failing else Outcome.ALLOW
        assert result.decision.outcome is expected
        if call in failing:
            assert result.decision.reason == "engine_unavailable"
    decisions = [e for e in audit.events if e.kind is EventKind.DECISION]
    assert len(decisions) == len(tools)  # nothing returned unlogged, failures included
