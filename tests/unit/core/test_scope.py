import pytest
from hypothesis import given
from hypothesis import strategies as st

from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.errors import InvalidResource
from tests.conftest import capabilities, scopes, sub_scopes


class TestCapability:
    def test_exact_covers_only_itself(self) -> None:
        cap = Capability(Kind.READ, "document:a")
        assert cap.covers(Capability(Kind.READ, "document:a"))
        assert not cap.covers(Capability(Kind.READ, "document:ab"))
        assert not cap.covers(Capability(Kind.READ, "document:*"))
        assert not cap.covers(Capability(Kind.ACT, "document:a"))

    def test_wildcard_prefix(self) -> None:
        cap = Capability(Kind.READ, "document:hr/*")
        assert cap.covers(Capability(Kind.READ, "document:hr/salaries"))
        assert cap.covers(Capability(Kind.READ, "document:hr/*"))
        assert cap.covers(Capability(Kind.READ, "document:hr/2025/*"))
        assert not cap.covers(Capability(Kind.READ, "document:*"))
        assert not cap.covers(Capability(Kind.READ, "document:h*"))
        assert not cap.covers(Capability(Kind.READ, "document:finance/x"))
        assert not cap.covers(Capability(Kind.READ, "tool:hr/x"))

    def test_universal_wildcard(self) -> None:
        assert Capability(Kind.ACT, "tool:*").covers(Capability(Kind.ACT, "tool:mail.send"))

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "document",
            ":x",
            "document:",
            "Document:x",
            "document:a*b",
            "document:a b",
            "document:**",
        ],
    )
    def test_invalid_resource_rejected(self, bad: str) -> None:
        with pytest.raises(InvalidResource):
            Capability(Kind.READ, bad)

    @given(capabilities())
    def test_reflexive(self, cap: Capability) -> None:
        assert cap.covers(cap)

    @given(capabilities(), capabilities(), capabilities())
    def test_transitive(self, a: Capability, b: Capability, c: Capability) -> None:
        if a.covers(b) and b.covers(c):
            assert a.covers(c)


class TestScope:
    def test_empty_scope_covers_nothing(self) -> None:
        assert not Scope.empty().covers(Capability(Kind.READ, "document:a"))
        assert Scope.empty().is_subset_of(Scope.empty())

    @given(scopes())
    def test_subset_reflexive(self, s: Scope) -> None:
        assert s.is_subset_of(s)

    @given(st.data())
    def test_dropping_capabilities_is_subset(self, data: st.DataObject) -> None:
        parent = data.draw(scopes(min_size=1))
        child = data.draw(sub_scopes(parent))
        assert child.is_subset_of(parent)

    @given(st.data())
    def test_adding_uncovered_capability_is_not_subset(self, data: st.DataObject) -> None:
        parent = data.draw(scopes())
        extra = data.draw(capabilities().filter(lambda c: not parent.covers(c)))
        child = Scope(frozenset(parent) | {extra})
        assert not child.is_subset_of(parent)

    def test_narrower_pattern_is_subset(self) -> None:
        parent = Scope.of(Capability(Kind.READ, "document:*"))
        child = Scope.of(
            Capability(Kind.READ, "document:hr/*"), Capability(Kind.READ, "document:x")
        )
        assert child.is_subset_of(parent)
        assert not parent.is_subset_of(child)
