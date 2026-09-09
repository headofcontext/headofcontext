"""FastAPI application: the only surface the SDK talks to (ADR 0011).

No postponed annotations here: FastAPI resolves dependency annotations of the route closures at
definition time, and the callers are locals of ``create_app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from headofcontext.api.ratelimit import RateLimiter, RateLimitMiddleware
from headofcontext.api.requestid import RequestIdMiddleware
from headofcontext.api.routes import routers
from headofcontext.api.schemas import (
    ErrorResponse,
)
from headofcontext.api.version import API_VERSION
from headofcontext.core.errors import (
    ActionDenied,
    ApprovalError,
    ConnectorStale,
    HocError,
    IdentityError,
    InvariantViolation,
    MandateDenied,
    MemoryDenied,
    TokenInvalid,
    Unavailable,
)
from headofcontext.logging import configure_logging
from headofcontext.services import Services, build_services
from headofcontext.settings import Settings

_STATUS: dict[type[HocError], int] = {
    IdentityError: 401,
    TokenInvalid: 403,
    InvariantViolation: 403,
    ActionDenied: 403,
    MemoryDenied: 403,
    MandateDenied: 403,
    ApprovalError: 403,
    Unavailable: 503,
    ConnectorStale: 503,
}


def status_for(exc: HocError) -> int:
    """403 for every denial, 401 for identity, 429 by the limiter, 503 for any dependency down."""
    for klass, code in _STATUS.items():
        if isinstance(exc, klass):
            return code
    return 403


def create_app(settings: Settings, services: Services | None = None) -> FastAPI:
    owns_services = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services or build_services(settings)
        try:
            yield
        finally:
            if owns_services:
                await app.state.services.aclose()

    app = FastAPI(
        title="HeadOfContext",
        version="1.0.0",
        description="The single authorization layer for enterprise AI agents.",
        lifespan=lifespan,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    if services is not None:
        app.state.services = services
    if settings.rate_limit_per_minute > 0:
        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(settings.rate_limit_per_minute),
            exempt_paths=frozenset({f"/{API_VERSION}/health"}),
        )
    # Added last, so outermost: even a 429 carries the request id and runs in its context.
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(HocError)
    async def _hoc_error(request: Request, exc: HocError) -> JSONResponse:
        return JSONResponse(
            status_code=status_for(exc), content={"reason": exc.reason, "detail": str(exc)}
        )

    for router in routers:
        app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    """Entry point for uvicorn: ``uvicorn headofcontext.api:app_from_env --factory``."""
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(settings)
