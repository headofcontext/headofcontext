"""The base every PostgreSQL store shares (ADR 0022): pooled connections, typed failures.

Subclasses declare ``unavailable`` (their ``HocError`` subclass, so I5 keeps typed reasons) and
``schema`` (their DDL, applied by the migration ledger; ``ensure_schema`` stays for library use
and tests). Statements go through ``_run`` / ``_query`` / ``_one``; multi-statement work through
``_transaction``. A psycopg error becomes the store's error; any other exception raised inside a
transaction body passes through untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar

import psycopg

from headofcontext.core.errors import HocError
from headofcontext.db.pool import get_pool

Row = tuple[object, ...]
Params = tuple[object, ...] | list[object]


class PostgresStore:
    unavailable: ClassVar[type[HocError]] = HocError
    schema: ClassVar[str] = ""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    def ensure_schema(self) -> None:
        if self.schema:
            self._run(self.schema, (), failure="could not create schema")

    def close(self) -> None:
        """Kept for compatibility: connections belong to the process pool (``db.pool``)."""

    def _run(self, sql: str, params: Params = (), *, failure: str = "write failed") -> int:
        try:
            with get_pool(self._conninfo).connection() as conn:
                return int(conn.execute(sql, params).rowcount)
        except psycopg.Error as exc:
            raise self.unavailable(failure) from exc

    def _query(self, sql: str, params: Params = (), *, failure: str = "read failed") -> list[Row]:
        try:
            with get_pool(self._conninfo).connection() as conn:
                return conn.execute(sql, params).fetchall()
        except psycopg.Error as exc:
            raise self.unavailable(failure) from exc

    def _one(self, sql: str, params: Params = (), *, failure: str = "read failed") -> Row | None:
        try:
            with get_pool(self._conninfo).connection() as conn:
                return conn.execute(sql, params).fetchone()
        except psycopg.Error as exc:
            raise self.unavailable(failure) from exc

    @contextmanager
    def _transaction(self, *, failure: str = "write failed") -> Iterator[psycopg.Connection[Row]]:
        try:
            with get_pool(self._conninfo).connection() as conn, conn.transaction():
                yield conn
        except psycopg.Error as exc:
            raise self.unavailable(failure) from exc


__all__ = ["Params", "PostgresStore", "Row"]
