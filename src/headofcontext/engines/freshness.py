"""Connector freshness guard (threat T8): stale tuples must not produce ALLOW decisions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from headofcontext.core.clock import Clock, SystemClock
from headofcontext.core.errors import ConnectorStale


class FreshnessGuard(Protocol):
    def assert_fresh(self) -> None:
        """Raise ConnectorStale if any registered connector is older than its allowed staleness."""
        ...


class AlwaysFresh:
    """For deployments without connectors (tuples written directly). Explicit, never a default."""

    def assert_fresh(self) -> None:
        return None


class InMemoryConnectorState:
    def __init__(self, max_staleness: timedelta, clock: Clock | None = None) -> None:
        self._max = max_staleness
        self._clock = clock or SystemClock()
        self._last_sync: dict[str, datetime] = {}

    def register(self, connector: str) -> None:
        """A registered connector that never synced is stale by definition."""
        self._last_sync.setdefault(connector, datetime.min.replace(tzinfo=self._clock.now().tzinfo))

    def mark_synced(self, connector: str, at: datetime | None = None) -> None:
        self._last_sync[connector] = at or self._clock.now()

    def assert_fresh(self) -> None:
        now = self._clock.now()
        for connector, last in self._last_sync.items():
            age = now - last
            if age > self._max:
                raise ConnectorStale(connector, age.total_seconds())
