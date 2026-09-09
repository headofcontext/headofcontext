"""PostgreSQL memory adapter (ADR 0021): durable content, full-text search, optional embeddings.

Storage and search only. Extraction, consolidation and graphs belong to Mem0 or Zep; this
adapter is the reference backend for a self-hosted deployment with no third tool.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from headofcontext.core.blocking import offload
from headofcontext.core.errors import ConfigurationError, Unavailable
from headofcontext.db.schema import MEMORY_CONTENT_SCHEMA
from headofcontext.db.store import PostgresStore, Row
from headofcontext.memory.model import Candidate

Embedder = Callable[[str], Sequence[float]]

_EMBEDDING_TABLE = "memory_embedding"
_TS_CONFIG = "simple"  # language-agnostic: no stemming assumptions about the content

# Statements are assembled once from module constants; every value travels as a parameter.
_INSERT_CONTENT = (
    "INSERT INTO memory_content (namespace, backend_ref, memory_id, content, tsv, created_at) "  # noqa: S608
    f"VALUES (%s, %s, %s, %s, to_tsvector('{_TS_CONFIG}', %s), %s)"
)
_INSERT_EMBEDDING = (
    f"INSERT INTO {_EMBEDDING_TABLE} (namespace, backend_ref, embedding) "  # noqa: S608
    "VALUES (%s, %s, %s::vector)"
)
_SEARCH_EMBEDDING = (
    "SELECT c.backend_ref, c.content, 1 - (e.embedding <=> %s::vector) "  # noqa: S608
    f"FROM {_EMBEDDING_TABLE} e JOIN memory_content c "
    "ON c.namespace = e.namespace AND c.backend_ref = e.backend_ref "
    "WHERE e.namespace = %s ORDER BY e.embedding <=> %s::vector, c.backend_ref LIMIT %s"
)
_SEARCH_TEXT = (
    "SELECT backend_ref, content, "  # noqa: S608
    f"ts_rank(tsv, websearch_to_tsquery('{_TS_CONFIG}', %s)) "
    "FROM memory_content WHERE namespace = %s "
    f"AND tsv @@ websearch_to_tsquery('{_TS_CONFIG}', %s) "
    "ORDER BY 3 DESC, backend_ref LIMIT %s"
)
_DELETE = "DELETE FROM memory_content WHERE namespace = %s AND backend_ref = %s"


class MemoryContentUnavailable(Unavailable):
    reason = "backend_unavailable"


class PostgresMemoryAdapter(PostgresStore):
    name = "postgres"
    unavailable = MemoryContentUnavailable
    schema = MEMORY_CONTENT_SCHEMA

    def __init__(self, conninfo: str, *, embedder: Embedder | None = None) -> None:
        super().__init__(conninfo)
        self.embedder = embedder
        if embedder is not None and not self._has_embeddings():
            raise ConfigurationError(
                "an embedder needs the memory_embedding table: pgvector is not available on "
                "this PostgreSQL (ADR 0021)"
            )

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        return await offload(self._store, memory_id, content, namespace)

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        return await offload(self._search, query, namespace, limit)

    async def delete(self, backend_ref: str, namespace: str) -> None:
        await offload(self._run, _DELETE, (namespace, backend_ref))

    def _store(self, memory_id: str, content: str, namespace: str) -> str:
        ref = uuid.uuid4().hex
        embedding = _literal(self.embedder(content)) if self.embedder is not None else None
        with self._transaction(failure="memory content write failed") as conn:
            conn.execute(
                _INSERT_CONTENT, (namespace, ref, memory_id, content, content, datetime.now(UTC))
            )
            if embedding is not None:
                conn.execute(_INSERT_EMBEDDING, (namespace, ref, embedding))
        return ref

    def _search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        rows: list[Row] = []
        if self.embedder is not None:
            vector = _literal(self.embedder(query))
            if vector != "[]" and any(float(v) for v in vector[1:-1].split(",")):
                rows = self._query(_SEARCH_EMBEDDING, (vector, namespace, vector, limit))
        if not rows:
            rows = self._query(_SEARCH_TEXT, (query, namespace, query, limit))
        return [
            Candidate(content=str(content), backend_refs=(str(ref),), score=float(str(score)))
            for ref, content, score in rows
        ]

    def _has_embeddings(self) -> bool:
        row = self._one("SELECT to_regclass(%s)", (_EMBEDDING_TABLE,))
        return row is not None and row[0] is not None


def _literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


__all__ = ["Embedder", "MemoryContentUnavailable", "PostgresMemoryAdapter"]
