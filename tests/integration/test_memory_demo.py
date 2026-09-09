"""The phase 1 demo, as a test: agent learns from an HR document for an HR user, a store
employee asks the same agent and gets nothing, then the HR user loses HR and gets nothing too.
Real OpenFGA, real PostgreSQL ledger and journal."""

from collections.abc import AsyncIterator

import pytest

from headofcontext.audit import PostgresAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.memory import InMemoryMemoryAdapter, MemoryService, PostgresLedger
from tests.services import FgaStore, load_json

pytestmark = pytest.mark.integration

SCOPE = Scope.of(
    Capability(Kind.READ, "document:*"),
    Capability(Kind.READ, "memory:*"),
    Capability(Kind.REMEMBER, "document:*"),
    Capability(Kind.REMEMBER, "memory:*"),  # needed to forget at the end
)


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


@pytest.fixture
def service(engine: OpenFgaEngine, pg_dsn: str) -> MemoryService:
    ledger = PostgresLedger(pg_dsn)
    ledger.ensure_schema()
    audit = PostgresAuditSink(pg_dsn)
    audit.ensure_schema()
    return MemoryService(
        engine=engine,
        tuples=engine,
        ledger=ledger,
        adapter=InMemoryMemoryAdapter(),
        decider=Decider(engine, audit),
        audit=audit,
    )


def _user(department: str) -> str:
    users = load_json("users.json")
    assert isinstance(users, list)
    return next(u["id"] for u in users if u["department"] == department and not u["intern"])


def _hr_doc() -> dict[str, str]:
    docs = load_json("documents.json")
    assert isinstance(docs, list)
    return next(d for d in docs if d["department"] == "rh" and d["confidentiality"] == "internal")


async def test_demo_scenario(service: MemoryService, engine: OpenFgaEngine) -> None:
    hr_user, store_user = _user("rh"), _user("magasin-lille")
    doc = _hr_doc()
    hr_chain = PrincipalChain.root(hr_user, "agent:assistant", SCOPE)
    store_chain = PrincipalChain.root(store_user, "agent:assistant", SCOPE)

    memory = await service.remember(hr_chain, f"Résumé: {doc['title']}", derived_from=[doc["id"]])
    try:
        assert [m.memory_id for m in await service.recall(hr_chain, doc["topic"])] == [
            memory.memory_id
        ]
        assert await service.recall(store_chain, doc["topic"]) == []

        # Revocation after write: the HR user leaves the HR group.
        membership = (hr_user, "member", "group:rh")
        await engine.delete_tuples([membership])
        try:
            assert await service.recall(hr_chain, doc["topic"]) == []
        finally:
            await engine.write_tuples([membership])
        assert len(await service.recall(hr_chain, doc["topic"])) == 1
    finally:
        await service.forget(hr_chain, memory.memory_id)
