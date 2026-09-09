"""Approval requests and their stores (ADR 0008). State only; notification is a plugin."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from headofcontext.core.blocking import offload
from headofcontext.core.engine import AuthzEngine
from headofcontext.core.errors import ApprovalError, HocError, InvariantViolation, Unavailable
from headofcontext.core.events import AuditEvent, AuditSink, EventKind
from headofcontext.core.refs import validate_ref
from headofcontext.db.schema import APPROVALS_SCHEMA
from headofcontext.db.store import PostgresStore

APPROVER_RELATION = "approver"
"""OpenFGA relation on `tool` naming who may resolve a request for it (ADR 0017)."""


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    subject: str
    actor: str
    delegation_depth: int
    tool: str
    args_hash: str
    decision_id: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    consumed_at: datetime | None = None
    approval_reason: str = field(default="approval_required")

    def with_status(self, status: ApprovalStatus, **changes: object) -> ApprovalRequest:
        return replace(self, status=status, **changes)  # type: ignore[arg-type]


class ApprovalStore(Protocol):
    def create(self, request: ApprovalRequest) -> None: ...

    def get(self, request_id: str) -> ApprovalRequest | None: ...

    def update(self, request: ApprovalRequest) -> None: ...

    def transition(
        self,
        request_id: str,
        from_statuses: tuple[ApprovalStatus, ...],
        to_status: ApprovalStatus,
        **fields: object,
    ) -> ApprovalRequest | None:
        """Move the request to ``to_status`` only if it is in one of ``from_statuses``, atomically.

        None when it was not (unknown, or another transition won): resolutions, expiries and
        consumptions all go through here so two callers can never both succeed.
        """
        ...

    def consume(self, request_id: str, now: datetime) -> ApprovalRequest | None:
        """APPROVED → CONSUMED as one atomic compare-and-set (ADR 0017).

        Returns the consumed request, or None when it was not APPROVED at that instant (unknown,
        pending, already consumed, or lost the race): the caller must treat None as a denial.
        """
        ...

    def list_pending(self, *, subject: str | None = None) -> list[ApprovalRequest]: ...

    def expire_pending(self, now: datetime) -> int:
        """Mark overdue PENDING requests EXPIRED; return how many."""
        ...


class ApprovalStoreUnavailable(Unavailable):
    reason = "approval_store_unavailable"


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._rows: dict[str, ApprovalRequest] = {}
        # Stores run in threads under the services (ADR 0022): every read-modify-write is locked.
        self._lock = threading.Lock()

    def create(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id in self._rows:
                raise ApprovalStoreUnavailable("duplicate request id")
            self._rows[request.request_id] = request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._rows.get(request_id)

    def update(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id not in self._rows:
                raise ApprovalStoreUnavailable("unknown request id")
            self._rows[request.request_id] = request

    def transition(
        self,
        request_id: str,
        from_statuses: tuple[ApprovalStatus, ...],
        to_status: ApprovalStatus,
        **fields: object,
    ) -> ApprovalRequest | None:
        with self._lock:
            current = self._rows.get(request_id)
            if current is None or current.status not in from_statuses:
                return None
            moved = current.with_status(to_status, **fields)
            self._rows[request_id] = moved
            return moved

    def consume(self, request_id: str, now: datetime) -> ApprovalRequest | None:
        return self.transition(
            request_id, (ApprovalStatus.APPROVED,), ApprovalStatus.CONSUMED, consumed_at=now
        )

    def list_pending(self, *, subject: str | None = None) -> list[ApprovalRequest]:
        return [
            r
            for r in self._rows.values()
            if r.status is ApprovalStatus.PENDING and (subject is None or r.subject == subject)
        ]

    def expire_pending(self, now: datetime) -> int:
        with self._lock:
            overdue = [
                r
                for r in self._rows.values()
                if r.status is ApprovalStatus.PENDING and r.expires_at < now
            ]
            for r in overdue:
                self._rows[r.request_id] = r.with_status(ApprovalStatus.EXPIRED)
            return len(overdue)


_COLUMNS = (
    "request_id, subject, actor, delegation_depth, tool, args_hash, decision_id, created_at, "
    "expires_at, status, resolved_by, resolved_at, resolution_reason, consumed_at, approval_reason"
)


class PostgresApprovalStore(PostgresStore):
    unavailable = ApprovalStoreUnavailable
    schema = APPROVALS_SCHEMA

    def create(self, request: ApprovalRequest) -> None:
        self._run(
            f"INSERT INTO approval_requests ({_COLUMNS}) "  # noqa: S608
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            _values(request),
            failure="approval store write failed",
        )

    def get(self, request_id: str) -> ApprovalRequest | None:
        rows = self._requests("WHERE request_id = %s", (request_id,))
        return rows[0] if rows else None

    def update(self, request: ApprovalRequest) -> None:
        self._run(
            "UPDATE approval_requests SET status = %s, resolved_by = %s, resolved_at = %s, "
            "resolution_reason = %s, consumed_at = %s WHERE request_id = %s",
            (
                str(request.status),
                request.resolved_by,
                request.resolved_at,
                request.resolution_reason,
                request.consumed_at,
                request.request_id,
            ),
            failure="approval store write failed",
        )

    def transition(
        self,
        request_id: str,
        from_statuses: tuple[ApprovalStatus, ...],
        to_status: ApprovalStatus,
        **fields: object,
    ) -> ApprovalRequest | None:
        # The WHERE on status is the compare-and-set: concurrent callers see one row updated.
        allowed = {"resolved_by", "resolved_at", "resolution_reason", "consumed_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ApprovalStoreUnavailable(f"unknown approval fields {sorted(unknown)}")
        assignments = ", ".join(f"{name} = %s" for name in fields)
        sql = (
            "UPDATE approval_requests SET status = %s"  # noqa: S608
            + (f", {assignments}" if assignments else "")
            + f" WHERE request_id = %s AND status = ANY(%s) RETURNING {_COLUMNS}"
        )
        params: tuple[object, ...] = (
            str(to_status),
            *fields.values(),
            request_id,
            [str(s) for s in from_statuses],
        )
        row = self._one(sql, params, failure="approval transition failed")
        return _from_row(row) if row is not None else None

    def consume(self, request_id: str, now: datetime) -> ApprovalRequest | None:
        return self.transition(
            request_id, (ApprovalStatus.APPROVED,), ApprovalStatus.CONSUMED, consumed_at=now
        )

    def list_pending(self, *, subject: str | None = None) -> list[ApprovalRequest]:
        if subject is None:
            return self._requests("WHERE status = %s", (str(ApprovalStatus.PENDING),))
        return self._requests(
            "WHERE status = %s AND subject = %s", (str(ApprovalStatus.PENDING), subject)
        )

    def expire_pending(self, now: datetime) -> int:
        return self._run(
            "UPDATE approval_requests SET status = %s WHERE status = %s AND expires_at < %s",
            (str(ApprovalStatus.EXPIRED), str(ApprovalStatus.PENDING), now),
            failure="approval sweep failed",
        )

    def _requests(self, where: str, params: tuple[object, ...]) -> list[ApprovalRequest]:
        rows = self._query(
            f"SELECT {_COLUMNS} FROM approval_requests {where} ORDER BY created_at",  # noqa: S608
            params,
            failure="approval store read failed",
        )
        return [_from_row(row) for row in rows]


def _values(r: ApprovalRequest) -> tuple[object, ...]:
    return (
        r.request_id,
        r.subject,
        r.actor,
        r.delegation_depth,
        r.tool,
        r.args_hash,
        r.decision_id,
        r.created_at,
        r.expires_at,
        str(r.status),
        r.resolved_by,
        r.resolved_at,
        r.resolution_reason,
        r.consumed_at,
        r.approval_reason,
    )


def _from_row(row: tuple[object, ...]) -> ApprovalRequest:
    def ts(value: object) -> datetime | None:
        return value if isinstance(value, datetime) else None

    created, expires = ts(row[7]), ts(row[8])
    if created is None or expires is None:
        raise ApprovalStoreUnavailable("approval row has invalid timestamps")
    return ApprovalRequest(
        request_id=str(row[0]),
        subject=str(row[1]),
        actor=str(row[2]),
        delegation_depth=int(str(row[3])),
        tool=str(row[4]),
        args_hash=str(row[5]),
        decision_id=str(row[6]),
        created_at=created,
        expires_at=expires,
        status=ApprovalStatus(str(row[9])),
        resolved_by=str(row[10]) if row[10] is not None else None,
        resolved_at=ts(row[11]),
        resolution_reason=str(row[12]) if row[12] is not None else None,
        consumed_at=ts(row[13]),
        approval_reason=str(row[14]),
    )


async def authorize_approver(
    engine: AuthzEngine | None,
    audit: AuditSink,
    now: datetime,
    request: ApprovalRequest,
    approver: str,
    *,
    allow_self_approval: bool = False,
) -> None:
    """Refuse unless ``approver`` may resolve this request (ADR 0017). Refusals are journaled.

    The subject may resolve their own request only when self-approval is enabled: they already
    hold ``can_invoke`` on the tool, or the decision would have been DENY. Anyone else must hold
    ``approver`` on the tool in OpenFGA; an unreachable or missing engine refuses (I5).
    """
    try:
        validate_ref(approver, "user")
    except InvariantViolation as exc:
        raise ApprovalError("approver must be a user") from exc
    reason: str | None
    if approver == request.subject:
        if allow_self_approval:
            return
        reason = "self_approval_disabled"
    elif engine is None:
        reason = "engine_unavailable"
    else:
        try:
            allowed = await engine.check(approver, APPROVER_RELATION, request.tool)
        except HocError as exc:
            reason = exc.reason
        except Exception:  # I5: any engine failure refuses
            reason = "engine_unavailable"
        else:
            reason = None if allowed else "approver_not_authorized"
    if reason is None:
        return
    await offload(
        audit.record,
        AuditEvent(
            kind=EventKind.APPROVAL_RESOLVED,
            timestamp=now,
            subject=request.subject,
            actor=approver,
            delegation_depth=request.delegation_depth,
            action=f"approval:{request.request_id}",
            resource=request.tool,
            outcome="REFUSED",
            reason=reason,
            args_hash=request.args_hash,
            decision_id=request.decision_id,
        ),
    )
    raise ApprovalError(f"approver refused: {reason}", reason=reason)


def resolve_request(
    store: ApprovalStore,
    audit: AuditSink,
    now: datetime,
    request_id: str,
    *,
    approver: str,
    approved: bool,
    reason: str = "",
    allow_self_approval: bool = False,
) -> ApprovalRequest:
    """A human decides. Shared by the gate and the CLI so both audit the same way."""
    try:
        validate_ref(approver, "user")
    except InvariantViolation as exc:
        raise ApprovalError("approver must be a user") from exc
    request = store.get(request_id)
    if request is None:
        raise ApprovalError("unknown approval request")
    if request.status is ApprovalStatus.PENDING and now > request.expires_at:
        request = request.with_status(ApprovalStatus.EXPIRED)
        store.update(request)
    if request.status is not ApprovalStatus.PENDING:
        raise ApprovalError(f"request is {request.status}, not pending")
    if approver == request.subject and not allow_self_approval:
        raise ApprovalError("the subject cannot approve their own request")
    status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    # Compare-and-set from PENDING: two resolvers racing never both succeed, and a rejection is
    # never overwritten by a later approval.
    resolved = store.transition(
        request_id,
        (ApprovalStatus.PENDING,),
        status,
        resolved_by=approver,
        resolved_at=now,
        resolution_reason=reason,
    )
    if resolved is None:
        raise ApprovalError("request is no longer pending", reason="approval_not_pending")
    audit.record(
        AuditEvent(
            kind=EventKind.APPROVAL_RESOLVED,
            timestamp=now,
            subject=request.subject,
            actor=approver,
            delegation_depth=request.delegation_depth,
            action=f"approval:{request.request_id}",
            resource=request.tool,
            outcome=str(status).upper(),
            reason=reason or str(status),
            args_hash=request.args_hash,
            decision_id=request.decision_id,
        )
    )
    return resolved


__all__ = [
    "APPROVER_RELATION",
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalStore",
    "ApprovalStoreUnavailable",
    "InMemoryApprovalStore",
    "PostgresApprovalStore",
    "authorize_approver",
    "resolve_request",
]
