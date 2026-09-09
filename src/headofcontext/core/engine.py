"""AuthzEngine Protocol. The only implementation is the OpenFGA adapter; tests use fakes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class AuthzEngine(Protocol):
    """Every method raises EngineUnavailable or ConnectorStale instead of guessing (I5)."""

    async def check(self, subject: str, relation: str, obj: str) -> bool: ...

    async def batch_check(self, subject: str, items: Sequence[tuple[str, str]]) -> list[bool]: ...

    async def list_objects(self, subject: str, relation: str, type_: str) -> list[str]: ...


Tuple = tuple[str, str, str]
"""(user, relation, object) in OpenFGA order."""


class TupleStore(Protocol):
    """Relationship writes owned by HeadOfContext itself (memory provenance, connector sync)."""

    async def write_tuples(self, tuples: Sequence[Tuple]) -> None: ...

    async def delete_tuples(self, tuples: Sequence[Tuple]) -> None: ...

    async def read_tuples(self, obj: str, relation: str) -> list[Tuple]: ...
