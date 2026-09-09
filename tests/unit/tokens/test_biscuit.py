"""Issue, attenuate, verify and revoke biscuit tokens (ADR 0003)."""

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Delegation, Kind, PrincipalChain, Scope
from headofcontext.core.errors import ScopeEscalation, TokenInvalid, TokenRevoked
from headofcontext.tokens.biscuit import (
    InMemoryRevocationStore,
    KeyRing,
    TokenService,
    VerifiedToken,
    attenuate,
)
from tests.conftest import FrozenClock, root_chains, sub_scopes

READ_ALL = Capability(Kind.READ, "document:*")
READ_HR = Capability(Kind.READ, "document:hr/*")
SEND_MAIL = Capability(Kind.ACT, "tool:mail.send")
ROOT = PrincipalChain.root("user:alice", "agent:a", Scope.of(READ_ALL, SEND_MAIL))


@pytest.fixture
def keyring() -> KeyRing:
    return KeyRing.generate(key_id=1)


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def revocations() -> InMemoryRevocationStore:
    return InMemoryRevocationStore()


@pytest.fixture
def service(
    keyring: KeyRing,
    audit: InMemoryAuditSink,
    revocations: InMemoryRevocationStore,
    clock: FrozenClock,
) -> TokenService:
    return TokenService(keyring, revocations, audit, clock, ttl=timedelta(hours=1))


class TestIssueAndVerify:
    def test_roundtrip(self, service: TokenService) -> None:
        issued = service.issue(ROOT)
        verified = service.verify(
            issued.token, caller="agent:a", operation=Kind.READ, resource="document:x"
        )
        assert isinstance(verified, VerifiedToken)
        assert verified.chain == ROOT
        assert verified.subject == "user:alice"
        assert verified.actor == "agent:a"
        assert verified.revocation_ids == issued.revocation_ids
        assert len(verified.revocation_ids) == 1

    def test_issue_requires_root_chain(self, service: TokenService) -> None:
        delegated = ROOT.delegate("agent:b", Scope.of(READ_HR))
        with pytest.raises(TokenInvalid):
            service.issue(delegated)

    def test_exact_capability(self, service: TokenService) -> None:
        token = service.issue(ROOT).token
        service.verify(token, caller="agent:a", operation=Kind.ACT, resource="tool:mail.send")
        with pytest.raises(TokenInvalid, match="operation"):
            service.verify(
                token, caller="agent:a", operation=Kind.ACT, resource="tool:mail.sendall"
            )

    def test_prefix_capability(self, service: TokenService) -> None:
        token = service.issue(PrincipalChain.root("user:alice", "agent:a", Scope.of(READ_HR))).token
        service.verify(
            token, caller="agent:a", operation=Kind.READ, resource="document:hr/salaries"
        )
        with pytest.raises(TokenInvalid):
            service.verify(token, caller="agent:a", operation=Kind.READ, resource="document:hrx")
        with pytest.raises(TokenInvalid):
            service.verify(
                token, caller="agent:a", operation=Kind.ACT, resource="document:hr/salaries"
            )

    def test_wrong_caller(self, service: TokenService) -> None:
        token = service.issue(ROOT).token
        with pytest.raises(TokenInvalid, match="caller"):
            service.verify(token, caller="agent:b", operation=Kind.READ, resource="document:x")

    def test_expired(self, service: TokenService, clock: FrozenClock) -> None:
        token = service.issue(ROOT).token
        clock.tick(hours=1, seconds=1)
        with pytest.raises(TokenInvalid, match="expired"):
            service.verify(token, caller="agent:a", operation=Kind.READ, resource="document:x")

    def test_unknown_key_id(
        self, service: TokenService, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        other = TokenService(
            KeyRing.generate(key_id=2), InMemoryRevocationStore(), audit, clock, key_id=2
        )
        token = other.issue(ROOT).token
        with pytest.raises(TokenInvalid, match="key"):
            service.verify(token, caller="agent:a", operation=Kind.READ, resource="document:x")

    def test_same_key_id_other_key(
        self, service: TokenService, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        other = TokenService(KeyRing.generate(key_id=1), InMemoryRevocationStore(), audit, clock)
        token = other.issue(ROOT).token
        with pytest.raises(TokenInvalid):
            service.verify(token, caller="agent:a", operation=Kind.READ, resource="document:x")

    def test_garbage(self, service: TokenService) -> None:
        with pytest.raises(TokenInvalid):
            service.verify(
                "not-a-token", caller="agent:a", operation=Kind.READ, resource="document:x"
            )

    def test_issue_is_audited_without_token(
        self, service: TokenService, audit: InMemoryAuditSink
    ) -> None:
        issued = service.issue(ROOT)
        event = audit.events[-1]
        assert event.kind is EventKind.TOKEN_ISSUED
        assert event.subject == "user:alice"
        assert event.token_fingerprint is not None
        assert issued.token not in event.canonical_json()


class TestAttenuate:
    def test_delegation_narrows(self, service: TokenService) -> None:
        root = service.issue(ROOT).token
        child = service.attenuate(root, to_actor="agent:b", scope=Scope.of(READ_HR))
        verified = service.verify(
            child.token, caller="agent:b", operation=Kind.READ, resource="document:hr/x"
        )
        assert verified.chain == ROOT.delegate("agent:b", Scope.of(READ_HR))
        assert verified.chain.delegation == (Delegation("agent:a", "agent:b", Scope.of(READ_HR)),)
        assert len(verified.revocation_ids) == 2
        with pytest.raises(TokenInvalid):
            service.verify(
                child.token, caller="agent:b", operation=Kind.READ, resource="document:finance/x"
            )
        with pytest.raises(TokenInvalid):
            service.verify(
                child.token, caller="agent:b", operation=Kind.ACT, resource="tool:mail.send"
            )

    def test_delegated_token_bound_to_new_holder(self, service: TokenService) -> None:
        root = service.issue(ROOT).token
        child = service.attenuate(root, to_actor="agent:b", scope=Scope.of(READ_HR))
        with pytest.raises(TokenInvalid, match="caller"):
            service.verify(
                child.token, caller="agent:a", operation=Kind.READ, resource="document:hr/x"
            )

    def test_multi_hop(self, service: TokenService) -> None:
        root = service.issue(ROOT).token
        b = service.attenuate(root, to_actor="agent:b", scope=Scope.of(READ_HR, SEND_MAIL))
        c = service.attenuate(b.token, to_actor="agent:c", scope=Scope.of(SEND_MAIL))
        verified = service.verify(
            c.token, caller="agent:c", operation=Kind.ACT, resource="tool:mail.send"
        )
        assert verified.chain.depth == 2
        assert verified.chain.root_actor == "agent:a"
        with pytest.raises(TokenInvalid):
            service.verify(c.token, caller="agent:c", operation=Kind.READ, resource="document:hr/x")
        with pytest.raises(TokenInvalid):
            service.verify(c.token, caller="agent:b", operation=Kind.ACT, resource="tool:mail.send")

    def test_escalation_rejected_before_building(self, service: TokenService) -> None:
        child = service.attenuate(
            service.issue(ROOT).token, to_actor="agent:b", scope=Scope.of(READ_HR)
        )
        with pytest.raises(ScopeEscalation):
            service.attenuate(child.token, to_actor="agent:c", scope=Scope.of(READ_ALL))

    def test_empty_scope_allows_nothing(self, service: TokenService) -> None:
        child = service.attenuate(
            service.issue(ROOT).token, to_actor="agent:b", scope=Scope.empty()
        )
        with pytest.raises(TokenInvalid):
            service.verify(
                child.token, caller="agent:b", operation=Kind.READ, resource="document:x"
            )

    def test_attenuate_is_offline(self, service: TokenService, keyring: KeyRing) -> None:
        """Attenuation needs only the public key: a holder can delegate without the issuer."""
        root = service.issue(ROOT).token
        child = attenuate(root, keyring, to_actor="agent:b", scope=Scope.of(READ_HR))
        service.verify(child.token, caller="agent:b", operation=Kind.READ, resource="document:hr/x")

    def test_depth_bound(self, service: TokenService) -> None:
        token = service.issue(
            PrincipalChain.root("user:alice", "agent:0", Scope.of(READ_ALL))
        ).token
        from headofcontext.core import MAX_DELEGATION_DEPTH

        for i in range(1, MAX_DELEGATION_DEPTH + 1):
            token = service.attenuate(token, to_actor=f"agent:{i}", scope=Scope.of(READ_ALL)).token
        with pytest.raises(TokenInvalid):
            service.attenuate(token, to_actor="agent:overflow", scope=Scope.of(READ_ALL))

    def test_attenuation_is_audited(self, service: TokenService, audit: InMemoryAuditSink) -> None:
        service.attenuate(service.issue(ROOT).token, to_actor="agent:b", scope=Scope.of(READ_HR))
        assert audit.events[-1].kind is EventKind.TOKEN_DELEGATED
        assert audit.events[-1].actor == "agent:b"
        assert audit.events[-1].delegation_depth == 1

    @given(st.data())
    @settings(max_examples=60, deadline=None)
    def test_property_delegated_token_allows_exactly_subscope(self, data: st.DataObject) -> None:
        keyring = KeyRing.generate(key_id=1)
        service = TokenService(
            keyring, InMemoryRevocationStore(), InMemoryAuditSink(), FrozenClock()
        )
        root_chain = data.draw(root_chains())
        child_scope = data.draw(sub_scopes(root_chain.scope))
        to = data.draw(
            st.sampled_from(["agent:x", "agent:y"]).filter(lambda a: a != root_chain.actor)
        )
        child = service.attenuate(service.issue(root_chain).token, to_actor=to, scope=child_scope)
        probe = data.draw(st.sampled_from(sorted(root_chain.scope)))
        concrete = probe.resource[:-1] + "probe" if probe.is_pattern else probe.resource
        expected = child_scope.covers(Capability(probe.kind, concrete))
        try:
            service.verify(child.token, caller=to, operation=probe.kind, resource=concrete)
            allowed = True
        except TokenInvalid:
            allowed = False
        assert allowed == expected


class TestRevocation:
    def test_revoke_root_invalidates_downstream(self, service: TokenService) -> None:
        issued = service.issue(ROOT)
        child = service.attenuate(issued.token, to_actor="agent:b", scope=Scope.of(READ_HR))
        service.revoke_id(issued.revocation_ids[0], reason="user logged out")
        with pytest.raises(TokenRevoked):
            service.verify(
                issued.token, caller="agent:a", operation=Kind.READ, resource="document:x"
            )
        with pytest.raises(TokenRevoked):
            service.verify(
                child.token, caller="agent:b", operation=Kind.READ, resource="document:hr/x"
            )

    def test_revoke_child_keeps_parent(self, service: TokenService) -> None:
        issued = service.issue(ROOT)
        child = service.attenuate(issued.token, to_actor="agent:b", scope=Scope.of(READ_HR))
        service.revoke_id(child.revocation_ids[-1], reason="sub-agent finished")
        with pytest.raises(TokenRevoked):
            service.verify(
                child.token, caller="agent:b", operation=Kind.READ, resource="document:hr/x"
            )
        service.verify(issued.token, caller="agent:a", operation=Kind.READ, resource="document:x")

    def test_revoke_middle_of_three_hops(self, service: TokenService) -> None:
        a = service.issue(ROOT)
        b = service.attenuate(a.token, to_actor="agent:b", scope=Scope.of(READ_HR))
        c = service.attenuate(b.token, to_actor="agent:c", scope=Scope.of(READ_HR))
        service.revoke_id(b.revocation_ids[-1], reason="b compromised")
        with pytest.raises(TokenRevoked):
            service.verify(c.token, caller="agent:c", operation=Kind.READ, resource="document:hr/x")
        service.verify(a.token, caller="agent:a", operation=Kind.READ, resource="document:x")

    def test_revocation_is_audited(self, service: TokenService, audit: InMemoryAuditSink) -> None:
        issued = service.issue(ROOT)
        service.revoke_id(issued.revocation_ids[0], reason="test")
        assert audit.events[-1].kind is EventKind.TOKEN_REVOKED
        assert audit.events[-1].reason == "test"


def test_keyring_round_trips_raw_private_key() -> None:
    """HOC_ROOT_KEY_HEX is loaded through this path: the same key must verify what it signed."""
    import biscuit_auth

    raw = biscuit_auth.KeyPair().private_key.to_bytes()
    ring = KeyRing.from_private_key_bytes(7, raw)
    again = KeyRing.from_private_key_bytes(7, raw)
    assert ring.public_key_bytes(7) == again.public_key_bytes(7)


class TestVerifyMany:
    def test_evaluates_checks_per_resource(self, service: TokenService) -> None:
        root = service.issue(ROOT)
        child = service.attenuate(
            root.token,
            to_actor="agent:b",
            scope=Scope.of(Capability(Kind.READ, "document:hr/x")),
        )
        verified, permits = service.verify_many(
            child.token,
            caller="agent:b",
            operation=Kind.READ,
            resources=["document:hr/x", "document:hr/y", "document:hr/x"],
        )
        assert verified.chain.actor == "agent:b"
        assert permits == {"document:hr/x": True, "document:hr/y": False}

    def test_empty_list_still_binds_holder_and_expiry(
        self, service: TokenService, clock: FrozenClock
    ) -> None:
        token = service.issue(ROOT).token
        with pytest.raises(TokenInvalid):
            service.verify_many(token, caller="agent:z", operation=Kind.READ, resources=[])
        clock.tick(hours=2)
        with pytest.raises(TokenInvalid):
            service.verify_many(token, caller="agent:a", operation=Kind.READ, resources=[])
