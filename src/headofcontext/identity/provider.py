"""IdentityProvider Protocol and its value objects (ADR 0004)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from headofcontext.core.events import fingerprint


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    subject: str
    issuer: str
    groups: tuple[str, ...]
    expires_at: datetime
    claims: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ExchangedToken:
    """A token obtained by RFC 8693 exchange. The token string itself is never printed."""

    access_token: str = field(repr=False)
    token_type: str
    expires_in: int | None
    issued_token_type: str | None
    scope: str | None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.access_token)


class IdentityProvider(Protocol):
    async def verify_user_token(self, token: str) -> VerifiedIdentity:
        """Validate a user's JWT. Raises IdentityError on any failure (I5)."""
        ...

    async def exchange(
        self,
        *,
        subject_token: str,
        actor_token: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
    ) -> ExchangedToken:
        """RFC 8693 token exchange so downstream calls run under the user's identity."""
        ...

    async def aclose(self) -> None:
        """Release connections; called once at service shutdown."""
        ...
