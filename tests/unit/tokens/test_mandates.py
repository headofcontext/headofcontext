"""Standing mandates (ADR 0016): created while the human is present, used by the agent alone."""

from __future__ import annotations

from datetime import timedelta

import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.audit.events import EventKind
from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.errors import MandateDenied, TokenRevoked
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from headofcontext.tokens.mandates import (
    InMemoryMandateStore,
    Mandate,
    MandateService,
    MandateStatus,
)
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

ALICE, ASSISTANT, MAILER = "user:alice", "agent:assistant", "agent:mailer"
MANDATE_SCOPE = Scope.of(Capability(Kind.READ, "document:*"), Capability(Kind.ACT, "tool:mail.*"))
NARROW = Scope.of(Capability(Kind.ACT, "tool:mail.send"))
WIDE = Scope.of(Capability(Kind.ACT, "tool:*"))


@pytest.fixture
def graph() -> FakeGraph:
    g = FakeGraph()
    g.grant(ALICE, "can_act_on_behalf_of", ASSISTANT)
    g.grant(ALICE, "can_act_on_behalf_of", MAILER)
    return g


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def tokens(audit: InMemoryAuditSink, clock: FrozenClock) -> TokenService:
    return TokenService(
        KeyRing.generate(1), InMemoryRevocationStore(), audit, clock, ttl=timedelta(hours=1)
    )


@pytest.fixture
def service(
    graph: FakeGraph, tokens: TokenService, audit: InMemoryAuditSink, clock: FrozenClock
) -> MandateService:
    return MandateService(InMemoryMandateStore(), tokens, graph, audit, clock, max_days=90)


async def create(service: MandateService, clock: FrozenClock, **overrides: object) -> Mandate:
    params: dict[str, object] = {
        "subject": ALICE,
        "agent": ASSISTANT,
        "scope": MANDATE_SCOPE,
        "expires_at": clock.now() + timedelta(days=30),
        "created_by": ALICE,
    }
    params.update(overrides)
    return await service.create(**params)  # type: ignore[arg-type]


async def test_create_requires_directory_binding(
    service: MandateService, clock: FrozenClock, graph: FakeGraph
) -> None:
    mandate = await create(service, clock)
    assert mandate.status is MandateStatus.ACTIVE and mandate.subject == ALICE
    graph.revoke(ALICE, "can_act_on_behalf_of", MAILER)
    with pytest.raises(MandateDenied) as exc:
        await create(service, clock, agent=MAILER)
    assert exc.value.reason == "not_related"


async def test_create_is_bounded_and_validated(service: MandateService, clock: FrozenClock) -> None:
    with pytest.raises(MandateDenied) as exc:
        await create(service, clock, expires_at=clock.now() + timedelta(days=91))
    assert exc.value.reason == "mandate_too_long"
    with pytest.raises(MandateDenied):
        await create(service, clock, expires_at=clock.now() - timedelta(seconds=1))
    with pytest.raises(MandateDenied) as exc:
        await create(service, clock, created_by="user:bob")
    assert exc.value.reason == "not_subject"
    with pytest.raises(MandateDenied):
        await create(service, clock, subject="agent:x", created_by="agent:x")


async def test_issue_subset_scope_and_bounded_ttl(
    service: MandateService, clock: FrozenClock, tokens: TokenService
) -> None:
    mandate = await create(service, clock, max_token_ttl=timedelta(minutes=30))
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=NARROW)
    assert issued.chain.subject == ALICE and issued.chain.actor == ASSISTANT
    assert issued.chain.scope == NARROW
    assert issued.expires_at == clock.now() + timedelta(minutes=30)
    assert mandate.revocation_id in issued.revocation_ids
    verified = tokens.verify(
        issued.token, caller=ASSISTANT, operation=Kind.ACT, resource="tool:mail.send"
    )
    assert verified.mandate_id == mandate.mandate_id

    longer = await service.issue(
        mandate.mandate_id, caller=ASSISTANT, scope=NARROW, ttl=timedelta(hours=5)
    )
    assert longer.expires_at == clock.now() + timedelta(minutes=30), "capped by max_token_ttl"


async def test_issue_never_exceeds_the_mandate(service: MandateService, clock: FrozenClock) -> None:
    mandate = await create(service, clock)
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=WIDE)
    assert exc.value.reason == "scope_escalation"
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=MAILER, scope=NARROW)
    assert exc.value.reason == "mandate_wrong_agent"
    with pytest.raises(MandateDenied) as exc:
        await service.issue("nope", caller=ASSISTANT, scope=NARROW)
    assert exc.value.reason == "mandate_unknown"


async def test_token_ttl_never_outlives_the_mandate(
    service: MandateService, clock: FrozenClock
) -> None:
    mandate = await create(service, clock, expires_at=clock.now() + timedelta(minutes=10))
    issued = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=NARROW)
    assert issued.expires_at == mandate.expires_at


async def test_revocation_kills_every_token_including_attenuated_ones(
    service: MandateService, clock: FrozenClock, tokens: TokenService, audit: InMemoryAuditSink
) -> None:
    mandate = await create(service, clock)
    root = await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=MANDATE_SCOPE)
    child = tokens.attenuate(root.token, to_actor=MAILER, scope=NARROW)
    revoked = await service.revoke(mandate.mandate_id, by=ALICE, reason="done")
    assert revoked.status is MandateStatus.REVOKED
    with pytest.raises(TokenRevoked):
        tokens.inspect(root.token, caller=ASSISTANT)
    with pytest.raises(TokenRevoked):
        tokens.verify(child.token, caller=MAILER, operation=Kind.ACT, resource="tool:mail.send")
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=NARROW)
    assert exc.value.reason == "mandate_revoked"
    kinds = [e.kind for e in audit.events]
    assert EventKind.MANDATE_CREATED in kinds and EventKind.MANDATE_REVOKED in kinds


async def test_only_the_subject_revokes(service: MandateService, clock: FrozenClock) -> None:
    mandate = await create(service, clock)
    with pytest.raises(MandateDenied) as exc:
        await service.revoke(mandate.mandate_id, by="user:bob", reason="hijack")
    assert exc.value.reason == "not_subject"
    assert (await service.get(mandate.mandate_id)).status is MandateStatus.ACTIVE  # type: ignore[union-attr]


async def test_expiry_is_checked_on_issue_and_by_the_sweeper(
    service: MandateService, clock: FrozenClock
) -> None:
    mandate = await create(service, clock, expires_at=clock.now() + timedelta(hours=1))
    clock.tick(hours=2)
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=NARROW)
    assert exc.value.reason == "mandate_expired"
    other = await create(service, clock, expires_at=clock.now() + timedelta(minutes=1))
    clock.tick(minutes=2)
    assert await service.expire() == 1
    assert (await service.get(other.mandate_id)).status is MandateStatus.EXPIRED  # type: ignore[union-attr]


async def test_binding_removed_after_creation_denies_issue(
    service: MandateService, clock: FrozenClock, graph: FakeGraph
) -> None:
    mandate = await create(service, clock)
    graph.revoke(ALICE, "can_act_on_behalf_of", ASSISTANT)
    with pytest.raises(MandateDenied) as exc:
        await service.issue(mandate.mandate_id, caller=ASSISTANT, scope=NARROW)
    assert exc.value.reason == "not_related"


async def test_list_for_subject_only(service: MandateService, clock: FrozenClock) -> None:
    mine = await create(service, clock)
    assert [m.mandate_id for m in await service.list_for(ALICE)] == [mine.mandate_id]
    assert await service.list_for("user:bob") == []


def test_token_without_mandate_has_no_mandate_id(tokens: TokenService) -> None:
    from headofcontext.core import PrincipalChain

    issued = tokens.issue(PrincipalChain.root(ALICE, ASSISTANT, NARROW))
    assert tokens.inspect(issued.token, caller=ASSISTANT).mandate_id is None
