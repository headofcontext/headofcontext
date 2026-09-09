"""Mem0 adapter (https://github.com/mem0ai/mem0). Optional extra: ``pip install mem0ai``.

The adapter is typed against the subset of the Mem0 client we use (``add``, ``search``,
``delete``) so it can be tested with a fake and so that installing ``mem0ai`` stays the
deployer's choice. Content is stored verbatim (``infer=False``): the memory tool must never
rewrite what was authorized, and the ledger's content hash must stay meaningful.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from headofcontext.core.errors import ConfigurationError
from headofcontext.memory.model import Candidate


class Mem0Like(Protocol):
    def add(self, messages: Any, **kwargs: Any) -> Any: ...

    def search(self, query: str, **kwargs: Any) -> Any: ...

    def delete(self, memory_id: str) -> Any: ...


class Mem0Adapter:
    name = "mem0"

    def __init__(self, client: Mem0Like) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> Mem0Adapter:
        try:
            from mem0 import Memory as Mem0Memory  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise ConfigurationError("install the 'mem0ai' package to use Mem0Adapter") from exc
        client = Mem0Memory.from_config(config) if config else Mem0Memory()
        return cls(client)

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        response = await asyncio.to_thread(
            self._client.add,
            [{"role": "user", "content": content}],
            user_id=namespace,
            metadata={"hoc_memory_id": memory_id},
            infer=False,
        )
        results = _results(response)
        if not results or "id" not in results[0]:
            raise RuntimeError("mem0 did not return a memory id")
        return str(results[0]["id"])

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        response = await asyncio.to_thread(
            self._client.search, query, user_id=namespace, limit=limit
        )
        candidates: list[Candidate] = []
        for row in _results(response):
            ref = row.get("id")
            content = row.get("memory")
            if not isinstance(ref, str) or not isinstance(content, str):
                continue
            score = row.get("score")
            candidates.append(
                Candidate(
                    content=content,
                    backend_refs=(ref,),
                    score=float(score) if isinstance(score, int | float) else None,
                )
            )
        return candidates[:limit]

    async def delete(self, backend_ref: str, namespace: str) -> None:
        await asyncio.to_thread(self._client.delete, backend_ref)


def _results(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        rows = response.get("results", [])
    elif isinstance(response, list):
        rows = response
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]
