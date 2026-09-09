"""Memory value objects and the two Protocols the service composes (ADR 0007)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from headofcontext.core.permits import ResourcePermits


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Provenance ledger row. Never contains the content, only its hash."""

    memory_id: str
    written_for: str
    written_by: str
    derived_from: tuple[str, ...]
    content_hash: str
    backend: str
    backend_ref: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Memory:
    """What callers get back. The content is excluded from repr so it never lands in logs."""

    memory_id: str
    content: str = field(repr=False)
    derived_from: tuple[str, ...] = ()
    written_for: str = ""
    written_by: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A search hit from a backend: content plus the backend references it was built from."""

    content: str = field(repr=False)
    backend_refs: tuple[str, ...] = ()
    score: float | None = None


class MemoryAdapter(Protocol):
    """Content storage and similarity search. Knows nothing about rights."""

    @property
    def name(self) -> str: ...

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        """Persist verbatim content; return the backend reference."""
        ...

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]: ...

    async def delete(self, backend_ref: str, namespace: str) -> None: ...


class ProvenanceLedger(Protocol):
    """HeadOfContext-owned provenance. Raises on backend failure; never returns partial data."""

    def put(self, record: MemoryRecord) -> None: ...

    def get(self, memory_id: str) -> MemoryRecord | None: ...

    def get_by_backend_ref(self, backend: str, backend_ref: str) -> MemoryRecord | None: ...

    def delete(self, memory_id: str) -> None: ...


__all__ = [
    "Candidate",
    "Memory",
    "MemoryAdapter",
    "MemoryRecord",
    "ProvenanceLedger",
    "ResourcePermits",
]
