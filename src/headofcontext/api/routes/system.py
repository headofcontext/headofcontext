"""Liveness, readiness and the root redirect."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from headofcontext.api.deps import svc
from headofcontext.api.schemas import HealthResponse, ReadyResponse
from headofcontext.api.version import API_VERSION
from headofcontext.core.blocking import offload
from headofcontext.core.errors import ConnectorStale

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs", status_code=307)


# -- health --------------------------------------------------------------------------------


@router.get(f"/{API_VERSION}/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = svc(request)
    try:
        await offload(s.freshness.assert_fresh)
        connectors = "fresh"
    except ConnectorStale as exc:
        connectors = f"stale:{exc.connector}"
    return HealthResponse(status="ok", service=s.settings.service_name, connectors=connectors)


@router.get(
    f"/{API_VERSION}/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
async def ready(request: Request) -> JSONResponse:
    """Readiness (ADR 0020): every dependency the next decision needs, checked now."""
    ready_now, checks = await svc(request).readiness.run()
    body = ReadyResponse(status="ready" if ready_now else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if ready_now else 503, content=body.model_dump())
