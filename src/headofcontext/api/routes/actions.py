"""Gate and redeem tool calls (ADR 0008)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from headofcontext.api.deps import Agent, session
from headofcontext.api.schemas import (
    ApprovalModel,
    DecisionModel,
    GateRequest,
    GateResponse,
    RedeemRequest,
)
from headofcontext.api.version import API_VERSION

router = APIRouter()


@router.post(f"/{API_VERSION}/actions/gate", response_model=GateResponse)
async def actions_gate(request: Request, body: GateRequest, caller: Agent) -> GateResponse:
    result = await session(request, caller, body.token).authorize(body.tool, body.args)
    return GateResponse(
        decision=DecisionModel.from_core(result.decision),
        approval=ApprovalModel.from_core(result.approval) if result.approval else None,
    )


@router.post(f"/{API_VERSION}/actions/redeem", response_model=DecisionModel)
async def actions_redeem(request: Request, body: RedeemRequest, caller: Agent) -> DecisionModel:
    decision = await session(request, caller, body.token).redeem(
        body.request_id, body.tool, body.args
    )
    return DecisionModel.from_core(decision)
