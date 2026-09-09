"""Provenance-aware memory over HTTP (ADR 0007, 0018)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from headofcontext.api.deps import Agent, chain_of, permits_of, svc
from headofcontext.api.schemas import (
    ForgetRequest,
    MemoryModel,
    RecallRequest,
    RecallResponse,
    RememberRequest,
)
from headofcontext.api.version import API_VERSION

router = APIRouter()


@router.post(f"/{API_VERSION}/memory/remember", response_model=MemoryModel)
async def memory_remember(request: Request, body: RememberRequest, caller: Agent) -> MemoryModel:
    chain = await chain_of(request, caller, body.token)
    memory = await svc(request).memory.remember(
        chain,
        body.content,
        derived_from=body.derived_from,
        permits=permits_of(request, caller, body.token),
    )
    return MemoryModel.from_core(memory)


@router.post(f"/{API_VERSION}/memory/recall", response_model=RecallResponse)
async def memory_recall(request: Request, body: RecallRequest, caller: Agent) -> RecallResponse:
    chain = await chain_of(request, caller, body.token)
    memories = await svc(request).memory.recall(
        chain, body.query, limit=body.limit, permits=permits_of(request, caller, body.token)
    )
    return RecallResponse(memories=[MemoryModel.from_core(m) for m in memories])


@router.post(f"/{API_VERSION}/memory/forget", response_model=MemoryModel)
async def memory_forget(request: Request, body: ForgetRequest, caller: Agent) -> MemoryModel:
    chain = await chain_of(request, caller, body.token)
    await svc(request).memory.forget(
        chain, body.memory_id, permits=permits_of(request, caller, body.token)
    )
    return MemoryModel(
        memory_id=body.memory_id,
        content="",
        derived_from=[],
        written_for=chain.subject,
        written_by=chain.actor,
        created_at=None,
    )
