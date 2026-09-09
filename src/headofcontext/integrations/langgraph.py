"""LangGraph / LangChain integration: wrap tools so every call goes through HeadOfContext.

    from headofcontext.integrations.langgraph import guard_tools
    tools = guard_tools([send_mail, export_hr], session)
    node = ToolNode(tools)

Requires the ``langgraph`` extra (``langchain-core``). Imported lazily.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from headofcontext.core.errors import ActionDenied, ConfigurationError
from headofcontext.integrations.guard import ApprovalPending, OnDeny, ToolGuard
from headofcontext.integrations.session import AgentSession


def guard_tools(
    tools: Sequence[Any],
    session: AgentSession,
    *,
    on_deny: OnDeny = "message",
    tool_map: dict[str, str] | None = None,
) -> list[Any]:
    """Return LangChain tools that authorize each call before delegating to the original."""
    try:
        from langchain_core.tools import BaseTool, StructuredTool  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigurationError("install the 'langgraph' extra to use this integration") from exc

    guard = ToolGuard(session, on_deny=on_deny, tool_map=tool_map)
    guarded: list[Any] = []
    for tool in tools:
        if not isinstance(tool, BaseTool):
            raise TypeError(f"expected a LangChain BaseTool, got {type(tool).__name__}")
        guarded.append(_wrap_langchain_tool(tool, guard, StructuredTool))
    return guarded


def _wrap_langchain_tool(tool: Any, guard: ToolGuard, structured_tool: Any) -> Any:
    name = str(tool.name)

    async def run_async(**kwargs: Any) -> Any:
        await guard.authorize(name, kwargs)
        return await tool.ainvoke(kwargs)

    def run_sync(**kwargs: Any) -> Any:
        try:
            _run(guard.authorize(name, kwargs))
        except (ActionDenied, ApprovalPending) as exc:
            return guard.refuse(name, exc)
        return tool.invoke(kwargs)

    async def run_async_refusing(**kwargs: Any) -> Any:
        try:
            await guard.authorize(name, kwargs)
        except (ActionDenied, ApprovalPending) as exc:
            return guard.refuse(name, exc)
        return await tool.ainvoke(kwargs)

    return structured_tool.from_function(
        func=run_sync,
        coroutine=run_async_refusing,
        name=name,
        description=str(tool.description),
        args_schema=tool.args_schema,
        return_direct=bool(getattr(tool, "return_direct", False)),
    )


def _run(coro: Any) -> Any:
    from headofcontext.integrations.guard import _run_sync  # noqa: PLC0415

    return _run_sync(coro)
