"""The read filter over HTTP (ADR 0010)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from headofcontext.api.deps import Agent, chain_of, permits_of, svc
from headofcontext.api.schemas import FilterRequest, FilterResponse
from headofcontext.api.version import API_VERSION
from headofcontext.read import filter_items

router = APIRouter()


@router.post(f"/{API_VERSION}/read/filter", response_model=FilterResponse)
async def read_filter(request: Request, body: FilterRequest, caller: Agent) -> FilterResponse:
    s = svc(request)
    chain = await chain_of(request, caller, body.token)
    items: list[dict[str, Any]] = [item.model_dump() for item in body.items]
    # The token's own checks are evaluated per document before the engine is asked (T19).
    result = await filter_items(
        chain,
        items,
        engine=s.engine,
        audit=s.audit,
        relation=body.relation,
        strategy=body.strategy,
        permits=permits_of(request, caller, body.token),
    )
    return FilterResponse(
        kept=[str(item["id"]) for item in result.kept],
        dropped=list(result.dropped),
        strategy=result.strategy,
    )
