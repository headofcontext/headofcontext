"""Revocation store (I4). Any revoked block id anywhere in a token invalidates the whole token."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol


class RevocationStore(Protocol):
    def revoke(self, revocation_id: str, *, reason: str, at: datetime) -> None: ...

    def is_revoked(self, revocation_ids: Iterable[str]) -> bool:
        """True if at least one of the ids is revoked. Must raise on backend failure (I5)."""
        ...


class InMemoryRevocationStore:
    def __init__(self) -> None:
        self._revoked: dict[str, tuple[str, datetime]] = {}

    def revoke(self, revocation_id: str, *, reason: str, at: datetime) -> None:
        self._revoked[revocation_id] = (reason, at)

    def is_revoked(self, revocation_ids: Iterable[str]) -> bool:
        return any(rid in self._revoked for rid in revocation_ids)

    def __len__(self) -> int:
        return len(self._revoked)
