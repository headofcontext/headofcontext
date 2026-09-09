"""ACT: execution-time authorization of tool calls (ADR 0008)."""

from headofcontext.actions.approvals import (
    APPROVER_RELATION,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    ApprovalStoreUnavailable,
    InMemoryApprovalStore,
    PostgresApprovalStore,
    authorize_approver,
    resolve_request,
)
from headofcontext.actions.channels import (
    ApprovalChannel,
    ApprovalChannelError,
    LogChannel,
    WebhookChannel,
    build_channels,
    request_payload,
)
from headofcontext.actions.gate import DEFAULT_APPROVAL_TTL, ActionGate, GateResult
from headofcontext.actions.policies import CompositePolicy, DenyWhen, RequireApproval
from headofcontext.core.errors import ActionDenied, ApprovalError

__all__ = [
    "APPROVER_RELATION",
    "DEFAULT_APPROVAL_TTL",
    "ActionDenied",
    "ActionGate",
    "ApprovalChannel",
    "ApprovalChannelError",
    "ApprovalError",
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalStore",
    "ApprovalStoreUnavailable",
    "CompositePolicy",
    "DenyWhen",
    "GateResult",
    "InMemoryApprovalStore",
    "LogChannel",
    "PostgresApprovalStore",
    "RequireApproval",
    "WebhookChannel",
    "authorize_approver",
    "build_channels",
    "request_payload",
    "resolve_request",
]
