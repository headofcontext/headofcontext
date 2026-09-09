"""PostgreSQL-backed revocation store. A failing backend raises, and the token layer denies."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from headofcontext.core.errors import Unavailable
from headofcontext.db.schema import REVOCATIONS_SCHEMA
from headofcontext.db.store import PostgresStore


class RevocationStoreUnavailable(Unavailable):
    reason = "revocation_store_unavailable"


class PostgresRevocationStore(PostgresStore):
    unavailable = RevocationStoreUnavailable
    schema = REVOCATIONS_SCHEMA

    def revoke(self, revocation_id: str, *, reason: str, at: datetime) -> None:
        self._run(
            "INSERT INTO token_revocations (revocation_id, reason, revoked_at) "
            "VALUES (%s, %s, %s) ON CONFLICT (revocation_id) DO NOTHING",
            (revocation_id, reason, at),
            failure="revocation write failed",
        )

    def is_revoked(self, revocation_ids: Iterable[str]) -> bool:
        ids = list(revocation_ids)
        if not ids:
            return False
        row = self._one(
            "SELECT 1 FROM token_revocations WHERE revocation_id = ANY(%s) LIMIT 1",
            (ids,),
            failure="revocation lookup failed",
        )
        return row is not None
