"""Human approvals (ADR 0008, 0017)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from headofcontext.api.deps import User, svc
from headofcontext.api.schemas import ApprovalModel, ApprovalsResponse, ResolveRequest
from headofcontext.api.version import API_VERSION

router = APIRouter()


@router.get(f"/{API_VERSION}/approvals", response_model=ApprovalsResponse)
async def approvals_list(request: Request, caller: User) -> ApprovalsResponse:
    pending = await svc(request).gate.pending_for(caller.ref)
    return ApprovalsResponse(requests=[ApprovalModel.from_core(r) for r in pending])


@router.post(f"/{API_VERSION}/approvals/{{request_id}}/resolve", response_model=ApprovalModel)
async def approvals_resolve(
    request: Request, request_id: str, body: ResolveRequest, caller: User
) -> ApprovalModel:
    resolved = await svc(request).gate.resolve(
        request_id, approver=caller.ref, approved=body.approved, reason=body.reason
    )
    return ApprovalModel.from_core(resolved)
