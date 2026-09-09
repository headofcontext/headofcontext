"""Provenance ledgers: in-memory for tests, PostgreSQL for deployments."""

from __future__ import annotations

import json
from datetime import datetime

from headofcontext.core.errors import Unavailable
from headofcontext.db.schema import LEDGER_SCHEMA
from headofcontext.db.store import PostgresStore
from headofcontext.memory.model import MemoryRecord


class LedgerUnavailable(Unavailable):
    reason = "ledger_unavailable"


class InMemoryLedger:
    def __init__(self) -> None:
        self._rows: dict[str, MemoryRecord] = {}

    def put(self, record: MemoryRecord) -> None:
        self._rows[record.memory_id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._rows.get(memory_id)

    def get_by_backend_ref(self, backend: str, backend_ref: str) -> MemoryRecord | None:
        for row in self._rows.values():
            if row.backend == backend and row.backend_ref == backend_ref:
                return row
        return None

    def delete(self, memory_id: str) -> None:
        self._rows.pop(memory_id, None)

    def count(self) -> int:
        return len(self._rows)


_COLUMNS = (
    "memory_id, written_for, written_by, derived_from, content_hash, "
    "backend, backend_ref, created_at"
)


class PostgresLedger(PostgresStore):
    unavailable = LedgerUnavailable
    schema = LEDGER_SCHEMA

    def put(self, record: MemoryRecord) -> None:
        self._run(
            f"INSERT INTO memory_provenance ({_COLUMNS}) "  # noqa: S608
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (memory_id) DO UPDATE SET written_for = EXCLUDED.written_for, "
            "written_by = EXCLUDED.written_by, derived_from = EXCLUDED.derived_from, "
            "content_hash = EXCLUDED.content_hash, backend = EXCLUDED.backend, "
            "backend_ref = EXCLUDED.backend_ref, created_at = EXCLUDED.created_at",
            (
                record.memory_id,
                record.written_for,
                record.written_by,
                json.dumps(list(record.derived_from)),
                record.content_hash,
                record.backend,
                record.backend_ref,
                record.created_at,
            ),
            failure="provenance write failed",
        )

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._record("memory_id = %s", (memory_id,))

    def get_by_backend_ref(self, backend: str, backend_ref: str) -> MemoryRecord | None:
        return self._record("backend = %s AND backend_ref = %s", (backend, backend_ref))

    def delete(self, memory_id: str) -> None:
        self._run(
            "DELETE FROM memory_provenance WHERE memory_id = %s",
            (memory_id,),
            failure="provenance delete failed",
        )

    def _record(self, where: str, params: tuple[str, ...]) -> MemoryRecord | None:
        row = self._one(
            f"SELECT {_COLUMNS} FROM memory_provenance WHERE {where}",  # noqa: S608
            params,
            failure="provenance read failed",
        )
        if row is None:
            return None
        created = row[7]
        if not isinstance(created, datetime):
            raise LedgerUnavailable("provenance row has an invalid timestamp")
        return MemoryRecord(
            memory_id=str(row[0]),
            written_for=str(row[1]),
            written_by=str(row[2]),
            derived_from=tuple(str(d) for d in json.loads(str(row[3]))),
            content_hash=str(row[4]),
            backend=str(row[5]),
            backend_ref=str(row[6]),
            created_at=created,
        )
