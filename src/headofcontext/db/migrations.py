"""Versioned schema migrations (ADR 0020).

Version 1 is the union of the store schemas as they were when the ledger was introduced, every
statement idempotent, so an existing database adopts the ledger without change. A later change
is a new ``Migration``; an applied one is never edited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from headofcontext.core.errors import Unavailable
from headofcontext.db.schema import (
    APPROVALS_SCHEMA,
    AUDIT_SCHEMA,
    AUDIT_TRUNCATE_GUARD_SCHEMA,
    CONNECTOR_STATE_SCHEMA,
    LEDGER_SCHEMA,
    MANDATES_SCHEMA,
    MEMORY_CONTENT_SCHEMA,
    REVOCATIONS_SCHEMA,
)

log = logging.getLogger(__name__)

LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
"""
_LOCK = "SELECT pg_advisory_lock(hashtext('schema_migrations'))"
_UNLOCK = "SELECT pg_advisory_unlock(hashtext('schema_migrations'))"


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


class SchemaOutOfDate(Unavailable):
    """The database is behind the code: the service refuses to start (fail closed)."""

    reason = "schema_out_of_date"


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "baseline",
        "\n".join(
            [
                AUDIT_SCHEMA,
                REVOCATIONS_SCHEMA,
                APPROVALS_SCHEMA,
                MANDATES_SCHEMA,
                LEDGER_SCHEMA,
                CONNECTOR_STATE_SCHEMA,
            ]
        ),
    ),
    Migration(2, "memory_content", MEMORY_CONTENT_SCHEMA),
    Migration(3, "audit_truncate_guard", AUDIT_TRUNCATE_GUARD_SCHEMA),
)
LATEST = MIGRATIONS[-1].version


def pending(current: int) -> tuple[Migration, ...]:
    return tuple(m for m in MIGRATIONS if m.version > current)


def current_version(conninfo: str) -> int:
    """Highest applied version, 0 for a database that never saw the ledger."""
    with psycopg.connect(conninfo, autocommit=True) as conn:
        return _current(conn)


def migrate(conninfo: str) -> list[int]:
    """Apply every pending migration, one transaction each, serialized across processes."""
    applied: list[int] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        # A session-level advisory lock serializes every migrating process, the ledger creation
        # included: two replicas booting on an empty database cannot race on CREATE TABLE.
        conn.execute(_LOCK)
        try:
            conn.execute(LEDGER)
            if _current(conn) > LATEST:
                raise SchemaOutOfDate(
                    f"database schema is at version {_current(conn)}, newer than this code "
                    f"({LATEST}): refusing to migrate"
                )
            for migration in pending(_current(conn)):
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.name, datetime.now(UTC)),
                    )
                log.info("schema migration %d (%s) applied", migration.version, migration.name)
                applied.append(migration.version)
        finally:
            conn.execute(_UNLOCK)
    return applied


def assert_current(conninfo: str) -> None:
    """The database must be exactly at the version the code knows: behind means migrate,
    ahead means this code was rolled back under a newer schema, and must not run either."""
    version = current_version(conninfo)
    if version < LATEST:
        raise SchemaOutOfDate(
            f"database schema is at version {version}, code expects {LATEST}: run `hoc db migrate`"
        )
    if version > LATEST:
        raise SchemaOutOfDate(
            f"database schema is at version {version}, newer than this code ({LATEST}): "
            "deploy the matching version"
        )


def ping(conninfo: str) -> int:
    """``SELECT 1`` plus the schema version, for the readiness probe. Raises on any failure."""
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=3) as conn:
        conn.execute("SELECT 1")
        return _current(conn)


def _current(conn: psycopg.Connection[tuple[object, ...]]) -> int:
    exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()
    if exists is None or exists[0] is None:
        return 0
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(str(row[0])) if row is not None and row[0] is not None else 0
