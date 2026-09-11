"""ToolGuard: wrap a tool function so every call is authorized first (ADR 0009)."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from headofcontext.actions import GateResult
from headofcontext.core.errors import ActionDenied, HocError
from headofcontext.integrations.session import AgentSession

OnDeny = Literal["raise", "message"]


class ApprovalPending(HocError):
    """The call needs a human decision; ``request_id`` identifies the pending request."""

    reason = "approval_required"

    def __init__(self, request_id: str, tool: str) -> None:
        super().__init__(f"tool {tool!r} requires approval (request {request_id})")
        self.request_id = request_id
        self.tool = tool


class ToolGuard:
    def __init__(
        self,
        session: AgentSession,
        *,
        on_deny: OnDeny = "message",
        tool_map: Mapping[str, str] | None = None,
        default_resource: Callable[[str], str] | None = None,
    ) -> None:
        self._session = session
        self._on_deny = on_deny
        self._tool_map = dict(tool_map or {})
        self._default_resource = default_resource

    @property
    def session(self) -> AgentSession:
        return self._session

    def resource_for(self, name: str) -> str:
        """The tool resource a framework name is gated as: the map, else ``default_resource``
        (the name itself unless the integration says otherwise)."""
        mapped = self._tool_map.get(name)
        if mapped is not None:
            return mapped
        return self._default_resource(name) if self._default_resource else name

    async def authorize(self, name: str, args: Mapping[str, Any]) -> GateResult:
        """Raises ActionDenied / ApprovalPending unless the call is allowed."""
        resource = self.resource_for(name)
        result = await self._session.authorize(resource, args)
        if result.allowed:
            return result
        if result.pending and result.approval is not None:
            raise ApprovalPending(result.approval.request_id, name)
        raise ActionDenied(
            f"tool {name!r} denied: {result.decision.reason}", reason=result.decision.reason
        )

    def wrap_async(
        self, name: str, fn: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            call_args = _bind(fn, args, kwargs)
            try:
                await self.authorize(name, call_args)
            except (ActionDenied, ApprovalPending) as exc:
                return self.refuse(name, exc)
            return await fn(*args, **kwargs)

        return guarded

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            call_args = _bind(fn, args, kwargs)
            try:
                _run_sync(self.authorize(name, call_args))
            except (ActionDenied, ApprovalPending) as exc:
                return self.refuse(name, exc)
            return fn(*args, **kwargs)

        return guarded

    def refuse(self, name: str, exc: ActionDenied | ApprovalPending) -> str:
        if self._on_deny == "raise":
            raise exc
        return refusal_message(name, exc)


def refusal_message(name: str, exc: ActionDenied | ApprovalPending) -> str:
    """What the model reads instead of the tool output. Same wording in every integration."""
    if isinstance(exc, ApprovalPending):
        return (
            f"HeadOfContext: tool '{name}' requires approval "
            f"(request {exc.request_id}). Do not retry until it is approved."
        )
    return f"HeadOfContext denied tool '{name}': {exc.reason}. Do not retry."


def _bind(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Name positional arguments so that policies and audit hashes see the same shape."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return {"args": list(args), **kwargs}


def _run_sync(coro: Awaitable[Any]) -> Any:
    """Run a coroutine from sync code, whether or not an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(coro))
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(_await(coro))
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, name="hoc-guard", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


async def _await(coro: Awaitable[Any]) -> Any:
    return await coro
