"""ActionGate: authorize a tool call, park it for approval, redeem an approval (ADR 0008)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from headofcontext.actions.approvals import (
    APPROVER_RELATION,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    authorize_approver,
    resolve_request,
)
from headofcontext.actions.channels import ApprovalChannel, ApprovalChannelError
from headofcontext.core import (
    Action,
    Clock,
    Decider,
    Decision,
    DecisionContext,
    Outcome,
    PrincipalChain,
    SystemClock,
)
from headofcontext.core.blocking import offload
from headofcontext.core.errors import ApprovalError, EngineUnavailable, HocError
from headofcontext.core.events import AuditEvent, AuditSink, EventKind, hash_args
from headofcontext.core.metrics import MetricsSink
from headofcontext.core.refs import validate_ref, validate_resource_pattern

DEFAULT_APPROVAL_TTL = timedelta(hours=1)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: Decision
    approval: ApprovalRequest | None = None

    @property
    def allowed(self) -> bool:
        return self.decision.outcome is Outcome.ALLOW

    @property
    def pending(self) -> bool:
        return self.decision.outcome is Outcome.REQUIRE_APPROVAL and self.approval is not None


class ActionGate:
    def __init__(
        self,
        decider: Decider,
        approvals: ApprovalStore,
        audit: AuditSink,
        clock: Clock | None = None,
        *,
        approval_ttl: timedelta = DEFAULT_APPROVAL_TTL,
        allow_self_approval: bool = False,
        channels: Sequence[ApprovalChannel] = (),
        metrics: MetricsSink | None = None,
    ) -> None:
        self._decider = decider
        self.approvals = approvals
        self._audit = audit
        self._clock = clock or SystemClock()
        self._ttl = approval_ttl
        self._allow_self_approval = allow_self_approval
        self._channels = tuple(channels)
        self._metrics = metrics

    def now(self) -> datetime:
        return self._clock.now()

    @property
    def decider(self) -> Decider:
        """The decision path of the gate; the tool catalog asks it so the two never disagree."""
        return self._decider

    def audit_raw(self, event: AuditEvent) -> None:
        """Record a denial that has no chain to attach (an invalid token, for instance)."""
        self._audit.record(event)

    async def gate(
        self, chain: PrincipalChain, tool: str, args: Mapping[str, Any] | None = None
    ) -> GateResult:
        """Authorize ``tool`` for the chain. Never executes anything."""
        validate_ref(tool, "tool")
        args = dict(args or {})
        decision = await self._decider.decide(chain, Action.invoke(), tool, args=args)
        if decision.outcome is not Outcome.REQUIRE_APPROVAL:
            return GateResult(decision)
        now = self._clock.now()
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            subject=chain.subject,
            actor=chain.actor,
            delegation_depth=chain.depth,
            tool=tool,
            args_hash=hash_args(args) or "",
            decision_id=decision.decision_id,
            created_at=now,
            expires_at=now + self._ttl,
            approval_reason=decision.reason,
        )
        await offload(self.approvals.create, request)
        await offload(
            self._audit.record,
            self._event(EventKind.APPROVAL_REQUESTED, request, "PENDING", decision.reason),
        )
        await self._notify(request)
        return GateResult(decision, request)

    async def _notify(self, request: ApprovalRequest) -> None:
        # The request is already stored: a channel failure is recorded, never turned into a
        # different outcome (ADR 0014).
        for channel in self._channels:
            try:
                await channel.notify(request)
            except ApprovalChannelError as exc:
                log.warning(
                    "approval channel %s failed for request %s: %s",
                    channel.name,
                    request.request_id,
                    exc,
                )
                await offload(
                    self._audit.record,
                    self._event(EventKind.APPROVAL_NOTIFY_FAILED, request, "PENDING", exc.reason),
                )
                if self._metrics is not None:
                    self._metrics.notify_failed(channel.name)

    async def resolve(
        self, request_id: str, *, approver: str, approved: bool, reason: str = ""
    ) -> ApprovalRequest:
        """A human decides: the tool's `approver` in OpenFGA, or the subject with self-approval."""
        now = self._clock.now()
        request = await offload(self.approvals.get, request_id)
        if request is None:
            raise ApprovalError("unknown approval request")
        await authorize_approver(
            self._decider.engine,
            self._audit,
            now,
            request,
            approver,
            allow_self_approval=self._allow_self_approval,
        )
        return await offload(
            resolve_request,
            self.approvals,
            self._audit,
            now,
            request_id,
            approver=approver,
            approved=approved,
            reason=reason,
            allow_self_approval=self._allow_self_approval,
        )

    async def pending_for(self, user: str) -> list[ApprovalRequest]:
        """Pending requests ``user`` may approve (one BatchCheck) plus their own as subject."""
        validate_ref(user, "user")
        pending = await offload(self.approvals.list_pending)
        if not pending:
            return []
        try:
            related = await self._decider.engine.batch_check(
                user, [(APPROVER_RELATION, r.tool) for r in pending]
            )
        except HocError:
            raise
        except Exception as exc:  # I5: never fall back to an unfiltered list
            raise EngineUnavailable("approver check failed") from exc
        return [r for r, ok in zip(pending, related, strict=True) if ok or r.subject == user]

    async def redeem(
        self,
        chain: PrincipalChain,
        request_id: str,
        args: Mapping[str, Any] | None = None,
        *,
        tool: str | None = None,
    ) -> Decision:
        """Turn an approved request into a fresh ALLOW decision, once, for the same call."""
        args = dict(args or {})
        request = await offload(self.approvals.get, request_id)
        if request is None:
            return await self._deny(chain, tool or "tool:unknown", args, "approval_unknown")
        target = tool or request.tool
        validate_resource_pattern(target)
        request = await self._expire_if_needed(request)
        mismatch = self._mismatch(request, chain, target, args)
        if mismatch is not None:
            return await self._deny(chain, target, args, mismatch)
        # Claim the request before the decision: concurrent redeems race on this compare-and-set
        # and exactly one wins (ADR 0017). A DENY afterwards leaves it consumed, fail closed.
        consumed = await offload(self.approvals.consume, request.request_id, self._clock.now())
        if consumed is None:
            return await self._deny(chain, target, args, "approval_consumed")
        decision = await self._decider.decide(
            chain,
            Action.invoke(),
            target,
            args=args,
            context=DecisionContext(approval_id=request.request_id),
        )
        outcome = "redeemed" if decision.outcome is Outcome.ALLOW else decision.reason
        await offload(
            self._audit.record,
            self._event(EventKind.APPROVAL_CONSUMED, consumed, "CONSUMED", outcome),
        )
        return decision

    # -- internals ---------------------------------------------------------------------------

    def _mismatch(  # noqa: PLR0911 — one early return per typed reason reads better than nesting
        self, request: ApprovalRequest, chain: PrincipalChain, tool: str, args: Mapping[str, Any]
    ) -> str | None:
        if request.status is ApprovalStatus.CONSUMED:
            return "approval_consumed"
        if request.status is ApprovalStatus.EXPIRED:
            return "approval_expired"
        if request.status is not ApprovalStatus.APPROVED:
            return "approval_not_approved"
        if request.tool != tool:
            return "approval_tool_mismatch"
        if (request.subject, request.actor, request.delegation_depth) != (
            chain.subject,
            chain.actor,
            chain.depth,
        ):
            return "approval_chain_mismatch"
        if request.args_hash != (hash_args(args) or ""):
            return "approval_args_mismatch"
        return None

    async def _expire_if_needed(self, request: ApprovalRequest) -> ApprovalRequest:
        if (
            request.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
            and self._clock.now() > request.expires_at
        ):
            expired = await offload(
                self.approvals.transition,
                request.request_id,
                (ApprovalStatus.PENDING, ApprovalStatus.APPROVED),
                ApprovalStatus.EXPIRED,
            )
            return expired or await offload(self.approvals.get, request.request_id) or request
        return request

    async def _deny(
        self, chain: PrincipalChain, tool: str, args: Mapping[str, Any], reason: str
    ) -> Decision:
        """A denial that still goes through the audit path, with the typed reason."""
        decision = Decision(
            outcome=Outcome.DENY,
            reason=reason,
            chain=chain,
            resource=tool,
            action=Action.invoke(),
            timestamp=self._clock.now(),
            engine_latency_ms=0.0,
        )
        await offload(
            self._audit.record, AuditEvent.from_decision(decision, args_hash=hash_args(args))
        )
        return decision

    def _event(
        self,
        kind: EventKind,
        request: ApprovalRequest,
        outcome: str,
        reason: str,
        *,
        actor_override: str | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            kind=kind,
            timestamp=self._clock.now(),
            subject=request.subject,
            actor=actor_override or request.actor,
            delegation_depth=request.delegation_depth,
            action=f"approval:{request.request_id}",
            resource=request.tool,
            outcome=outcome,
            reason=reason,
            args_hash=request.args_hash,
            decision_id=request.decision_id,
        )
