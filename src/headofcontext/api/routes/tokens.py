"""Issue, attenuate, inspect and revoke biscuits (ADR 0003, 0011, 0016)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request

from headofcontext.api.deps import Agent, svc
from headofcontext.api.schemas import (
    AttenuateRequest,
    InspectResponse,
    IssueRequest,
    RevokeRequest,
    TokenBody,
    TokenResponse,
)
from headofcontext.api.version import API_VERSION
from headofcontext.core import PrincipalChain
from headofcontext.core.blocking import offload
from headofcontext.core.errors import TokenInvalid
from headofcontext.core.events import AuditEvent, EventKind
from headofcontext.core.refs import validate_ref
from headofcontext.identity.callers import require_human
from headofcontext.services import Services

router = APIRouter()


@router.post(f"/{API_VERSION}/tokens/issue", response_model=TokenResponse)
async def issue(request: Request, body: IssueRequest, caller: Agent) -> TokenResponse:
    s = svc(request)
    ttl = timedelta(minutes=body.ttl_minutes) if body.ttl_minutes else None
    if body.mandate_id is not None:
        # ADR 0016: the human authorized this agent earlier; the mandate is the proof.
        issued = await s.mandates.issue(
            body.mandate_id, caller=caller.ref, scope=body.scope.to_core(), ttl=ttl
        )
    else:
        if body.user_token is None:  # the schema validator guarantees one of the two
            raise TokenInvalid("user_token or mandate_id is required", reason="token_invalid")
        identity = await s.identity.verify_user_token(body.user_token)
        try:
            subject = require_human(identity, s.agent_detection)  # T20: agents are not humans
        except TokenInvalid as exc:
            _deny_issue(s, identity.subject, caller.ref, exc.reason)
            raise
        bound = await s.engine.check(subject, "can_act_on_behalf_of", caller.ref)
        if not bound:
            _deny_issue(s, subject, caller.ref, "not_related")
            raise TokenInvalid("agent is not bound to this user", reason="not_related")
        chain = PrincipalChain.root(subject, caller.ref, body.scope.to_core())
        issued = await offload(s.tokens.issue, chain, ttl=ttl)
    return TokenResponse.from_issued(issued)


@router.post(f"/{API_VERSION}/tokens/attenuate", response_model=TokenResponse)
async def attenuate(request: Request, body: AttenuateRequest, caller: Agent) -> TokenResponse:
    s = svc(request)
    validate_ref(body.to_actor, "agent")
    await offload(s.tokens.inspect, body.token, caller=caller.ref)  # holder binding first
    issued = await offload(
        s.tokens.attenuate, body.token, to_actor=body.to_actor, scope=body.scope.to_core()
    )
    return TokenResponse.from_issued(issued)


@router.post(f"/{API_VERSION}/tokens/inspect", response_model=InspectResponse)
async def inspect(request: Request, body: TokenBody, caller: Agent) -> InspectResponse:
    verified = await offload(svc(request).tokens.inspect, body.token, caller=caller.ref)
    return InspectResponse.from_verified(verified)


@router.post(f"/{API_VERSION}/tokens/revoke", response_model=InspectResponse)
async def revoke(request: Request, body: RevokeRequest, caller: Agent) -> InspectResponse:
    s = svc(request)
    verified = await offload(s.tokens.inspect, body.token, caller=caller.ref)
    # Revokes this token's own block only, never a mandate id (ADR 0016 amendment).
    await offload(s.tokens.revoke, body.token, caller=caller.ref, reason=body.reason)
    return InspectResponse.from_verified(verified)


def _deny_issue(s: Services, subject: str, actor: str, reason: str) -> None:
    s.audit.record(
        AuditEvent(
            kind=EventKind.TOKEN_ISSUED,
            timestamp=s.tokens.now(),
            subject=subject,
            actor=actor,
            delegation_depth=0,
            action="token:issue",
            resource=actor,
            outcome="DENY",
            reason=reason,
        )
    )
