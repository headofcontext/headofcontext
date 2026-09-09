"""OpenFGA adapter against the real container with the ACME tuples."""

from collections.abc import AsyncIterator

import pytest

from headofcontext.core.errors import EngineUnavailable
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
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


def _user_in(department: str) -> str:
    users = load_json("users.json")
    assert isinstance(users, list)
    return next(u["id"] for u in users if u["department"] == department and not u["intern"])


def _doc(confidentiality: str, department: str) -> str:
    docs = load_json("documents.json")
    assert isinstance(docs, list)
    return next(
        d["id"]
        for d in docs
        if d["confidentiality"] == confidentiality and d["department"] == department
    )


async def test_department_member_sees_internal_document(engine: OpenFgaEngine) -> None:
    assert await engine.check(_user_in("rh"), "viewer", _doc("internal", "rh")) is True
    assert await engine.check(_user_in("it"), "viewer", _doc("internal", "rh")) is False


async def test_direction_inherits_rh_and_finance(engine: OpenFgaEngine) -> None:
    boss = _user_in("direction")
    assert await engine.check(boss, "viewer", _doc("internal", "rh")) is True
    assert await engine.check(boss, "viewer", _doc("internal", "finance")) is True
    assert await engine.check(boss, "viewer", _doc("internal", "it")) is False


async def test_public_document_visible_to_everyone(engine: OpenFgaEngine) -> None:
    doc = _doc("public", "finance")
    for dept in ("rh", "it", "magasin-lille", "juridique"):
        assert await engine.check(_user_in(dept), "viewer", doc) is True


async def test_batch_check_and_list_objects_agree(engine: OpenFgaEngine) -> None:
    user = _user_in("magasin-lyon")
    listed = set(await engine.list_objects(user, "viewer", "document"))
    probe = [
        _doc("internal", "magasin-lyon"),
        _doc("internal", "magasin-lille"),
        _doc("public", "rh"),
    ]
    results = await engine.batch_check(user, [("viewer", d) for d in probe])
    assert results == [d in listed for d in probe]
    assert results == [True, False, True]


async def test_tools(engine: OpenFgaEngine) -> None:
    assert await engine.check(_user_in("rh"), "can_invoke", "tool:hr.export") is True
    assert await engine.check(_user_in("it"), "can_invoke", "tool:hr.export") is False
    assert await engine.check(_user_in("it"), "can_invoke", "tool:mail.send") is True


async def test_i5_unreachable_engine_raises(openfga_store: FgaStore) -> None:
    engine = OpenFgaEngine.connect("http://localhost:1", openfga_store.store_id, AlwaysFresh())
    try:
        with pytest.raises(EngineUnavailable):
            await engine.check("user:nobody", "viewer", "document:x")
    finally:
        await engine.close()
