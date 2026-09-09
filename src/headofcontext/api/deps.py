"""Request-scoped dependencies shared by the routers (ADR 0024)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, cast

from fastapi import Depends, Header, Request

from headofcontext.core import Kind, PrincipalChain
from headofcontext.core.blocking import offload
from headofcontext.core.errors import HocError, IdentityError
from headofcontext.identity.callers import Caller, caller_from_identity
from headofcontext.integrations import AgentSession
from headofcontext.memory import ResourcePermits
from headofcontext.services import Services


class CallerMismatch(HocError):
    reason = "caller_mismatch"


def svc(request: Request) -> Services:
    return cast(Services, request.app.state.services)


async def identity_of(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Caller:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise IdentityError("missing bearer token", reason="missing_bearer")
    token = authorization.split(" ", 1)[1].strip()
    identity = await svc(request).identity.verify_user_token(token)
    return caller_from_identity(identity, svc(request).agent_detection)


async def require_agent(caller: Annotated[Caller, Depends(identity_of)]) -> Caller:
    if caller.kind != "agent":
        raise CallerMismatch("this endpoint is for agents", reason="caller_not_agent")
    return caller


async def require_user(caller: Annotated[Caller, Depends(identity_of)]) -> Caller:
    if caller.kind != "user":
        raise CallerMismatch("this endpoint is for humans", reason="caller_not_user")
    return caller


Agent = Annotated[Caller, Depends(require_agent)]
User = Annotated[Caller, Depends(require_user)]


def session(request: Request, caller: Caller, token: str) -> AgentSession:
    s = svc(request)
    return AgentSession(token=token, caller=caller.ref, token_service=s.tokens, gate=s.gate)


async def chain_of(request: Request, caller: Caller, token: str) -> PrincipalChain:
    verified = await offload(svc(request).tokens.inspect, token, caller=caller.ref)
    return verified.chain


def permits_of(request: Request, caller: Caller, token: str) -> ResourcePermits:
    """The token's per-resource answer, asked by the memory service where resources appear."""
    tokens = svc(request).tokens

    def permits(operation: Kind, resources: Sequence[str]) -> Mapping[str, bool]:
        verified = tokens.verify_many(
            token, caller=caller.ref, operation=operation, resources=resources
        )
        return verified[1]

    return permits


__all__ = [
    "Agent",
    "CallerMismatch",
    "User",
    "chain_of",
    "identity_of",
    "permits_of",
    "require_agent",
    "require_user",
    "session",
    "svc",
]
