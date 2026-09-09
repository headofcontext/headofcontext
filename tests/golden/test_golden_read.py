"""Golden set through read.filter_items over the fixture tuples (engine-level store).

Every question is asked for its subject; the filtered result must contain every expected visible
document and none of the expected invisible ones. Zero leaks is the only acceptable outcome.
"""

from collections.abc import AsyncIterator

import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.read import filter_items
from tests.golden.conftest import GoldenCase
from tests.services import FgaStore

pytestmark = [pytest.mark.golden, pytest.mark.integration]


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


@pytest.mark.parametrize("strategy", ["batch", "list_objects"])
async def test_read_filter_zero_leak(
    engine: OpenFgaEngine, case: GoldenCase, strategy: str
) -> None:
    items = [{"id": d} for d in (*case.expected_visible, *case.expected_invisible)]
    result = await filter_items(
        case.chain(),
        items,
        engine=engine,
        audit=InMemoryAuditSink(),
        strategy=strategy,  # type: ignore[arg-type]
    )
    kept_ids = {item["id"] for item in result.kept}
    assert kept_ids.isdisjoint(case.expected_invisible), "leak"
    assert kept_ids == set(case.expected_visible)
