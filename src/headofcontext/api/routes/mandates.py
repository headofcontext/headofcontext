"""Standing mandates (ADR 0016)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request

from headofcontext.api.deps import User, svc
from headofcontext.api.schemas import (
    MandateCreateRequest,
    MandateModel,
    MandateRevokeRequest,
    MandatesResponse,
)
from headofcontext.api.version import API_VERSION

router = APIRouter()


@router.post(f"/{API_VERSION}/mandates", response_model=MandateModel)
async def mandates_create(
    request: Request, body: MandateCreateRequest, caller: User
) -> MandateModel:
    s = svc(request)
    now = s.tokens.now()
    mandate = await s.mandates.create(
        subject=caller.ref,
        agent=body.agent,
        scope=body.scope.to_core(),
        expires_at=now + timedelta(hours=body.expires_in_hours),
        created_by=caller.ref,
        max_token_ttl=(
            timedelta(minutes=body.max_token_ttl_minutes) if body.max_token_ttl_minutes else None
        ),
    )
    return MandateModel.from_core(mandate)


@router.get(f"/{API_VERSION}/mandates", response_model=MandatesResponse)
async def mandates_list(request: Request, caller: User) -> MandatesResponse:
    mandates = await svc(request).mandates.list_for(caller.ref)
    return MandatesResponse(mandates=[MandateModel.from_core(m) for m in mandates])


@router.delete(f"/{API_VERSION}/mandates/{{mandate_id}}", response_model=MandateModel)
async def mandates_revoke(
    request: Request, mandate_id: str, caller: User, body: MandateRevokeRequest | None = None
) -> MandateModel:
    reason = body.reason if body is not None else "revoked by subject"
    revoked = await svc(request).mandates.revoke(mandate_id, by=caller.ref, reason=reason)
    return MandateModel.from_core(revoked)
