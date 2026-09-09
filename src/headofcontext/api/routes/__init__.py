"""One router per function of the product (ADR 0024); `create_app` includes them in order."""

from headofcontext.api.routes import actions, approvals, mandates, memory, read, system, tokens

routers = [
    system.router,
    tokens.router,
    read.router,
    actions.router,
    approvals.router,
    mandates.router,
    memory.router,
]

__all__ = ["routers"]
