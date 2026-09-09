"""Audit events and the AuditSink Protocol (ADR 0005).

Events never carry tokens, document bodies or tool arguments: only identifiers and hashes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from headofcontext.core.decision import Decision

GENESIS_HASH = "0" * 64


class EventKind(StrEnum):
    DECISION = "decision"
    TOKEN_ISSUED = "token_issued"  # noqa: S105
    TOKEN_DELEGATED = "token_delegated"  # noqa: S105
    TOKEN_REVOKED = "token_revoked"  # noqa: S105
    MEMORY_WRITTEN = "memory_written"
    MEMORY_READ = "memory_read"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    APPROVAL_CONSUMED = "approval_consumed"
    APPROVAL_NOTIFY_FAILED = "approval_notify_failed"
    MANDATE_CREATED = "mandate_created"
    MANDATE_REVOKED = "mandate_revoked"
    MANDATE_EXPIRED = "mandate_expired"
    READ_FILTERED = "read_filtered"
    CONNECTOR_SYNCED = "connector_synced"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    kind: EventKind
    timestamp: datetime
    subject: str
    actor: str
    delegation_depth: int
    action: str
    resource: str
    outcome: str
    reason: str
    engine_latency_ms: float = 0.0
    token_fingerprint: str | None = None
    args_hash: str | None = None
    decision_id: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def from_decision(cls, decision: Decision, *, args_hash: str | None = None) -> AuditEvent:
        return cls(
            kind=EventKind.DECISION,
            timestamp=decision.timestamp,
            subject=decision.chain.subject,
            actor=decision.chain.actor,
            delegation_depth=decision.chain.depth,
            action=f"{decision.action.kind}:{decision.action.relation}",
            resource=decision.resource,
            outcome=str(decision.outcome),
            reason=decision.reason,
            engine_latency_ms=decision.engine_latency_ms,
            args_hash=args_hash,
            decision_id=decision.decision_id,
        )

    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), default=_json_default
        )


@dataclass(frozen=True, slots=True)
class StoredAuditEvent:
    """An event as persisted: chained to its predecessor by hash."""

    event: AuditEvent
    prev_hash: str
    hash: str

    @classmethod
    def chain(cls, event: AuditEvent, prev_hash: str) -> StoredAuditEvent:
        return cls(event=event, prev_hash=prev_hash, hash=compute_hash(prev_hash, event))


def compute_hash(prev_hash: str, event: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + event.canonical_json()).encode()).hexdigest()


def hash_args(args: Mapping[str, Any] | None) -> str | None:
    """Fingerprint of tool arguments. The arguments themselves are never stored."""
    if args is None:
        return None
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(canonical.encode()).hexdigest()


def fingerprint(secret: str | bytes) -> str:
    """sha256 hex of a token or secret. The only form in which they may appear in logs."""
    data = secret.encode() if isinstance(secret, str) else secret
    return hashlib.sha256(data).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Persist the event. Raises AuditUnavailable on any failure."""
        ...
