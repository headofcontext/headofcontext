"""T2 (escalation at token level), T7 (replay / forgery), T11 (self-asserted identity)."""

from datetime import timedelta

import biscuit_auth
import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Kind, PrincipalChain, Scope
from headofcontext.core.errors import InvalidResource, TokenInvalid
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from tests.conftest import FrozenClock

pytestmark = pytest.mark.adversarial

READ_HR = Capability(Kind.READ, "document:hr/*")
ROOT = PrincipalChain.root("user:alice", "agent:a", Scope.of(READ_HR))


@pytest.fixture
def keyring() -> KeyRing:
    return KeyRing.generate(key_id=1)


@pytest.fixture
def service(keyring: KeyRing, clock: FrozenClock) -> TokenService:
    return TokenService(
        keyring, InMemoryRevocationStore(), InMemoryAuditSink(), clock, ttl=timedelta(minutes=5)
    )


def _verify_read(
    service: TokenService, token: str, caller: str = "agent:a", resource: str = "document:hr/x"
) -> None:
    service.verify(token, caller=caller, operation=Kind.READ, resource=resource)


def test_t7_tampered_token(service: TokenService) -> None:
    token = service.issue(ROOT).token
    flipped = token[:40] + ("A" if token[40] != "A" else "B") + token[41:]
    with pytest.raises(TokenInvalid):
        _verify_read(service, flipped)


def test_t7_foreign_root_key(service: TokenService, clock: FrozenClock) -> None:
    mallory = TokenService(
        KeyRing.generate(key_id=1), InMemoryRevocationStore(), InMemoryAuditSink(), clock
    )
    token = mallory.issue(
        PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:*")))
    ).token
    with pytest.raises(TokenInvalid):
        _verify_read(service, token)


def test_t7_replay_after_expiry(service: TokenService, clock: FrozenClock) -> None:
    token = service.issue(ROOT).token
    _verify_read(service, token)
    clock.tick(minutes=5, seconds=1)
    with pytest.raises(TokenInvalid):
        _verify_read(service, token)


def test_t7_replay_by_another_agent(service: TokenService) -> None:
    token = service.issue(ROOT).token
    with pytest.raises(TokenInvalid):
        _verify_read(service, token, caller="agent:mallory")


def test_t2_forged_facts_in_attenuation_block_are_ignored(
    service: TokenService, keyring: KeyRing
) -> None:
    """A holder appends raw datalog facts claiming more rights. Biscuit does not trust them."""
    token = service.issue(ROOT).token
    parsed = biscuit_auth.Biscuit.from_base64(token, keyring.public_key(1))
    forged = parsed.append(
        biscuit_auth.BlockBuilder(
            'cap_prefix("read", "document:"); cap_prefix("act", "tool:");'
            'cap_exact("read", "document:finance/x");'
        )
    ).to_base64()
    _verify_read(service, forged, resource="document:hr/x")  # still within the authority scope
    with pytest.raises(TokenInvalid):
        _verify_read(service, forged, resource="document:finance/x")
    with pytest.raises(TokenInvalid):
        service.verify(forged, caller="agent:a", operation=Kind.ACT, resource="tool:mail.send")


def test_t11_forged_subject_in_attenuation_block_is_ignored(
    service: TokenService, keyring: KeyRing
) -> None:
    token = service.issue(ROOT).token
    parsed = biscuit_auth.Biscuit.from_base64(token, keyring.public_key(1))
    forged = parsed.append(
        biscuit_auth.BlockBuilder('subject("user:mallory"); actor("agent:mallory");')
    ).to_base64()
    verified = _verify_read_ok(service, forged)
    assert verified.subject == "user:alice"
    assert verified.actor == "agent:a"


def test_t2_forged_delegation_trail_claiming_wider_scope(
    service: TokenService, keyring: KeyRing
) -> None:
    """A hand-built delegation block whose declared scope is wider than the parent is rejected."""
    token = service.issue(ROOT).token
    parsed = biscuit_auth.Biscuit.from_base64(token, keyring.public_key(1))
    forged = parsed.append(
        biscuit_auth.BlockBuilder(
            'delegation("agent:a", "agent:b"); scope_cap("read", "document:*");'
        )
    ).to_base64()
    with pytest.raises(TokenInvalid):
        _verify_read(service, forged, caller="agent:b")


def test_t2_forged_delegation_without_checks_cannot_widen(
    service: TokenService, keyring: KeyRing
) -> None:
    """A block declaring a narrow scope without checks still cannot exceed the parent."""
    token = service.issue(ROOT).token
    parsed = biscuit_auth.Biscuit.from_base64(token, keyring.public_key(1))
    forged = parsed.append(
        biscuit_auth.BlockBuilder(
            'delegation("agent:a", "agent:b"); scope_cap("read", "document:hr/*");'
        )
    ).to_base64()
    _verify_read(service, forged, caller="agent:b", resource="document:hr/x")
    with pytest.raises(TokenInvalid):
        _verify_read(service, forged, caller="agent:b", resource="document:finance/x")
    with pytest.raises(TokenInvalid):
        _verify_read(service, forged, caller="agent:a", resource="document:hr/x")


def test_t2_delegation_trail_with_broken_links_rejected(
    service: TokenService, keyring: KeyRing
) -> None:
    token = service.issue(ROOT).token
    parsed = biscuit_auth.Biscuit.from_base64(token, keyring.public_key(1))
    forged = parsed.append(
        biscuit_auth.BlockBuilder(
            'delegation("agent:zzz", "agent:b"); scope_cap("read", "document:hr/*");'
        )
    ).to_base64()
    with pytest.raises(TokenInvalid):
        _verify_read(service, forged, caller="agent:b")


def test_t11_datalog_injection_through_resource_rejected() -> None:
    with pytest.raises(InvalidResource):
        Capability(Kind.READ, 'document:x"); cap_prefix("act", "tool:')


def _verify_read_ok(service: TokenService, token: str):
    return service.verify(token, caller="agent:a", operation=Kind.READ, resource="document:hr/x")
