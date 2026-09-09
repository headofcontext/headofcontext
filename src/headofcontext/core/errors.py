"""Typed errors. Every error carries a short machine-readable ``reason`` used in audit events."""

from __future__ import annotations


class HocError(Exception):
    """Base class for all HeadOfContext errors."""

    reason: str = "error"

    def __init__(self, message: str = "", *, reason: str | None = None) -> None:
        super().__init__(message or self.reason)
        if reason is not None:
            self.reason = reason


class Unavailable(HocError):
    """A dependency did not answer (store, engine, connector). Always a denial (I5); 503."""

    reason = "unavailable"


class ConfigurationError(HocError):
    """A startup or wiring mistake: never an HTTP answer, the process stops instead."""

    reason = "configuration_error"


class InvariantViolation(HocError):
    """A PrincipalChain, Scope or Decision would break one of the invariants I1-I5."""

    reason = "invariant_violation"


class ScopeEscalation(InvariantViolation):
    """I1: a delegation tried to widen the scope."""

    reason = "scope_escalation"


class InvalidResource(InvariantViolation):
    """A resource reference is not of the form ``type:id`` with an optional trailing ``*``."""

    reason = "invalid_resource"


class EngineUnavailable(Unavailable):
    """I5: the authorization engine could not answer. Always maps to DENY."""

    reason = "engine_unavailable"


class ConnectorStale(HocError):
    """I5: a connector's last successful sync is older than allowed. Always maps to DENY."""

    reason = "connector_stale"

    def __init__(self, connector: str, staleness_seconds: float) -> None:
        super().__init__(f"connector {connector!r} stale for {staleness_seconds:.0f}s")
        self.connector = connector
        self.staleness_seconds = staleness_seconds


class TokenInvalid(HocError):
    """A biscuit failed signature, structure, expiry or check verification."""

    reason = "token_invalid"


class TokenRevoked(TokenInvalid):
    """A biscuit contains a revoked block (I4)."""

    reason = "token_revoked"


class IdentityError(HocError):
    """An IdP token could not be verified or exchanged."""

    reason = "identity_error"


class AuditUnavailable(Unavailable):
    """The audit journal could not record an event. No decision may be returned."""

    reason = "audit_unavailable"


class MemoryDenied(HocError):
    """A memory write or forget was refused; ``reason`` carries the decision reason."""

    reason = "memory_denied"


class MandateDenied(HocError):
    """A mandate could not be created, used or revoked; ``reason`` says why."""

    reason = "mandate_denied"


class ActionDenied(HocError):
    """A tool call was refused by the gate; ``reason`` carries the decision reason."""

    reason = "action_denied"


class ApprovalError(HocError):
    """An approval request could not be created, resolved or redeemed as asked."""

    reason = "approval_error"
