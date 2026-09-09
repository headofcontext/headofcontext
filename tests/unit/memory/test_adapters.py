"""Adapters: in-memory search, and Mem0 / Zep against fakes of the client surface we use."""

from typing import Any

import pytest

from headofcontext.memory import InMemoryMemoryAdapter
from headofcontext.memory.mem0 import Mem0Adapter
from headofcontext.memory.zep import ZepAdapter


class TestInMemory:
    async def test_search_ranks_by_overlap(self) -> None:
        adapter = InMemoryMemoryAdapter()
        a = await adapter.store("memory:a", "grille de salaires vendeurs", "ns")
        await adapter.store("memory:b", "planning magasin lille", "ns")
        hits = await adapter.search("salaires", "ns", limit=5)
        assert [h.backend_refs for h in hits] == [(a,)]
        assert hits[0].content == "grille de salaires vendeurs"

    async def test_namespaces_isolated(self) -> None:
        adapter = InMemoryMemoryAdapter()
        await adapter.store("memory:a", "salaires", "ns1")
        assert await adapter.search("salaires", "ns2", limit=5) == []

    async def test_delete(self) -> None:
        adapter = InMemoryMemoryAdapter()
        ref = await adapter.store("memory:a", "salaires", "ns")
        await adapter.delete(ref, "ns")
        assert await adapter.search("salaires", "ns", limit=5) == []


class FakeMem0:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def add(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"op": "add", "messages": messages, **kwargs})
        mid = f"m0-{len(self.rows)}"
        self.rows[mid] = {
            "id": mid,
            "memory": messages[0]["content"],
            "user_id": kwargs["user_id"],
            "metadata": kwargs.get("metadata"),
        }
        return {"results": [{"id": mid, "memory": messages[0]["content"], "event": "ADD"}]}

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"op": "search", "query": query, **kwargs})
        rows = [
            r
            for r in self.rows.values()
            if r["user_id"] == kwargs["user_id"] and query in r["memory"]
        ]
        return {"results": [{**r, "score": 0.9} for r in rows[: kwargs.get("limit", 10)]]}

    def delete(self, memory_id: str) -> None:
        self.calls.append({"op": "delete", "memory_id": memory_id})
        self.rows.pop(memory_id, None)


class TestMem0:
    async def test_store_is_verbatim_and_tagged(self) -> None:
        client = FakeMem0()
        adapter = Mem0Adapter(client)
        ref = await adapter.store("memory:abc", "grille de salaires", "hoc")
        assert ref == "m0-0"
        call = client.calls[0]
        assert call["infer"] is False  # never let the memory tool rewrite what was authorized
        assert call["user_id"] == "hoc"
        assert call["metadata"]["hoc_memory_id"] == "memory:abc"

    async def test_search_maps_ids(self) -> None:
        client = FakeMem0()
        adapter = Mem0Adapter(client)
        await adapter.store("memory:abc", "grille de salaires", "hoc")
        hits = await adapter.search("salaires", "hoc", limit=5)
        assert hits[0].backend_refs == ("m0-0",)
        assert hits[0].content == "grille de salaires"
        assert hits[0].score == pytest.approx(0.9)

    async def test_delete(self) -> None:
        client = FakeMem0()
        adapter = Mem0Adapter(client)
        ref = await adapter.store("memory:abc", "x", "hoc")
        await adapter.delete(ref, "hoc")
        assert client.rows == {}


class FakeZepEpisode:
    def __init__(self, uuid: str) -> None:
        self.uuid_ = uuid


class FakeZepEdge:
    def __init__(self, fact: str, episodes: list[str]) -> None:
        self.fact = fact
        self.episodes = episodes
        self.score = 0.5


class FakeZepGraph:
    def __init__(self) -> None:
        self.episodes: dict[str, tuple[str, str]] = {}
        self.calls: list[dict[str, Any]] = []
        self.episode = self

    def add(self, **kwargs: Any) -> FakeZepEpisode:
        self.calls.append({"op": "add", **kwargs})
        uuid = f"ep-{len(self.episodes)}"
        self.episodes[uuid] = (kwargs["user_id"], kwargs["data"])
        return FakeZepEpisode(uuid)

    def search(self, **kwargs: Any) -> Any:
        self.calls.append({"op": "search", **kwargs})
        edges = [
            FakeZepEdge(data, [uuid])
            for uuid, (user, data) in self.episodes.items()
            if user == kwargs["user_id"] and kwargs["query"] in data
        ]

        class Result:
            pass

        result = Result()
        result.edges = edges  # type: ignore[attr-defined]
        result.nodes = []  # type: ignore[attr-defined]
        return result

    def delete(self, uuid_: str) -> None:
        self.calls.append({"op": "delete", "uuid": uuid_})
        self.episodes.pop(uuid_, None)


class FakeZep:
    def __init__(self) -> None:
        self.graph = FakeZepGraph()


class TestZep:
    async def test_store_search_delete(self) -> None:
        client = FakeZep()
        adapter = ZepAdapter(client)
        ref = await adapter.store("memory:abc", "grille de salaires", "hoc")
        assert ref == "ep-0"
        assert client.graph.calls[0]["type"] == "text"
        hits = await adapter.search("salaires", "hoc", limit=5)
        assert hits[0].backend_refs == ("ep-0",)
        assert hits[0].content == "grille de salaires"
        await adapter.delete(ref, "hoc")
        assert client.graph.episodes == {}

    async def test_edge_from_several_episodes_keeps_all_refs(self) -> None:
        client = FakeZep()
        adapter = ZepAdapter(client)
        await adapter.store("memory:a", "salaires 2026", "hoc")
        await adapter.store("memory:b", "salaires 2025", "hoc")
        client.graph.search = lambda **kw: type(
            "R", (), {"edges": [FakeZepEdge("salaires", ["ep-0", "ep-1"])], "nodes": []}
        )()  # type: ignore[method-assign]
        hits = await adapter.search("salaires", "hoc", limit=5)
        assert hits[0].backend_refs == ("ep-0", "ep-1")
