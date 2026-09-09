"""Attacks on standing mandates (ADR 0016; T13, T14, T17). All must fail for the attacker."""

from __future__ import annotations

from datetime import timedelta

import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.errors import MandateDenied, TokenInvalid, TokenRevoked
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from headofcontext.tokens.mandates import InMemoryMandateStore, MandateService, MandateStatus
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

ALICE, BOB = "user:alice", "user:bob"
ASSISTANT, ROGUE = "agent:assistant", "agent:rogue"
SCOPE = Scope.of(Capability(Kind.ACT, "tool:mail.*"))


@pytest.fixture
def world(clock: FrozenClock) -> tuple[MandateService, TokenService, FakeGraph]:
    graph = FakeGraph()
    graph.grant(ALICE, "can_act_on_behalf_of", ASSISTANT)
    graph.grant(BOB, "can_act_on_behalf_of", ROGUE)
    audit = InMemoryAuditSink()
    tokens = TokenService(KeyRing.generate(1), InMemoryRevocationStore(), audit, clock)
    return MandateService(InMemoryMandateStore(), tokens, graph, audit, clock), tokens, graph


async def test_t13_stolen_mandate_id_is_useless_without_the_agent_credentials(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    service, _, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=7),
        created_by=ALICE,
    )
    # The rogue agent knows the id (leaked log, shared channel) and presents its own credentials.
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ROGUE, scope=SCOPE)
    assert exc.value.reason == "mandate_wrong_agent"


async def test_t13_forged_mandate_fact_in_a_token_is_rejected(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    """A token that claims a mandate it was not issued under fails signature verification."""
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=7),
        created_by=ALICE,
    )
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    tampered = issued.token[:-12] + ("A" if issued.token[-12] != "A" else "B") + issued.token[-11:]
    with pytest.raises(TokenInvalid):
        tokens.inspect(tampered, caller=ASSISTANT)


async def test_t14_wide_mandate_cannot_exceed_what_the_human_may_do(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    """The mandate scope is a ceiling for tokens; OpenFGA remains the ceiling for decisions."""
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=Scope.of(Capability(Kind.ACT, "tool:*")),
        expires_at=clock.now() + timedelta(days=7),
        created_by=ALICE,
    )
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    with pytest.raises(TokenInvalid):
        tokens.verify(issued.token, caller=ASSISTANT, operation=Kind.ACT, resource="tool:hr.export")


async def test_t14_revocation_wins_over_a_long_mandate(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=90),
        created_by=ALICE,
    )
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    await service.revoke(mandate.mandate_id, by=ALICE, reason="leaving")
    with pytest.raises(TokenRevoked):
        tokens.verify(issued.token, caller=ASSISTANT, operation=Kind.ACT, resource="tool:mail.send")


async def test_t14_another_human_cannot_revoke_or_read_my_mandates(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    service, _, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=7),
        created_by=ALICE,
    )
    assert await service.list_for(BOB) == []
    with pytest.raises(MandateDenied):
        await service.revoke(mandate.mandate_id, by=BOB, reason="sabotage")


async def test_mandate_for_someone_else_cannot_be_created(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    """Bob, authenticated, tries to mandate his agent to act for Alice."""
    service, _, graph = world
    graph.grant(ALICE, "can_act_on_behalf_of", ROGUE)
    with pytest.raises(MandateDenied) as exc:
        await service.create(
            subject=ALICE,
            agent=ROGUE,
            scope=SCOPE,
            expires_at=clock.now() + timedelta(days=7),
            created_by=BOB,
        )
    assert exc.value.reason == "not_subject"


async def test_t17_delegate_revoking_its_token_does_not_kill_the_mandate(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    """A sub-agent revokes the token it holds: its copy dies, the parent and the mandate live."""
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=30),
        created_by=ALICE,
    )
    parent = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    child = tokens.attenuate(
        parent.token, to_actor=ROGUE, scope=Scope.of(Capability(Kind.ACT, "tool:mail.send"))
    )
    tokens.revoke(child.token, caller=ROGUE, reason="sabotage")
    with pytest.raises(TokenRevoked):
        tokens.inspect(child.token, caller=ROGUE)
    tokens.verify(parent.token, caller=ASSISTANT, operation=Kind.ACT, resource="tool:mail.send")
    assert (await service.get(mandate.mandate_id)).status is MandateStatus.ACTIVE  # type: ignore[union-attr]
    await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)


async def test_t17_revoke_token_never_targets_a_mandate_id(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=30),
        created_by=ALICE,
    )
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    revoked = tokens.revoke(issued.token, caller=ASSISTANT, reason="done")
    assert not revoked.startswith("mandate:")
    assert not tokens.is_revoked((mandate.revocation_id,))


async def test_t17_out_of_band_revocation_of_the_id_reads_as_revoked(
    world: tuple[MandateService, TokenService, FakeGraph], clock: FrozenClock
) -> None:
    """If the revocation id is revoked by any path, the mandate state follows before any issue."""
    service, tokens, _ = world
    mandate = await service.create(
        subject=ALICE,
        agent=ASSISTANT,
        scope=SCOPE,
        expires_at=clock.now() + timedelta(days=30),
        created_by=ALICE,
    )
    tokens.revoke_id(mandate.revocation_id, reason="operator")
    assert (await service.get(mandate.mandate_id)).status is MandateStatus.REVOKED  # type: ignore[union-attr]
    assert (await service.list_for(ALICE))[0].status is MandateStatus.REVOKED
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=SCOPE)
    assert exc.value.reason == "mandate_revoked"
