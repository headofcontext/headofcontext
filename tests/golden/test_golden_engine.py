"""Golden set at the engine level: OpenFGA must agree with the fixture oracle on every document.

This validates the fixtures and the model. The end-to-end run (through read.filter and the
connectors) lives in test_golden_read.py and stays red until phase 3.
"""

from collections.abc import AsyncIterator

import pytest

from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
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


async def test_engine_matches_oracle(engine: OpenFgaEngine, case: GoldenCase) -> None:
    docs = [*case.expected_visible, *case.expected_invisible]
    results = await engine.batch_check(case.subject, [("viewer", d) for d in docs])
    leaked = [
        d
        for d, ok in zip(
            case.expected_invisible, results[len(case.expected_visible) :], strict=True
        )
        if ok
    ]
    missing = [
        d
        for d, ok in zip(case.expected_visible, results[: len(case.expected_visible)], strict=True)
        if not ok
    ]
    assert leaked == [], f"{case.subject} must not see {leaked}"
    assert missing == [], f"{case.subject} should see {missing}"
