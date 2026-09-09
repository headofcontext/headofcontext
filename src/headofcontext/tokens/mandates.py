"""Standing mandates (ADR 0016): a human authorizes an agent once, the agent works alone after.

A mandate is state in the token layer, like an approval is state in the action layer. It never
widens anything: its scope is what the human chose, every token issued under it is a subset, and
decisions are still made for the human's rights in OpenFGA. Revoking it revokes one id that
every derived token carries.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from headofcontext.core import (
    AuthzEngine,
    Capability,
    Clock,
    Kind,
    PrincipalChain,
    Scope,
    SystemClock,
)
from headofcontext.core.blocking import offload
from headofcontext.core.errors import HocError, InvariantViolation, MandateDenied, Unavailable
from headofcontext.core.events import AuditEvent, AuditSink, EventKind
from headofcontext.core.refs import validate_ref
from headofcontext.db.schema import MANDATES_SCHEMA
from headofcontext.db.store import PostgresStore
from headofcontext.tokens.biscuit.service import IssuedToken, TokenService, mandate_revocation_id

log = logging.getLogger(__name__)

DEFAULT_MAX_DAYS = 90
DEFAULT_MAX_TOKEN_TTL = timedelta(hours=1)
BINDING_RELATION = "can_act_on_behalf_of"


class MandateStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Mandate:
    mandate_id: str
    subject: str
    agent: str
    scope: Scope
    created_at: datetime
    expires_at: datetime
    max_token_ttl: timedelta
    created_by: str
    status: MandateStatus = MandateStatus.ACTIVE
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    @property
    def revocation_id(self) -> str:
        return mandate_revocation_id(self.mandate_id)

    def with_status(self, status: MandateStatus, **changes: object) -> Mandate:
        return replace(self, status=status, **changes)  # type: ignore[arg-type]


class MandateStoreUnavailable(Unavailable):
    reason = "mandate_store_unavailable"


class MandateStore(Protocol):
    def create(self, mandate: Mandate) -> None: ...

    def get(self, mandate_id: str) -> Mandate | None: ...

    def update(self, mandate: Mandate) -> None: ...

    def list_for(self, subject: str) -> list[Mandate]: ...

    def expire_active(self, now: datetime) -> list[Mandate]:
        """Mark every ACTIVE mandate past its expiry EXPIRED; return them."""
        ...


class InMemoryMandateStore:
    def __init__(self) -> None:
        self._rows: dict[str, Mandate] = {}
        self._lock = threading.Lock()  # stores run in threads under the services (ADR 0022)

    def create(self, mandate: Mandate) -> None:
        with self._lock:
            if mandate.mandate_id in self._rows:
                raise MandateStoreUnavailable("duplicate mandate id")
            self._rows[mandate.mandate_id] = mandate

    def get(self, mandate_id: str) -> Mandate | None:
        return self._rows.get(mandate_id)

    def update(self, mandate: Mandate) -> None:
        with self._lock:
            if mandate.mandate_id not in self._rows:
                raise MandateStoreUnavailable("unknown mandate")
            self._rows[mandate.mandate_id] = mandate

    def list_for(self, subject: str) -> list[Mandate]:
        return sorted(
            (m for m in self._rows.values() if m.subject == subject), key=lambda m: m.created_at
        )

    def expire_active(self, now: datetime) -> list[Mandate]:
        with self._lock:
            expired = [
                m.with_status(MandateStatus.EXPIRED)
                for m in self._rows.values()
                if m.status is MandateStatus.ACTIVE and m.expires_at <= now
            ]
            for m in expired:
                self._rows[m.mandate_id] = m
            return expired


_COLUMNS = (
    "mandate_id, subject, agent, scope_json, created_at, expires_at, max_token_ttl_seconds, "
    "created_by, status, revoked_at, revocation_reason"
)


class PostgresMandateStore(PostgresStore):
    unavailable = MandateStoreUnavailable
    schema = MANDATES_SCHEMA

    def create(self, mandate: Mandate) -> None:
        self._run(
            f"INSERT INTO mandates ({_COLUMNS}) "  # noqa: S608
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            _values(mandate),
            failure="mandate store write failed",
        )

    def get(self, mandate_id: str) -> Mandate | None:
        rows = self._mandates("WHERE mandate_id = %s", (mandate_id,))
        return rows[0] if rows else None

    def update(self, mandate: Mandate) -> None:
        self._run(
            "UPDATE mandates SET status = %s, revoked_at = %s, revocation_reason = %s "
            "WHERE mandate_id = %s",
            (
                str(mandate.status),
                mandate.revoked_at,
                mandate.revocation_reason,
                mandate.mandate_id,
            ),
            failure="mandate store write failed",
        )

    def list_for(self, subject: str) -> list[Mandate]:
        return self._mandates("WHERE subject = %s", (subject,))

    def expire_active(self, now: datetime) -> list[Mandate]:
        rows = self._query(
            f"UPDATE mandates SET status = %s WHERE status = %s AND expires_at <= %s "  # noqa: S608
            f"RETURNING {_COLUMNS}",
            (str(MandateStatus.EXPIRED), str(MandateStatus.ACTIVE), now),
            failure="mandate sweep failed",
        )
        return [_from_row(row) for row in rows]

    def _mandates(self, where: str, params: tuple[object, ...]) -> list[Mandate]:
        rows = self._query(
            f"SELECT {_COLUMNS} FROM mandates {where} ORDER BY created_at",  # noqa: S608
            params,
            failure="mandate store read failed",
        )
        return [_from_row(row) for row in rows]


def _values(m: Mandate) -> tuple[object, ...]:
    return (
        m.mandate_id,
        m.subject,
        m.agent,
        scope_to_json(m.scope),
        m.created_at,
        m.expires_at,
        int(m.max_token_ttl.total_seconds()),
        m.created_by,
        str(m.status),
        m.revoked_at,
        m.revocation_reason,
    )


def _from_row(row: tuple[object, ...]) -> Mandate:
    def ts(value: object) -> datetime | None:
        return value if isinstance(value, datetime) else None

    created_at, expires_at = ts(row[4]), ts(row[5])
    if created_at is None or expires_at is None:
        raise MandateStoreUnavailable("mandate row has invalid timestamps")
    return Mandate(
        mandate_id=str(row[0]),
        subject=str(row[1]),
        agent=str(row[2]),
        scope=scope_from_json(str(row[3])),
        created_at=created_at,
        expires_at=expires_at,
        max_token_ttl=timedelta(seconds=int(str(row[6]))),
        created_by=str(row[7]),
        status=MandateStatus(str(row[8])),
        revoked_at=ts(row[9]),
        revocation_reason=str(row[10]) if row[10] is not None else None,
    )


def scope_to_json(scope: Scope) -> str:
    return json.dumps(
        [{"kind": str(c.kind), "resource": c.resource} for c in sorted(scope)],
        separators=(",", ":"),
    )


def scope_from_json(raw: str) -> Scope:
    data = json.loads(raw)
    return Scope.from_iterable(Capability(Kind(c["kind"]), str(c["resource"])) for c in data)


class MandateService:
    def __init__(
        self,
        store: MandateStore,
        tokens: TokenService,
        engine: AuthzEngine | None,
        audit: AuditSink,
        clock: Clock | None = None,
        *,
        max_days: int = DEFAULT_MAX_DAYS,
        default_max_token_ttl: timedelta = DEFAULT_MAX_TOKEN_TTL,
    ) -> None:
        self.store = store
        self._tokens = tokens
        self._engine = engine
        self._audit = audit
        self._clock = clock or SystemClock()
        self._max_days = max_days
        self._default_max_token_ttl = default_max_token_ttl

    # -- human side -------------------------------------------------------------------------

    async def create(
        self,
        *,
        subject: str,
        agent: str,
        scope: Scope,
        expires_at: datetime,
        created_by: str,
        max_token_ttl: timedelta | None = None,
    ) -> Mandate:
        """The human (``created_by``) mandates ``agent`` to act for them until ``expires_at``."""
        try:
            validate_ref(subject, "user")
            validate_ref(agent, "agent")
        except InvariantViolation as exc:
            raise MandateDenied(str(exc), reason="invalid_reference") from exc
        if created_by != subject:
            raise MandateDenied("only the subject can mandate an agent", reason="not_subject")
        now = self._clock.now()
        if expires_at <= now:
            raise MandateDenied("mandate already expired", reason="mandate_expired")
        if expires_at > now + timedelta(days=self._max_days):
            raise MandateDenied(
                f"mandate exceeds the {self._max_days}-day maximum", reason="mandate_too_long"
            )
        if not scope:
            raise MandateDenied("mandate scope is empty", reason="scope_empty")
        ttl = max_token_ttl or self._default_max_token_ttl
        if ttl <= timedelta(0):
            raise MandateDenied("max_token_ttl must be positive", reason="invalid_ttl")
        await self._require_binding(subject, agent)
        mandate = Mandate(
            mandate_id=uuid.uuid4().hex,
            subject=subject,
            agent=agent,
            scope=scope,
            created_at=now,
            expires_at=expires_at,
            max_token_ttl=ttl,
            created_by=created_by,
        )
        await offload(self.store.create, mandate)
        await offload(
            self._audit.record,
            self._event(EventKind.MANDATE_CREATED, mandate, "created", "created"),
        )
        return mandate

    async def get(self, mandate_id: str) -> Mandate | None:
        return await offload(self._get_fresh, mandate_id)

    async def list_for(self, subject: str) -> list[Mandate]:
        return await offload(self._list_fresh, subject)

    async def revoke(self, mandate_id: str, *, by: str, reason: str) -> Mandate:
        return await offload(self._revoke, mandate_id, by, reason)

    async def expire(self) -> int:
        return await offload(self._expire)

    # -- blocking halves, run in a thread (ADR 0027) -------------------------------------------

    def _get_fresh(self, mandate_id: str) -> Mandate | None:
        return self._fresh(self.store.get(mandate_id))

    def _list_fresh(self, subject: str) -> list[Mandate]:
        return [m for m in (self._fresh(m) for m in self.store.list_for(subject)) if m]

    def _revoke(self, mandate_id: str, by: str, reason: str) -> Mandate:
        mandate = self.store.get(mandate_id)
        if mandate is None:
            raise MandateDenied("unknown mandate", reason="mandate_unknown")
        if by != mandate.subject:
            raise MandateDenied("only the subject can revoke a mandate", reason="not_subject")
        now = self._clock.now()
        # Revoke the id first: even if the store write fails, no token under it works anymore.
        self._tokens.revoke_id(mandate.revocation_id, reason=reason or "mandate revoked")
        revoked = mandate.with_status(
            MandateStatus.REVOKED, revoked_at=now, revocation_reason=reason or None
        )
        self.store.update(revoked)
        self._audit.record(self._event(EventKind.MANDATE_REVOKED, revoked, "revoked", reason))
        return revoked

    def _expire(self) -> int:
        expired = self.store.expire_active(self._clock.now())
        for mandate in expired:
            self._audit.record(
                self._event(EventKind.MANDATE_EXPIRED, mandate, "expired", "expired")
            )
        return len(expired)

    # -- agent side ---------------------------------------------------------------------------

    async def issue(
        self, mandate_id: str, *, caller: str, scope: Scope, ttl: timedelta | None = None
    ) -> IssuedToken:
        """A short token for ``caller`` under an active mandate; never wider, never longer."""
        mandate = await self.get(mandate_id)
        if mandate is None:
            raise MandateDenied("unknown mandate", reason="mandate_unknown")
        if mandate.status is MandateStatus.REVOKED:
            raise MandateDenied("mandate revoked", reason="mandate_revoked")
        if mandate.status is MandateStatus.EXPIRED:
            raise MandateDenied("mandate expired", reason="mandate_expired")
        if caller != mandate.agent:
            raise MandateDenied("mandate belongs to another agent", reason="mandate_wrong_agent")
        if not scope.is_subset_of(mandate.scope):
            raise MandateDenied("requested scope exceeds the mandate", reason="scope_escalation")
        await self._require_binding(mandate.subject, mandate.agent)
        now = self._clock.now()
        wanted = ttl or mandate.max_token_ttl
        effective = min(wanted, mandate.max_token_ttl, mandate.expires_at - now)
        chain = PrincipalChain.root(mandate.subject, mandate.agent, scope)
        return await offload(
            self._tokens.issue, chain, ttl=effective, mandate_id=mandate.mandate_id
        )

    # -- internals ----------------------------------------------------------------------------

    def _fresh(self, mandate: Mandate | None) -> Mandate | None:
        """Lazy state: an ACTIVE mandate past its date reads as EXPIRED, one whose revocation id
        is revoked reads as REVOKED, before the sweeper runs and before any token is issued."""
        if mandate is None or mandate.status is not MandateStatus.ACTIVE:
            return mandate
        now = self._clock.now()
        if mandate.expires_at <= now:
            expired = mandate.with_status(MandateStatus.EXPIRED)
            self.store.update(expired)
            self._audit.record(
                self._event(EventKind.MANDATE_EXPIRED, expired, "expired", "expired")
            )
            return expired
        try:
            id_revoked = self._tokens.is_revoked((mandate.revocation_id,))
        except HocError as exc:
            raise MandateDenied(
                "revocation store unavailable", reason="revocation_store_unavailable"
            ) from exc
        if id_revoked:
            reason = "revocation_id_revoked"
            revoked = mandate.with_status(
                MandateStatus.REVOKED, revoked_at=now, revocation_reason=reason
            )
            self.store.update(revoked)
            self._audit.record(self._event(EventKind.MANDATE_REVOKED, revoked, "revoked", reason))
            return revoked
        return mandate

    async def _require_binding(self, subject: str, agent: str) -> None:
        # Fail closed: an unreachable engine is a refusal, and the binding is re-checked on every
        # issue so that a removed agent stops even under a still-active mandate.
        if self._engine is None:
            raise MandateDenied("no engine to check the binding", reason="engine_unavailable")
        try:
            bound = await self._engine.check(subject, BINDING_RELATION, agent)
        except HocError:
            raise
        except Exception as exc:
            raise MandateDenied("engine unavailable", reason="engine_unavailable") from exc
        if not bound:
            raise MandateDenied("agent is not bound to this user", reason="not_related")

    def _event(self, kind: EventKind, mandate: Mandate, outcome: str, reason: str) -> AuditEvent:
        return AuditEvent(
            kind=kind,
            timestamp=self._clock.now(),
            subject=mandate.subject,
            actor=mandate.agent,
            delegation_depth=0,
            action="mandate",
            resource=f"mandate:{mandate.mandate_id}",
            outcome=outcome,
            reason=reason or outcome,
        )
