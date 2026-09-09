"""MemoryService: remember / recall / forget with provenance re-checked on every read (ADR 0007).

Trust model, in one sentence: the memory tool stores text, HeadOfContext owns the provenance
(ledger + OpenFGA tuples), and a memory is returned only when the *current* subject may still
view every source it derives from.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence

from headofcontext.core import (
    Action,
    AuthzEngine,
    Clock,
    Decider,
    Kind,
    Outcome,
    PrincipalChain,
    SystemClock,
    Tuple,
    TupleStore,
)
from headofcontext.core.blocking import offload
from headofcontext.core.errors import HocError, MemoryDenied
from headofcontext.core.events import AuditEvent, AuditSink, EventKind
from headofcontext.core.refs import validate_ref
from headofcontext.memory.model import (
    Candidate,
    Memory,
    MemoryAdapter,
    MemoryRecord,
    ProvenanceLedger,
    ResourcePermits,
)

log = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "hoc"
DEFAULT_LIMIT = 10
MAX_LIMIT = 50


class MemoryService:
    def __init__(
        self,
        *,
        engine: AuthzEngine,
        tuples: TupleStore,
        ledger: ProvenanceLedger,
        adapter: MemoryAdapter,
        decider: Decider,
        audit: AuditSink,
        clock: Clock | None = None,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        self._engine = engine
        self._tuples = tuples
        self._ledger = ledger
        self._adapter = adapter
        self._decider = decider
        self._audit = audit
        self._clock = clock or SystemClock()
        self._namespace = namespace

    # -- write -------------------------------------------------------------------------------

    async def remember(
        self,
        chain: PrincipalChain,
        content: str,
        *,
        derived_from: Sequence[str],
        permits: ResourcePermits | None = None,
    ) -> Memory:
        sources = _dedupe(derived_from)
        for source in sources:
            validate_ref(source, "document")
        memory_id = f"memory:{uuid.uuid4().hex}"
        refused = await offload(_refused, permits, Kind.REMEMBER, sources or (memory_id,))
        if refused:
            # The token's own checks refuse a source (ADR 0018): denied before anything is written.
            await offload(
                self._audit.record,
                self._memory_event(
                    EventKind.MEMORY_WRITTEN,
                    chain,
                    memory_id,
                    "DENY",
                    _content_hash(content),
                    len(sources),
                    reason="token_check_failed",
                ),
            )
            raise MemoryDenied("remember refused: token_check_failed", reason="token_check_failed")
        decision = await self._decider.decide_all(
            chain,
            Action.remember(),
            sources,
            scope_resources=sources or [memory_id],
            resource=memory_id,
            args={"content_sha256": _content_hash(content)},
        )
        if decision.outcome is not Outcome.ALLOW:
            raise MemoryDenied(f"remember refused: {decision.reason}", reason=decision.reason)

        now = self._clock.now()
        provenance: list[Tuple] = [(d, "derived_from", memory_id) for d in sources]
        provenance.append((chain.actor, "writer", memory_id))
        try:
            await self._tuples.write_tuples(provenance)
        except HocError as exc:
            raise MemoryDenied(f"remember refused: {exc.reason}", reason=exc.reason) from exc

        backend_ref: str | None = None
        try:
            backend_ref = await self._adapter.store(memory_id, content, self._namespace)
            record = MemoryRecord(
                memory_id=memory_id,
                written_for=chain.subject,
                written_by=chain.actor,
                derived_from=sources,
                content_hash=_content_hash(content),
                backend=self._adapter.name,
                backend_ref=backend_ref,
                created_at=now,
            )
            await offload(self._ledger.put, record)
        except Exception:
            await self._rollback(memory_id, provenance, backend_ref)
            raise

        await offload(
            self._audit.record,
            self._memory_event(
                EventKind.MEMORY_WRITTEN,
                chain,
                memory_id,
                "written",
                record.content_hash,
                len(sources),
            ),
        )
        return Memory(
            memory_id=memory_id,
            content=content,
            derived_from=sources,
            written_for=chain.subject,
            written_by=chain.actor,
            created_at=now,
        )

    # -- read --------------------------------------------------------------------------------

    async def recall(
        self,
        chain: PrincipalChain,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        permits: ResourcePermits | None = None,
    ) -> list[Memory]:
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            candidates = await self._adapter.search(query, self._namespace, limit)
        except Exception:
            # I5, journaled: the backend could not answer, so nothing is returned (ADR 0018).
            log.warning("memory backend search failed; returning nothing")
            await self._deny_read(chain, "memory:*", "backend_unavailable")
            return []
        kept: list[Memory] = []
        for candidate in candidates:
            memory = await self._admit(chain, candidate, permits)
            if memory is not None:
                kept.append(memory)
            if len(kept) >= limit:
                break
        return kept

    async def _admit(
        self, chain: PrincipalChain, candidate: Candidate, permits: ResourcePermits | None
    ) -> Memory | None:
        try:
            return await self._admit_unguarded(chain, candidate, permits)
        except HocError as exc:
            # I5: ledger or engine trouble while checking provenance means "not now", audited.
            await self._deny_read(
                chain,
                candidate.backend_refs[0] if candidate.backend_refs else "memory:unknown",
                exc.reason,
            )
            return None

    async def _deny_read(self, chain: PrincipalChain, memory_id: str, reason: str) -> None:
        await offload(
            self._audit.record,
            self._memory_event(
                EventKind.MEMORY_READ,
                chain,
                memory_id,
                outcome="DENY",
                content_hash=None,
                source_count=0,
                reason=reason,
            ),
        )

    async def _admit_unguarded(
        self, chain: PrincipalChain, candidate: Candidate, permits: ResourcePermits | None
    ) -> Memory | None:
        records = [
            await offload(self._ledger.get_by_backend_ref, self._adapter.name, ref)
            for ref in candidate.backend_refs
        ]
        if not records or any(r is None for r in records):
            # No provenance we can vouch for: the content never leaves the service, journaled.
            log.info("dropping memory candidate with unknown backend reference")
            await self._deny_read(chain, "memory:unknown", "no_provenance")
            return None
        rows = [r for r in records if r is not None]
        primary = rows[0]
        sources = await self._sources(rows)
        refused_read = await offload(
            _refused, permits, Kind.READ, [r.memory_id for r in rows] + list(sources)
        )
        if refused_read:
            # Reading a memory is reading its sources: the token must permit every one (ADR 0018).
            await self._deny_read(chain, primary.memory_id, "token_check_failed")
            return None

        if not sources:
            # No source at all: readable by the subject it was written for, nobody else.
            written_for = {r.written_for for r in rows}
            if written_for != {chain.subject}:
                await offload(
                    self._audit.record,
                    self._memory_event(
                        EventKind.MEMORY_READ,
                        chain,
                        primary.memory_id,
                        "DENY",
                        None,
                        0,
                        reason="not_written_for",
                    ),
                )
                return None
        decision = await self._decider.decide_all(
            chain,
            Action.read_memory(),
            sources,
            scope_resources=[r.memory_id for r in rows],
            resource=primary.memory_id,
        )
        await offload(
            self._audit.record,
            self._memory_event(
                EventKind.MEMORY_READ,
                chain,
                primary.memory_id,
                str(decision.outcome),
                primary.content_hash,
                len(sources),
                reason=decision.reason,
            ),
        )
        if decision.outcome is not Outcome.ALLOW:
            return None
        return Memory(
            memory_id=primary.memory_id,
            content=candidate.content,
            derived_from=sources,
            written_for=primary.written_for,
            written_by=primary.written_by,
            created_at=primary.created_at,
        )

    async def _sources(self, rows: Sequence[MemoryRecord]) -> tuple[str, ...]:
        """Union of ledger and OpenFGA provenance: tampering with one cannot remove a source."""
        sources: set[str] = set()
        for row in rows:
            sources.update(row.derived_from)
            for user, _, _ in await self._tuples.read_tuples(row.memory_id, "derived_from"):
                sources.add(user)
        return tuple(sorted(sources))

    # -- delete ------------------------------------------------------------------------------

    async def forget(
        self, chain: PrincipalChain, memory_id: str, *, permits: ResourcePermits | None = None
    ) -> None:
        validate_ref(memory_id, "memory")
        if await offload(_refused, permits, Kind.REMEMBER, [memory_id]):
            await self._deny_read(chain, memory_id, "token_check_failed")
            raise MemoryDenied("forget refused: token_check_failed", reason="token_check_failed")
        record = await offload(self._ledger.get, memory_id)
        if record is None or record.written_for != chain.subject:
            await offload(
                self._audit.record,
                self._memory_event(
                    EventKind.MEMORY_READ,
                    chain,
                    memory_id,
                    "DENY",
                    None,
                    0,
                    reason="not_written_for",
                ),
            )
            raise MemoryDenied("forget refused: not the subject this memory was written for")
        decision = await self._decider.decide_all(
            chain, Action.remember(), [], scope_resources=[memory_id], resource=memory_id
        )
        if decision.outcome is not Outcome.ALLOW:
            raise MemoryDenied(f"forget refused: {decision.reason}", reason=decision.reason)
        await self._adapter.delete(record.backend_ref, self._namespace)
        await offload(self._ledger.delete, memory_id)
        tuples = await self._tuples.read_tuples(memory_id, "derived_from")
        tuples += await self._tuples.read_tuples(memory_id, "writer")
        await self._tuples.delete_tuples(tuples)

    # -- internals ---------------------------------------------------------------------------

    async def _rollback(
        self, memory_id: str, provenance: list[Tuple], backend_ref: str | None
    ) -> None:
        if backend_ref is not None:
            # Content without a ledger row is unreadable but still stored: remove it (ADR 0018).
            try:
                await self._adapter.delete(backend_ref, self._namespace)
            except Exception:
                log.error("could not roll back backend content for %s", memory_id)
        try:
            await self._tuples.delete_tuples(provenance)
        except HocError:
            log.error("could not roll back provenance tuples for %s", memory_id)
        try:
            await offload(self._ledger.delete, memory_id)
        except HocError:
            log.error("could not roll back ledger row for %s", memory_id)

    def _memory_event(  # noqa: PLR0917
        self,
        kind: EventKind,
        chain: PrincipalChain,
        memory_id: str,
        outcome: str,
        content_hash: str | None,
        source_count: int,
        *,
        reason: str | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            kind=kind,
            timestamp=self._clock.now(),
            subject=chain.subject,
            actor=chain.actor,
            delegation_depth=chain.depth,
            action=f"memory:{kind.value.split('_')[1]}",
            resource=memory_id,
            outcome=outcome,
            reason=reason or f"sources={source_count}",
            args_hash=content_hash,
        )


def _refused(
    permits: ResourcePermits | None, operation: Kind, resources: Sequence[str]
) -> tuple[str, ...]:
    """Resources the token's checks refuse; empty without a callable (the trail is the ceiling)."""
    if permits is None or not resources:
        return ()
    answers = permits(operation, list(resources))
    return tuple(r for r in resources if not answers.get(r, False))


def _dedupe(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


__all__ = ["MemoryService"]
