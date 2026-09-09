"""Adapter behaviour with a fake SDK client. Real OpenFGA is exercised in tests/integration."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from headofcontext.core.errors import ConnectorStale, EngineUnavailable, InvariantViolation
from headofcontext.engines.freshness import AlwaysFresh, InMemoryConnectorState
from headofcontext.engines.openfga import OpenFgaEngine
from tests.conftest import FrozenClock


class FakeClient:
    def __init__(self, *, allowed: bool = True, fail: bool = False) -> None:
        self.allowed = allowed
        self.fail = fail
        self.requests: list[Any] = []

    async def check(self, body: Any, options: Any = None) -> Any:
        self.requests.append(body)
        if self.fail:
            raise ConnectionError("refused")
        return SimpleNamespace(allowed=self.allowed)

    async def batch_check(self, body: Any, options: Any = None) -> Any:
        self.requests.append(body)
        if self.fail:
            raise ConnectionError("refused")
        result = []
        for item in body.checks:
            if item.object.endswith("error"):
                result.append(
                    SimpleNamespace(correlation_id=item.correlation_id, allowed=True, error="boom")
                )
            else:
                result.append(
                    SimpleNamespace(
                        correlation_id=item.correlation_id,
                        allowed=self.allowed and item.object.endswith("yes"),
                        error=None,
                    )
                )
        return SimpleNamespace(result=result)

    async def list_objects(self, body: Any, options: Any = None) -> Any:
        self.requests.append(body)
        if self.fail:
            raise ConnectionError("refused")
        return SimpleNamespace(objects=["document:a", "document:b"])

    async def close(self) -> None:
        return None


async def test_check_passes_through() -> None:
    client = FakeClient(allowed=True)
    engine = OpenFgaEngine(client, AlwaysFresh())  # type: ignore[arg-type]
    assert await engine.check("user:alice", "viewer", "document:x") is True
    request = client.requests[0]
    assert (request.user, request.relation, request.object) == (
        "user:alice",
        "viewer",
        "document:x",
    )


async def test_i5_client_failure_is_engine_unavailable() -> None:
    engine = OpenFgaEngine(FakeClient(fail=True), AlwaysFresh())  # type: ignore[arg-type]
    with pytest.raises(EngineUnavailable):
        await engine.check("user:alice", "viewer", "document:x")
    with pytest.raises(EngineUnavailable):
        await engine.batch_check("user:alice", [("viewer", "document:x")])
    with pytest.raises(EngineUnavailable):
        await engine.list_objects("user:alice", "viewer", "document")


async def test_i3_non_user_subject_never_reaches_openfga() -> None:
    client = FakeClient()
    engine = OpenFgaEngine(client, AlwaysFresh())  # type: ignore[arg-type]
    for subject in ("agent:a", "group:rh", "alice"):
        with pytest.raises(InvariantViolation):
            await engine.check(subject, "viewer", "document:x")
    assert client.requests == []


async def test_batch_check_keeps_order_and_denies_errored_items() -> None:
    engine = OpenFgaEngine(FakeClient(), AlwaysFresh())  # type: ignore[arg-type]
    results = await engine.batch_check(
        "user:alice",
        [("viewer", "document:yes"), ("viewer", "document:no"), ("viewer", "document:error")],
    )
    assert results == [True, False, False]
    assert await engine.batch_check("user:alice", []) == []


async def test_list_objects() -> None:
    engine = OpenFgaEngine(FakeClient(), AlwaysFresh())  # type: ignore[arg-type]
    assert await engine.list_objects("user:alice", "viewer", "document") == [
        "document:a",
        "document:b",
    ]


async def test_t8_stale_connector_denies_before_calling_engine(clock: FrozenClock) -> None:
    client = FakeClient()
    state = InMemoryConnectorState(timedelta(minutes=5), clock)
    state.mark_synced("nextcloud")
    engine = OpenFgaEngine(client, state)  # type: ignore[arg-type]
    assert await engine.check("user:alice", "viewer", "document:x") is True
    clock.tick(minutes=6)
    with pytest.raises(ConnectorStale):
        await engine.check("user:alice", "viewer", "document:x")
    assert len(client.requests) == 1


async def test_t8_registered_but_never_synced_is_stale(clock: FrozenClock) -> None:
    state = InMemoryConnectorState(timedelta(minutes=5), clock)
    state.register("pipeshub")
    with pytest.raises(ConnectorStale):
        state.assert_fresh()


class FakeTupleClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.tuples: set[tuple[str, str, str]] = set()

    async def write_tuples(self, body: Any, options: Any = None) -> Any:
        if self.fail:
            raise ConnectionError("refused")
        for t in body:
            self.tuples.add((t.user, t.relation, t.object))

    async def delete_tuples(self, body: Any, options: Any = None) -> Any:
        if self.fail:
            raise ConnectionError("refused")
        for t in body:
            self.tuples.discard((t.user, t.relation, t.object))

    async def read(self, body: Any, options: Any = None) -> Any:
        if self.fail:
            raise ConnectionError("refused")
        found = [
            SimpleNamespace(key=SimpleNamespace(user=u, relation=r, object=o))
            for (u, r, o) in sorted(self.tuples)
            if (body.object is None or o == body.object)
            and (body.relation is None or r == body.relation)
        ]
        return SimpleNamespace(tuples=found, continuation_token="")


async def test_tuple_store_roundtrip() -> None:
    client = FakeTupleClient()
    engine = OpenFgaEngine(client, AlwaysFresh())  # type: ignore[arg-type]
    await engine.write_tuples(
        [("document:d1", "derived_from", "memory:m1"), ("agent:a", "writer", "memory:m1")]
    )
    assert await engine.read_tuples("memory:m1", "derived_from") == [
        ("document:d1", "derived_from", "memory:m1")
    ]
    await engine.delete_tuples([("document:d1", "derived_from", "memory:m1")])
    assert await engine.read_tuples("memory:m1", "derived_from") == []


async def test_tuple_store_validates_refs() -> None:
    engine = OpenFgaEngine(FakeTupleClient(), AlwaysFresh())  # type: ignore[arg-type]
    with pytest.raises(InvariantViolation):
        await engine.write_tuples([("document:d1", "derived_from", 'memory:m1"); drop')])
    with pytest.raises(InvariantViolation):
        await engine.write_tuples([("document:d1", "derived from", "memory:m1")])


async def test_tuple_store_failure_is_engine_unavailable() -> None:
    client = FakeTupleClient()
    client.fail = True
    engine = OpenFgaEngine(client, AlwaysFresh())  # type: ignore[arg-type]
    with pytest.raises(EngineUnavailable):
        await engine.write_tuples([("document:d1", "derived_from", "memory:m1")])
    with pytest.raises(EngineUnavailable):
        await engine.read_tuples("memory:m1", "derived_from")


class FlakyClient(FakeClient):
    """Fails the first N calls with a transport error, then answers normally."""

    def __init__(self, failures: int) -> None:
        super().__init__(allowed=True)
        self.failures = failures
        self.attempts = 0

    async def check(self, body: Any, options: Any = None) -> Any:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionError("transient")
        return await super().check(body, options)

    async def list_objects(self, body: Any, options: Any = None) -> Any:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionError("transient")
        return await super().list_objects(body, options)


async def test_one_transient_failure_is_retried() -> None:
    client = FlakyClient(failures=1)
    engine = OpenFgaEngine(client, AlwaysFresh(), retry_delay_seconds=0)  # type: ignore[arg-type]
    assert await engine.check("user:alice", "viewer", "document:x") is True
    assert client.attempts == 2


async def test_persistent_failure_still_denies() -> None:
    client = FlakyClient(failures=5)
    engine = OpenFgaEngine(client, AlwaysFresh(), retry_delay_seconds=0)  # type: ignore[arg-type]
    with pytest.raises(EngineUnavailable):
        await engine.list_objects("user:alice", "viewer", "document")
    assert client.attempts == 2  # one retry, never more


async def test_retries_can_be_disabled() -> None:
    client = FlakyClient(failures=1)
    engine = OpenFgaEngine(client, AlwaysFresh(), retries=0)  # type: ignore[arg-type]
    with pytest.raises(EngineUnavailable):
        await engine.check("user:alice", "viewer", "document:x")
    assert client.attempts == 1
