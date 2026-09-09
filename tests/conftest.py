"""Shared strategies and fixtures for the HeadOfContext test suite."""

from __future__ import annotations

import pytest
from hypothesis import strategies as st

from headofcontext.core import Capability, Kind, PrincipalChain, Scope

pytest_plugins = ["tests.services"]

RESOURCE_TYPES = ["document", "tool", "memory", "source"]
ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-_/."


@st.composite
def resource_patterns(draw: st.DrawFn) -> str:
    rtype = draw(st.sampled_from(RESOURCE_TYPES))
    rid = draw(st.text(alphabet=ID_ALPHABET, min_size=0, max_size=12))
    wildcard = draw(st.booleans())
    if wildcard:
        return f"{rtype}:{rid}*"
    if not rid:
        rid = "x"
    return f"{rtype}:{rid}"


@st.composite
def capabilities(draw: st.DrawFn) -> Capability:
    return Capability(draw(st.sampled_from(list(Kind))), draw(resource_patterns()))


@st.composite
def scopes(draw: st.DrawFn, min_size: int = 0, max_size: int = 8) -> Scope:
    caps = draw(st.frozensets(capabilities(), min_size=min_size, max_size=max_size))
    return Scope(caps)


@st.composite
def sub_scopes(draw: st.DrawFn, parent: Scope) -> Scope:
    """A scope that is a subset of ``parent`` by construction (drops capabilities)."""
    if len(parent) == 0:
        return Scope.empty()
    caps = draw(st.frozensets(st.sampled_from(sorted(parent)), max_size=len(parent)))
    return Scope(caps)


@st.composite
def agent_ids(draw: st.DrawFn) -> str:
    return "agent:" + draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=10)
    )


@st.composite
def user_ids(draw: st.DrawFn) -> str:
    return "user:" + draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=10)
    )


@st.composite
def root_chains(draw: st.DrawFn) -> PrincipalChain:
    return PrincipalChain.root(
        subject=draw(user_ids()), actor=draw(agent_ids()), scope=draw(scopes(min_size=1))
    )


from tests.support.fakes import FrozenClock  # noqa: E402 — re-exported for the suites


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()
