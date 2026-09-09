"""Zep / Graphiti adapter (https://github.com/getzep/zep). Optional extra ``zep-cloud``.

Each HeadOfContext memory becomes one Zep *episode* in the namespace's user graph. Search
returns graph edges (facts) that Zep derived from one or more episodes; every episode behind a
fact is reported as a backend reference so the service can require the provenance of all of
them. A fact whose episodes are not all known to the ledger is dropped by the service.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from headofcontext.core.errors import ConfigurationError
from headofcontext.memory.model import Candidate


class ZepGraphLike(Protocol):
    def add(self, **kwargs: Any) -> Any: ...

    def search(self, **kwargs: Any) -> Any: ...


class ZepLike(Protocol):
    @property
    def graph(self) -> Any: ...


class ZepAdapter:
    name = "zep"

    def __init__(self, client: ZepLike) -> None:
        self._client = client

    @classmethod
    def from_api_key(cls, api_key: str, **kwargs: Any) -> ZepAdapter:
        try:
            from zep_cloud.client import Zep  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise ConfigurationError("install the 'zep-cloud' package to use ZepAdapter") from exc
        return cls(Zep(api_key=api_key, **kwargs))

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        episode = await asyncio.to_thread(
            self._client.graph.add, user_id=namespace, type="text", data=content
        )
        ref = getattr(episode, "uuid_", None) or getattr(episode, "uuid", None)
        if not isinstance(ref, str) or not ref:
            raise RuntimeError("zep did not return an episode uuid")
        return ref

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        result = await asyncio.to_thread(
            self._client.graph.search, user_id=namespace, query=query, limit=limit, scope="edges"
        )
        candidates: list[Candidate] = []
        for edge in getattr(result, "edges", None) or []:
            fact = getattr(edge, "fact", None)
            episodes = tuple(str(e) for e in (getattr(edge, "episodes", None) or []))
            if not isinstance(fact, str) or not episodes:
                continue
            score = getattr(edge, "score", None)
            candidates.append(
                Candidate(
                    content=fact,
                    backend_refs=episodes,
                    score=float(score) if isinstance(score, int | float) else None,
                )
            )
        return candidates[:limit]

    async def delete(self, backend_ref: str, namespace: str) -> None:
        await asyncio.to_thread(self._client.graph.episode.delete, backend_ref)
