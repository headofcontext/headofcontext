"""Compatibility re-export: the audit event model lives in ``headofcontext.core.events``."""

from headofcontext.core.events import (
    GENESIS_HASH,
    AuditEvent,
    AuditSink,
    EventKind,
    StoredAuditEvent,
    compute_hash,
    fingerprint,
    hash_args,
)

__all__ = [
    "GENESIS_HASH",
    "AuditEvent",
    "AuditSink",
    "EventKind",
    "StoredAuditEvent",
    "compute_hash",
    "fingerprint",
    "hash_args",
]
