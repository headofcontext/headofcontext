"""In-process adapter with naive keyword search. For tests, demos and the quickstart."""

from __future__ import annotations

import re
import uuid

from headofcontext.memory.model import Candidate

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) > 2}


class InMemoryMemoryAdapter:
    name = "inmemory"

    def __init__(self) -> None:
        self._rows: dict[
            tuple[str, str], tuple[str, str]
        ] = {}  # (namespace, ref) -> (memory_id, content)
        self.fail_next_store = False

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        if self.fail_next_store:
            self.fail_next_store = False
            raise RuntimeError("backend write failed")
        ref = uuid.uuid4().hex
        self._rows[(namespace, ref)] = (memory_id, content)
        return ref

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        wanted = _tokens(query)
        scored: list[tuple[float, str, str]] = []
        for (ns, ref), (_, content) in self._rows.items():
            if ns != namespace:
                continue
            overlap = len(wanted & _tokens(content))
            if overlap:
                scored.append((overlap / max(len(wanted), 1), ref, content))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [Candidate(content=c, backend_refs=(ref,), score=s) for s, ref, c in scored[:limit]]

    async def delete(self, backend_ref: str, namespace: str) -> None:
        self._rows.pop((namespace, backend_ref), None)

    def count(self) -> int:
        return len(self._rows)
