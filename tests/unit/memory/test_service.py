"""MemoryService: remember / recall / forget with provenance re-checked at every read (ADR 0007)."""

from collections.abc import Callable, Sequence

import pytest

from headofcontext.audit import EventKind, InMemoryAuditSink
from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.errors import MemoryDenied
from headofcontext.memory import (
    Candidate,
    InMemoryLedger,
    InMemoryMemoryAdapter,
    LedgerUnavailable,
    Memory,
    MemoryService,
)
from tests.unit.memory.conftest import HR_DOC, LILLE_DOC, FakeGraph, chain_for

SALARY_NOTE = "La grille de salaires 2026 prévoit +3 % pour les vendeurs."


class TestRemember:
    async def test_stores_content_ledger_and_tuples(
        self,
        service: MemoryService,
        graph: FakeGraph,
        ledger: InMemoryLedger,
        adapter: InMemoryMemoryAdapter,
    ) -> None:
        memory = await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        assert isinstance(memory, Memory)
        assert memory.memory_id.startswith("memory:")
        assert memory.derived_from == (HR_DOC,)
        assert memory.written_for == "user:alice"
        assert memory.written_by == "agent:assistant"
        record = ledger.get(memory.memory_id)
        assert record is not None and record.derived_from == (HR_DOC,)
        assert (HR_DOC, "derived_from", memory.memory_id) in graph.tuples
        assert ("agent:assistant", "writer", memory.memory_id) in graph.tuples
        assert adapter.count() == 1

    async def test_subject_must_see_every_source(
        self, service: MemoryService, adapter: InMemoryMemoryAdapter
    ) -> None:
        with pytest.raises(MemoryDenied) as exc:
            await service.remember(
                chain_for("user:bob"), SALARY_NOTE, derived_from=[LILLE_DOC, HR_DOC]
            )
        assert exc.value.reason == "not_related"
        assert adapter.count() == 0

    async def test_scope_must_allow_remember_on_sources(self, service: MemoryService) -> None:
        scope = Scope.of(
            Capability(Kind.READ, "document:*"), Capability(Kind.REMEMBER, "document:lille/*")
        )
        with pytest.raises(MemoryDenied) as exc:
            await service.remember(
                chain_for("user:alice", scope=scope), SALARY_NOTE, derived_from=[HR_DOC]
            )
        assert exc.value.reason == "scope_excluded"

    async def test_no_provenance_needs_remember_on_memory(self, service: MemoryService) -> None:
        memory = await service.remember(
            chain_for("user:alice"), "Alice préfère les réunions le matin.", derived_from=[]
        )
        assert memory.derived_from == ()
        scope = Scope.of(Capability(Kind.REMEMBER, "document:*"))
        with pytest.raises(MemoryDenied):
            await service.remember(chain_for("user:alice", scope=scope), "x", derived_from=[])

    async def test_engine_down_denies(self, service: MemoryService, graph: FakeGraph) -> None:
        graph.down = True
        with pytest.raises(MemoryDenied) as exc:
            await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        assert exc.value.reason == "engine_unavailable"

    async def test_adapter_failure_rolls_back(
        self,
        service: MemoryService,
        graph: FakeGraph,
        ledger: InMemoryLedger,
        adapter: InMemoryMemoryAdapter,
    ) -> None:
        adapter.fail_next_store = True
        with pytest.raises(RuntimeError):
            await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        assert not any(t[1] == "derived_from" for t in graph.tuples)
        assert ledger.count() == 0

    async def test_audited_without_content(
        self, service: MemoryService, audit: InMemoryAuditSink
    ) -> None:
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        kinds = [e.kind for e in audit.events]
        assert EventKind.DECISION in kinds and EventKind.MEMORY_WRITTEN in kinds
        assert all("salaires" not in e.canonical_json() for e in audit.events)
        written = next(e for e in audit.events if e.kind is EventKind.MEMORY_WRITTEN)
        assert written.args_hash is not None

    async def test_duplicate_sources_collapse(self, service: MemoryService) -> None:
        memory = await service.remember(
            chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC, HR_DOC]
        )
        assert memory.derived_from == (HR_DOC,)


class TestRecall:
    async def test_author_subject_recalls(self, service: MemoryService) -> None:
        stored = await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        found = await service.recall(chain_for("user:alice"), "salaires")
        assert [m.memory_id for m in found] == [stored.memory_id]
        assert found[0].content == SALARY_NOTE

    async def test_other_subject_without_source_access_gets_nothing(
        self, service: MemoryService
    ) -> None:
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        assert await service.recall(chain_for("user:bob"), "salaires") == []

    async def test_other_subject_with_source_access_can_recall(
        self, service: MemoryService
    ) -> None:
        await service.remember(
            chain_for("user:alice"), "Planning Lille: ouverture 9h.", derived_from=[LILLE_DOC]
        )
        found = await service.recall(chain_for("user:bob"), "planning")
        assert len(found) == 1

    async def test_all_sources_required(self, service: MemoryService) -> None:
        await service.remember(
            chain_for("user:alice"), "Planning et salaires", derived_from=[LILLE_DOC, HR_DOC]
        )
        assert await service.recall(chain_for("user:bob"), "planning") == []
        assert len(await service.recall(chain_for("user:alice"), "planning")) == 1

    async def test_no_provenance_readable_by_written_for_only(self, service: MemoryService) -> None:
        await service.remember(chain_for("user:alice"), "Alice aime le café.", derived_from=[])
        assert len(await service.recall(chain_for("user:alice"), "café")) == 1
        assert await service.recall(chain_for("user:bob"), "café") == []

    async def test_actor_needs_read_scope_on_memory(self, service: MemoryService) -> None:
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        scope = Scope.of(Capability(Kind.READ, "document:*"))
        assert await service.recall(chain_for("user:alice", scope=scope), "salaires") == []

    async def test_engine_down_returns_nothing(
        self, service: MemoryService, graph: FakeGraph
    ) -> None:
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        graph.down = True
        assert await service.recall(chain_for("user:alice"), "salaires") == []

    async def test_every_candidate_is_audited(
        self, service: MemoryService, audit: InMemoryAuditSink
    ) -> None:
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        await service.recall(chain_for("user:bob"), "salaires")
        reads = [e for e in audit.events if e.kind is EventKind.MEMORY_READ]
        assert len(reads) == 1
        assert reads[0].subject == "user:bob" and reads[0].outcome == "DENY"

    async def test_limit_respected(self, service: MemoryService) -> None:
        for i in range(5):
            await service.remember(
                chain_for("user:alice"), f"note salaires {i}", derived_from=[HR_DOC]
            )
        assert len(await service.recall(chain_for("user:alice"), "salaires", limit=3)) == 3


class TestForget:
    async def test_written_for_can_forget(
        self,
        service: MemoryService,
        graph: FakeGraph,
        ledger: InMemoryLedger,
        adapter: InMemoryMemoryAdapter,
    ) -> None:
        memory = await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        await service.forget(chain_for("user:alice"), memory.memory_id)
        assert ledger.get(memory.memory_id) is None
        assert adapter.count() == 0
        assert not any(t[2] == memory.memory_id for t in graph.tuples)
        assert await service.recall(chain_for("user:alice"), "salaires") == []

    async def test_other_subject_cannot_forget(
        self, service: MemoryService, ledger: InMemoryLedger
    ) -> None:
        memory = await service.remember(
            chain_for("user:alice"), "Planning Lille", derived_from=[LILLE_DOC]
        )
        with pytest.raises(MemoryDenied):
            await service.forget(chain_for("user:bob"), memory.memory_id)
        assert ledger.get(memory.memory_id) is not None

    async def test_unknown_memory_is_denied_not_error(self, service: MemoryService) -> None:
        with pytest.raises(MemoryDenied):
            await service.forget(chain_for("user:alice"), "memory:nope")


def _permits(refuse: set[str]) -> Callable[[Kind, Sequence[str]], dict[str, bool]]:
    def permits(_operation: Kind, resources: Sequence[str]) -> dict[str, bool]:
        return {r: r not in refuse for r in resources}

    return permits


class TestTokenPermits:
    """ADR 0018: the token's own checks are asked where the resources become known."""

    async def test_remember_refused_source_is_denied_and_audited(
        self, service: MemoryService, audit: InMemoryAuditSink, adapter: InMemoryMemoryAdapter
    ) -> None:
        with pytest.raises(MemoryDenied) as exc:
            await service.remember(
                chain_for("user:alice"),
                SALARY_NOTE,
                derived_from=[HR_DOC],
                permits=_permits({HR_DOC}),
            )
        assert exc.value.reason == "token_check_failed"
        assert adapter.count() == 0
        last = audit.events[-1]
        assert last.kind is EventKind.MEMORY_WRITTEN and last.outcome == "DENY"
        assert last.reason == "token_check_failed"

    async def test_remember_without_sources_asks_about_the_memory_id(
        self, service: MemoryService
    ) -> None:
        def refuse_memories(_op: Kind, resources: Sequence[str]) -> dict[str, bool]:
            return {r: not r.startswith("memory:") for r in resources}

        with pytest.raises(MemoryDenied):
            await service.remember(
                chain_for("user:alice"), "note", derived_from=[], permits=refuse_memories
            )
        stored = await service.remember(
            chain_for("user:alice"), "note", derived_from=[], permits=_permits(set())
        )
        assert stored.memory_id.startswith("memory:")

    async def test_recall_refused_source_drops_the_memory(
        self, service: MemoryService, audit: InMemoryAuditSink
    ) -> None:
        stored = await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
        assert (
            await service.recall(chain_for("user:alice"), "salaires", permits=_permits({HR_DOC}))
            == []
        )
        assert (
            await service.recall(
                chain_for("user:alice"), "salaires", permits=_permits({stored.memory_id})
            )
            == []
        )
        denied = [e for e in audit.events if e.kind is EventKind.MEMORY_READ]
        assert [e.reason for e in denied] == ["token_check_failed", "token_check_failed"]
        assert (
            len(await service.recall(chain_for("user:alice"), "salaires", permits=_permits(set())))
            == 1
        )

    async def test_forget_refused_by_token(self, service: MemoryService) -> None:
        stored = await service.remember(chain_for("user:alice"), "note", derived_from=[])
        with pytest.raises(MemoryDenied) as exc:
            await service.forget(
                chain_for("user:alice"), stored.memory_id, permits=_permits({stored.memory_id})
            )
        assert exc.value.reason == "token_check_failed"
        await service.forget(chain_for("user:alice"), stored.memory_id, permits=_permits(set()))


class TestJournaledRefusals:
    """ADR 0018: nothing leaves `recall` unlogged, not even an empty answer."""

    async def test_backend_failure_is_audited(
        self, service: MemoryService, audit: InMemoryAuditSink, adapter: InMemoryMemoryAdapter
    ) -> None:
        async def boom(query: str, namespace: str, limit: int) -> list[Candidate]:
            raise RuntimeError("backend down")

        adapter.search = boom  # type: ignore[method-assign]
        assert await service.recall(chain_for("user:alice"), "anything") == []
        last = audit.events[-1]
        assert last.kind is EventKind.MEMORY_READ and last.outcome == "DENY"
        assert last.reason == "backend_unavailable" and last.resource == "memory:*"

    async def test_candidate_without_ledger_row_is_audited(
        self, service: MemoryService, audit: InMemoryAuditSink, adapter: InMemoryMemoryAdapter
    ) -> None:
        await adapter.store("memory:orphan", "content nobody vouches for", "hoc")
        assert await service.recall(chain_for("user:alice"), "content") == []
        last = audit.events[-1]
        assert last.kind is EventKind.MEMORY_READ and last.outcome == "DENY"
        assert last.reason == "no_provenance"


async def test_ledger_failure_leaves_no_content_in_the_backend(
    service: MemoryService, adapter: InMemoryMemoryAdapter, ledger: InMemoryLedger
) -> None:
    def boom(record: object) -> None:
        raise LedgerUnavailable("ledger down")

    ledger.put = boom  # type: ignore[method-assign]
    with pytest.raises(LedgerUnavailable):
        await service.remember(chain_for("user:alice"), SALARY_NOTE, derived_from=[HR_DOC])
    assert adapter.count() == 0
