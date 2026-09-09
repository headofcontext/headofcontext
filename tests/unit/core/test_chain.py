"""Invariants I1 (monotonicity), I2 (immutable subject), I3 (no orphan agent) on PrincipalChain."""

import itertools

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from headofcontext.core import (
    MAX_DELEGATION_DEPTH,
    Capability,
    Delegation,
    Kind,
    PrincipalChain,
    Scope,
)
from headofcontext.core.errors import InvariantViolation, ScopeEscalation
from tests.conftest import agent_ids, capabilities, root_chains, scopes, sub_scopes

READ_ALL = Scope.of(Capability(Kind.READ, "document:*"))
READ_HR = Scope.of(Capability(Kind.READ, "document:hr/*"))


class TestConstruction:
    def test_root_chain(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:a", READ_ALL)
        assert chain.subject == "user:alice"
        assert chain.actor == "agent:a"
        assert chain.root_actor == "agent:a"
        assert chain.depth == 0
        assert chain.scope == READ_ALL

    @pytest.mark.parametrize("subject", ["agent:a", "group:rh", "alice", "", "user:", "service:x"])
    def test_i3_subject_must_be_user(self, subject: str) -> None:
        with pytest.raises(InvariantViolation):
            PrincipalChain.root(subject, "agent:a", READ_ALL)

    @pytest.mark.parametrize("actor", ["user:alice", "a", "", "agent:"])
    def test_actor_must_be_agent(self, actor: str) -> None:
        with pytest.raises(InvariantViolation):
            PrincipalChain.root("user:alice", actor, READ_ALL)

    def test_inconsistent_links_rejected(self) -> None:
        links = (
            Delegation("agent:a", "agent:b", READ_HR),
            Delegation("agent:c", "agent:d", READ_HR),  # c != b
        )
        with pytest.raises(InvariantViolation):
            PrincipalChain("user:alice", "agent:d", links, READ_HR)

    def test_actor_must_match_last_link(self) -> None:
        links = (Delegation("agent:a", "agent:b", READ_HR),)
        with pytest.raises(InvariantViolation):
            PrincipalChain("user:alice", "agent:z", links, READ_HR)

    def test_scope_must_match_last_link(self) -> None:
        links = (Delegation("agent:a", "agent:b", READ_HR),)
        with pytest.raises(InvariantViolation):
            PrincipalChain("user:alice", "agent:b", links, READ_ALL)

    def test_widening_link_rejected_at_construction(self) -> None:
        links = (
            Delegation("agent:a", "agent:b", READ_HR),
            Delegation("agent:b", "agent:c", READ_ALL),
        )
        with pytest.raises(ScopeEscalation):
            PrincipalChain("user:alice", "agent:c", links, READ_ALL)


class TestDelegate:
    def test_delegate_narrows(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:a", READ_ALL).delegate("agent:b", READ_HR)
        assert chain.actor == "agent:b"
        assert chain.depth == 1
        assert chain.scope == READ_HR
        assert chain.delegation[0] == Delegation("agent:a", "agent:b", READ_HR)

    def test_delegate_escalation_rejected(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:a", READ_HR)
        with pytest.raises(ScopeEscalation):
            chain.delegate("agent:b", READ_ALL)

    def test_delegate_kind_change_rejected(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:a", READ_HR)
        with pytest.raises(ScopeEscalation):
            chain.delegate("agent:b", Scope.of(Capability(Kind.ACT, "document:hr/*")))

    def test_self_delegation_rejected(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:a", READ_HR)
        with pytest.raises(InvariantViolation):
            chain.delegate("agent:a", READ_HR)

    def test_depth_bounded(self) -> None:
        chain = PrincipalChain.root("user:alice", "agent:0", READ_ALL)
        for i in range(1, MAX_DELEGATION_DEPTH + 1):
            chain = chain.delegate(f"agent:{i}", READ_ALL)
        with pytest.raises(InvariantViolation):
            chain.delegate("agent:overflow", READ_ALL)

    def test_original_chain_unchanged(self) -> None:
        root = PrincipalChain.root("user:alice", "agent:a", READ_ALL)
        root.delegate("agent:b", READ_HR)
        assert root.depth == 0
        assert root.actor == "agent:a"

    @given(st.data())
    @settings(max_examples=200)
    def test_i1_monotonic_along_random_chains(self, data: st.DataObject) -> None:
        chain = data.draw(root_chains())
        steps = data.draw(st.integers(0, MAX_DELEGATION_DEPTH))
        for _ in range(steps):
            to = data.draw(agent_ids())
            assume(to != chain.actor)
            child = data.draw(sub_scopes(chain.scope))
            chain = chain.delegate(to, child)
        scopes_along = [chain.root_scope, *[d.scope for d in chain.delegation]]
        for wider, narrower in itertools.pairwise(scopes_along):
            assert narrower.is_subset_of(wider)

    @given(st.data())
    @settings(max_examples=200)
    def test_i1_escalation_always_rejected(self, data: st.DataObject) -> None:
        chain = data.draw(root_chains())
        extra = data.draw(capabilities().filter(lambda c: not chain.scope.covers(c)))
        wider = Scope(frozenset(chain.scope) | {extra})
        to = data.draw(agent_ids().filter(lambda a: a != chain.actor))
        with pytest.raises(ScopeEscalation):
            chain.delegate(to, wider)

    @given(st.data())
    def test_i2_subject_immutable(self, data: st.DataObject) -> None:
        chain = data.draw(root_chains())
        subject = chain.subject
        for _ in range(data.draw(st.integers(0, 4))):
            to = data.draw(agent_ids().filter(lambda a, c=chain: a != c.actor))
            chain = chain.delegate(to, data.draw(sub_scopes(chain.scope)))
        assert chain.subject == subject

    @given(root_chains(), scopes())
    def test_chain_is_immutable(self, chain: PrincipalChain, other: Scope) -> None:
        with pytest.raises(AttributeError):
            chain.scope = other  # type: ignore[misc]
