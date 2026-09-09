"""In-memory sink with the same hash chain as the Postgres sink; for tests and dev."""

from __future__ import annotations

import threading

from headofcontext.core.events import GENESIS_HASH, AuditEvent, StoredAuditEvent, compute_hash


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._events: list[StoredAuditEvent] = []
        # Services call sinks from threads (ADR 0022): the chain's predecessor must be read and
        # appended under one lock or concurrent writers fork it.
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            prev = self._events[-1].hash if self._events else GENESIS_HASH
            self._events.append(StoredAuditEvent.chain(event, prev))

    @property
    def events(self) -> list[AuditEvent]:
        return [stored.event for stored in self._events]

    @property
    def stored(self) -> list[StoredAuditEvent]:
        return list(self._events)

    def verify_chain(self) -> bool:
        prev = GENESIS_HASH
        for stored in self._events:
            if stored.prev_hash != prev or stored.hash != compute_hash(prev, stored.event):
                return False
            prev = stored.hash
        return True
