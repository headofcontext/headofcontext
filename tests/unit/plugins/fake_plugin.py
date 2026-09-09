"""A fake premium plugin: one factory per entry point group (ADR 0019).

This is the contract a private plugin is written against. The test loads these factories through
real ``importlib.metadata.EntryPoint`` objects, exactly as an installed distribution would be.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from headofcontext.actions import ApprovalRequest
from headofcontext.identity import ExchangedToken, VerifiedIdentity
from headofcontext.memory import Candidate
from headofcontext.read.connectors import Snapshot


class FakeChannel:
    name = "fake"

    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = dict(env)
        self.notified: list[str] = []

    async def notify(self, request: ApprovalRequest) -> None:
        self.notified.append(request.request_id)


class FakeConnector:
    def __init__(self, args: dict[str, Any]) -> None:
        self.args = args
        self.name = str(args.get("name", "fake"))

    async def snapshot(self) -> Snapshot:
        return Snapshot(
            connector=self.name,
            tuples=frozenset({("user:alice", "viewer", "document:fake-1")}),
            documents=(),
            taken_at=datetime.now(UTC),
        )


class FakeIdentity:
    def __init__(self, settings: Any) -> None:
        self.issuer = str(settings.oidc_issuer)
        self.closed = False

    async def verify_user_token(self, token: str) -> VerifiedIdentity:
        return VerifiedIdentity(
            subject=f"user:{token}",
            issuer=self.issuer,
            groups=(),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            claims={"sub": token, "idtyp": "app" if token.startswith("app-") else "user"},
        )

    async def exchange(
        self,
        *,
        subject_token: str,
        actor_token: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
    ) -> ExchangedToken:
        return ExchangedToken(
            access_token="x", token_type="Bearer", expires_in=60, issued_token_type=None, scope=None
        )

    async def aclose(self) -> None:
        self.closed = True


class FakeMemory:
    name = "fake-memory"

    def __init__(self, env: Mapping[str, str]) -> None:
        self.namespace_seen = env.get("HOC_MEMORY_NAMESPACE")
        self._rows: dict[str, str] = {}

    async def store(self, memory_id: str, content: str, namespace: str) -> str:
        self._rows[memory_id] = content
        return memory_id

    async def search(self, query: str, namespace: str, limit: int) -> list[Candidate]:
        return [
            Candidate(content=c, backend_refs=(ref,), score=1.0)
            for ref, c in self._rows.items()
            if query in c
        ][:limit]

    async def delete(self, backend_ref: str, namespace: str) -> None:
        self._rows.pop(backend_ref, None)


def make_channel(env: Mapping[str, str]) -> FakeChannel:
    return FakeChannel(env)


def make_connector(args: dict[str, Any]) -> FakeConnector:
    return FakeConnector(args)


def make_identity(settings: Any) -> FakeIdentity:
    return FakeIdentity(settings)


def make_memory(env: Mapping[str, str]) -> FakeMemory:
    return FakeMemory(env)
