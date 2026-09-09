"""T2 escalation by delegation, T6 orphan agent — every attack must be blocked."""

import dataclasses

import pytest

from headofcontext.core import Capability, Delegation, Kind, PrincipalChain, Scope
from headofcontext.core.errors import InvariantViolation, ScopeEscalation

pytestmark = pytest.mark.adversarial

READ_HR = Scope.of(Capability(Kind.READ, "document:hr/*"))


def test_t2_widen_via_two_hops() -> None:
    """A attenuates to B, then B tries to hand C something A never had."""
    chain = PrincipalChain.root("user:alice", "agent:a", READ_HR).delegate("agent:b", READ_HR)
    with pytest.raises(ScopeEscalation):
        chain.delegate("agent:c", Scope.of(Capability(Kind.READ, "document:*")))
    with pytest.raises(ScopeEscalation):
        chain.delegate("agent:c", Scope.of(Capability(Kind.ACT, "tool:hr.export")))


def test_t2_widen_by_rebuilding_chain_with_forged_links() -> None:
    """An attacker constructs the chain value directly with a widening middle link."""
    links = (
        Delegation("agent:a", "agent:b", READ_HR),
        Delegation("agent:b", "agent:c", Scope.of(Capability(Kind.READ, "document:*"))),
        Delegation("agent:c", "agent:d", READ_HR),
    )
    with pytest.raises(ScopeEscalation):
        PrincipalChain("user:alice", "agent:d", links, READ_HR)


def test_t2_wildcard_trick() -> None:
    """`document:hr*` is wider than `document:hr/*` (matches document:hrx). Must be rejected."""
    chain = PrincipalChain.root("user:alice", "agent:a", READ_HR)
    with pytest.raises(ScopeEscalation):
        chain.delegate("agent:b", Scope.of(Capability(Kind.READ, "document:hr*")))


def test_t2_replace_frozen_dataclass() -> None:
    """dataclasses.replace re-runs validation: swapping scope on a chain is caught."""
    chain = PrincipalChain.root("user:alice", "agent:a", READ_HR).delegate("agent:b", READ_HR)
    with pytest.raises(InvariantViolation):
        dataclasses.replace(chain, scope=Scope.of(Capability(Kind.READ, "document:*")))


def test_t6_agent_as_subject() -> None:
    with pytest.raises(InvariantViolation):
        PrincipalChain.root("agent:a", "agent:a", READ_HR)


def test_t6_delegation_cannot_change_subject() -> None:
    chain = PrincipalChain.root("user:alice", "agent:a", READ_HR)
    with pytest.raises(InvariantViolation):
        dataclasses.replace(chain, subject="agent:a")
