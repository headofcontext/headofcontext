"""Composition against the real OpenFGA: token → chain → gate across three hops, with revocation."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from headofcontext.actions import ActionGate, InMemoryApprovalStore
from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.core.errors import ActionDenied
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.integrations import AgentSession
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService
from tests.services import FgaStore, load_json

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(openfga_store: FgaStore) -> AsyncIterator[OpenFgaEngine]:
    engine = OpenFgaEngine.connect(
        openfga_store.url,
        openfga_store.store_id,
        AlwaysFresh(),
        authorization_model_id=openfga_store.model_id,
    )
    yield engine
    await engine.close()


def _user(department: str) -> str:
    users = load_json("users.json")
    assert isinstance(users, list)
    return next(u["id"] for u in users if u["department"] == department and not u["intern"])


async def test_three_hops_with_acme_rights(engine: OpenFgaEngine) -> None:
    audit = InMemoryAuditSink()
    tokens = TokenService(
        KeyRing.generate(1), InMemoryRevocationStore(), audit, ttl=timedelta(minutes=10)
    )
    gate = ActionGate(Decider(engine, audit), InMemoryApprovalStore(), audit)
    hr_user = _user("rh")
    root_chain = PrincipalChain.root(
        hr_user, "agent:assistant", Scope.of(Capability(Kind.ACT, "tool:*"))
    )
    assistant = AgentSession(
        token=tokens.issue(root_chain).token,
        caller="agent:assistant",
        token_service=tokens,
        gate=gate,
    )
    mailer = assistant.delegate("agent:mailer", Scope.of(Capability(Kind.ACT, "tool:mail.*")))
    sub = mailer.delegate("agent:sub", Scope.of(Capability(Kind.ACT, "tool:mail.send")))

    assert (await assistant.authorize("hr.export", {})).allowed  # HR user may export
    assert (await mailer.authorize("mail.send", {})).allowed
    assert (await sub.authorize("mail.send", {})).allowed
    with pytest.raises(ActionDenied):
        await mailer.authorize("hr.export", {})  # scope narrowed at hop 1
    with pytest.raises(ActionDenied):
        await sub.authorize("mail.send_bulk", {})  # scope narrowed at hop 2

    # A store employee with the same agents cannot export HR data: the subject decides.
    store_chain = PrincipalChain.root(_user("magasin-lille"), "agent:assistant", root_chain.scope)
    store_session = AgentSession(
        token=tokens.issue(store_chain).token,
        caller="agent:assistant",
        token_service=tokens,
        gate=gate,
    )
    assert not (await store_session.authorize("hr.export", {})).allowed
    assert (await store_session.authorize("mail.send", {})).allowed

    # Revoking the mailer hop propagates to sub and leaves the assistant untouched.
    tokens.revoke_id(mailer.revocation_ids[-1], reason="rotation")
    with pytest.raises(ActionDenied):
        await sub.authorize("mail.send", {})
    assert (await assistant.authorize("mail.send", {})).allowed
