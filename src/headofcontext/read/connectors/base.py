"""Connector framework: snapshots of source ACLs, reconciliation into OpenFGA, sync state.

Connectors never decide; they translate a source's permissions into tuples. Reconciliation is a
diff against the previous snapshot kept in the state store, which doubles as the freshness guard
(threat T8): a connector that has not synced within ``max_staleness`` makes every decision DENY.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from headofcontext.core import Clock, SystemClock, Tuple, TupleStore
from headofcontext.core.blocking import offload
from headofcontext.core.errors import ConnectorStale, InvariantViolation, Unavailable
from headofcontext.core.events import AuditEvent, AuditSink, EventKind
from headofcontext.core.refs import validate_ref, validate_resource_pattern
from headofcontext.db.schema import CONNECTOR_STATE_SCHEMA
from headofcontext.db.store import PostgresStore


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    id: str
    title: str
    path: str
    source: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The desired tuples for one connector, plus the documents it saw."""

    connector: str
    tuples: frozenset[Tuple]
    documents: tuple[DocumentMeta, ...]
    taken_at: datetime


@dataclass(frozen=True, slots=True)
class SyncReport:
    connector: str
    added: int
    removed: int
    documents: int
    synced_at: datetime


class SourceConnector(Protocol):
    @property
    def name(self) -> str: ...

    async def snapshot(self) -> Snapshot: ...


class ConnectorStateStore(Protocol):
    """Previous snapshot and sync time per connector; also the FreshnessGuard."""

    def register(self, connector: str) -> None: ...

    def load_tuples(self, connector: str) -> frozenset[Tuple] | None: ...

    def save(self, connector: str, tuples: frozenset[Tuple], synced_at: datetime) -> None: ...

    def last_sync(self, connector: str) -> datetime | None: ...

    def assert_fresh(self) -> None: ...


def principal(ref: str) -> str:
    """OpenFGA user for an ACL entry: users as-is, groups through their ``member`` userset."""
    if ref.startswith("group:"):
        validate_ref(ref, "group")
        return f"{ref}#member"
    validate_ref(ref, "user")
    return ref


def validate_tuple(t: Tuple) -> Tuple:
    user, relation, obj = t
    base, _, userset = user.partition("#")
    validate_resource_pattern(base)
    if userset and not userset.isidentifier():
        raise InvariantViolation(f"invalid userset {user!r}")
    if not relation.isidentifier():
        raise InvariantViolation(f"invalid relation {relation!r}")
    validate_resource_pattern(obj)
    return t


async def reconcile(
    connector: SourceConnector,
    *,
    tuples: TupleStore,
    state: ConnectorStateStore,
    audit: AuditSink,
    clock: Clock | None = None,
) -> SyncReport:
    """Bring OpenFGA in line with the connector's snapshot. Raises on any failure; state is
    only advanced after every write succeeded, so a half-applied sync reads as stale."""
    clock = clock or SystemClock()
    state.register(connector.name)
    snapshot = await connector.snapshot()
    desired = frozenset(validate_tuple(t) for t in snapshot.tuples)
    previous = state.load_tuples(connector.name) or frozenset()
    to_add = sorted(desired - previous)
    to_remove = sorted(previous - desired)
    for chunk in _chunks(to_add, 100):
        await tuples.write_tuples(chunk)
    for chunk in _chunks(to_remove, 100):
        await tuples.delete_tuples(chunk)
    synced_at = clock.now()
    await offload(state.save, connector.name, desired, synced_at)
    await offload(
        audit.record,
        AuditEvent(
            kind=EventKind.CONNECTOR_SYNCED,
            timestamp=synced_at,
            subject="-",
            actor=f"connector:{connector.name}",
            delegation_depth=0,
            action="connector:sync",
            resource=f"source:{connector.name}",
            outcome="ALLOW",
            reason=(
                f"added={len(to_add)} removed={len(to_remove)} documents={len(snapshot.documents)}"
            ),
        ),
    )
    return SyncReport(
        connector=connector.name,
        added=len(to_add),
        removed=len(to_remove),
        documents=len(snapshot.documents),
        synced_at=synced_at,
    )


def _chunks(items: list[Tuple], size: int) -> Iterable[list[Tuple]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class InMemoryConnectorStateStore:
    def __init__(self, max_staleness: timedelta, clock: Clock | None = None) -> None:
        self._max = max_staleness
        self._clock = clock or SystemClock()
        self._tuples: dict[str, frozenset[Tuple]] = {}
        self._synced: dict[str, datetime | None] = {}

    def register(self, connector: str) -> None:
        self._synced.setdefault(connector, None)

    def load_tuples(self, connector: str) -> frozenset[Tuple] | None:
        return self._tuples.get(connector)

    def save(self, connector: str, tuples: frozenset[Tuple], synced_at: datetime) -> None:
        self._tuples[connector] = tuples
        self._synced[connector] = synced_at

    def last_sync(self, connector: str) -> datetime | None:
        return self._synced.get(connector)

    def assert_fresh(self) -> None:
        now = self._clock.now()
        for connector, last in self._synced.items():
            if last is None:
                raise ConnectorStale(connector, float("inf"))
            age = now - last
            if age > self._max:
                raise ConnectorStale(connector, age.total_seconds())


class ConnectorStateUnavailable(Unavailable):
    reason = "connector_state_unavailable"


# Snapshots are scoped by ``namespace`` (the OpenFGA store id in practice): the same connector
# syncing into two stores must not share a diff baseline.


class PostgresConnectorStateStore(PostgresStore):
    """Snapshot + sync time per connector, scoped by namespace (the OpenFGA store id).

    Registration without a sync is stale (T8). Two stores fed by the same connector never share
    a diff baseline: the snapshot is keyed by ``(namespace, connector)``.
    """

    unavailable = ConnectorStateUnavailable
    schema = CONNECTOR_STATE_SCHEMA

    def __init__(
        self,
        conninfo: str,
        max_staleness: timedelta,
        clock: Clock | None = None,
        *,
        namespace: str = "",
    ) -> None:
        super().__init__(conninfo)
        self._max = max_staleness
        self._clock = clock or SystemClock()
        self._namespace = namespace

    def register(self, connector: str) -> None:
        self._run(
            "INSERT INTO connector_state_v2 (namespace, connector, synced_at, tuples) "
            "VALUES (%s, %s, NULL, '[]') ON CONFLICT (namespace, connector) DO NOTHING",
            (self._namespace, connector),
            failure="connector state write failed",
        )

    def load_tuples(self, connector: str) -> frozenset[Tuple] | None:
        row = self._one(
            "SELECT tuples, synced_at FROM connector_state_v2 "
            "WHERE namespace = %s AND connector = %s",
            (self._namespace, connector),
            failure="connector state read failed",
        )
        if row is None or row[1] is None:
            return None
        return frozenset((str(u), str(r), str(o)) for u, r, o in json.loads(str(row[0])))

    def save(self, connector: str, tuples: frozenset[Tuple], synced_at: datetime) -> None:
        self._run(
            "INSERT INTO connector_state_v2 (namespace, connector, synced_at, tuples) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (namespace, connector) DO UPDATE SET "
            "synced_at = EXCLUDED.synced_at, tuples = EXCLUDED.tuples",
            (self._namespace, connector, synced_at, json.dumps(sorted(tuples))),
            failure="connector state write failed",
        )

    def last_sync(self, connector: str) -> datetime | None:
        row = self._one(
            "SELECT synced_at FROM connector_state_v2 WHERE namespace = %s AND connector = %s",
            (self._namespace, connector),
            failure="connector state read failed",
        )
        return row[0] if row is not None and isinstance(row[0], datetime) else None

    def assert_fresh(self) -> None:
        now = self._clock.now()
        try:
            rows = self._query(
                "SELECT connector, synced_at FROM connector_state_v2 WHERE namespace = %s",
                (self._namespace,),
            )
        except ConnectorStateUnavailable as exc:
            # I5: an unreadable state store is a stale state store.
            raise ConnectorStale("connector_state", float("inf")) from exc
        for connector, synced_at in rows:
            if not isinstance(synced_at, datetime):
                raise ConnectorStale(str(connector), float("inf"))
            age = now - synced_at
            if age > self._max:
                raise ConnectorStale(str(connector), age.total_seconds())
