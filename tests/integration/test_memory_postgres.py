"""PostgresMemoryAdapter against the real PostgreSQL (ADR 0021): durable, namespaced, searchable."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import psycopg
import pytest

from headofcontext.core.errors import ConfigurationError
from headofcontext.db import migrate
from headofcontext.memory.postgres import PostgresMemoryAdapter

pytestmark = pytest.mark.integration


@pytest.fixture
def adapter(pg_dsn: str) -> PostgresMemoryAdapter:
    migrate(pg_dsn)
    return PostgresMemoryAdapter(pg_dsn)


@pytest.fixture
def namespace() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


async def test_store_search_delete_are_durable_and_namespaced(
    adapter: PostgresMemoryAdapter, namespace: str, pg_dsn: str
) -> None:
    ref = await adapter.store("memory:a", "La grille de salaires 2026 prévoit +3 %", namespace)
    await adapter.store("memory:b", "Planning du magasin de Lille: ouverture 9h", namespace)
    await adapter.store("memory:c", "grille de salaires d'un autre espace", namespace + "-other")

    found = await adapter.search("grille salaires", namespace, 10)
    assert [c.backend_refs for c in found] == [(ref,)]
    assert found[0].content.startswith("La grille") and found[0].score > 0
    assert await adapter.search("ouverture Lille", namespace, 10)
    assert await adapter.search("rien de tel", namespace, 10) == []

    # A second adapter instance (another replica) sees the same rows.
    other = PostgresMemoryAdapter(pg_dsn)
    assert [c.backend_refs for c in await other.search("salaires", namespace, 10)] == [(ref,)]

    await adapter.delete(ref, namespace)
    assert await adapter.search("grille salaires", namespace, 10) == []
    await adapter.delete("does-not-exist", namespace)  # idempotent
    adapter.close()
    other.close()


async def test_search_never_returns_more_than_limit(
    adapter: PostgresMemoryAdapter, namespace: str
) -> None:
    for i in range(5):
        await adapter.store(f"memory:{i}", f"note salaires numéro {i}", namespace)
    assert len(await adapter.search("salaires", namespace, 3)) == 3
    adapter.close()


def _embed(text: str) -> Sequence[float]:
    """A toy embedder: (has 'salaire', has 'lille', has 'budget')."""
    t = text.lower()
    return [float("salaire" in t), float("lille" in t), float("budget" in t)]


async def test_embedder_orders_by_similarity_when_pgvector_is_available(
    pg_dsn: str, namespace: str
) -> None:
    migrate(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        available = conn.execute("SELECT to_regclass('memory_embedding')").fetchone()
    if available is None or available[0] is None:
        pytest.skip("pgvector not available on this PostgreSQL")
    adapter = PostgresMemoryAdapter(pg_dsn, embedder=_embed)
    await adapter.store("memory:s", "Le salaire du directeur", namespace)
    lille = await adapter.store("memory:l", "Magasin de Lille, horaires", namespace)
    await adapter.store("memory:b", "Budget 2027", namespace)
    found = await adapter.search("ouverture du magasin à Lille", namespace, 2)
    assert found[0].backend_refs == (lille,)
    assert len(found) == 2
    adapter.close()


def test_embedder_without_pgvector_table_is_refused(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headofcontext.memory import postgres as module

    monkeypatch.setattr(module, "_EMBEDDING_TABLE", "memory_embedding_missing")
    with pytest.raises(ConfigurationError, match="pgvector"):
        PostgresMemoryAdapter(pg_dsn, embedder=_embed)
