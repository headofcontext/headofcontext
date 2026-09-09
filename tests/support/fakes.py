"""Fakes shared by every suite (ADR 0027): a relationship graph standing in for OpenFGA and a
deterministic clock. Integration tests use the real containers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from headofcontext.core import Tuple
from headofcontext.core.errors import EngineUnavailable


class FakeGraph:
    def __init__(self) -> None:
        self.tuples: set[Tuple] = set()
        self.down = False

    def grant(self, user: str, relation: str, obj: str) -> None:
        self.tuples.add((user, relation, obj))

    def revoke(self, user: str, relation: str, obj: str) -> None:
        self.tuples.discard((user, relation, obj))

    def _fail_if_down(self) -> None:
        if self.down:
            raise EngineUnavailable("openfga down")

    async def check(self, subject: str, relation: str, obj: str) -> bool:
        self._fail_if_down()
        return (subject, relation, obj) in self.tuples

    async def batch_check(self, subject: str, items: Sequence[tuple[str, str]]) -> list[bool]:
        self._fail_if_down()
        return [(subject, r, o) in self.tuples for r, o in items]

    async def list_objects(self, subject: str, relation: str, type_: str) -> list[str]:
        self._fail_if_down()
        return sorted(
            o
            for u, r, o in self.tuples
            if u == subject and r == relation and o.startswith(type_ + ":")
        )

    async def write_tuples(self, tuples: Sequence[Tuple]) -> None:
        self._fail_if_down()
        self.tuples.update(tuples)

    async def delete_tuples(self, tuples: Sequence[Tuple]) -> None:
        self._fail_if_down()
        self.tuples.difference_update(tuples)

    async def read_tuples(self, obj: str, relation: str) -> list[Tuple]:
        self._fail_if_down()
        return sorted(t for t in self.tuples if t[2] == obj and t[1] == relation)


class FrozenClock:
    """Deterministic clock for tests; advance with ``tick``."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def tick(self, **delta: float) -> None:
        self._now += timedelta(**delta)
