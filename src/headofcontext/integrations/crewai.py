"""CrewAI integration: wrap ``crewai.tools.BaseTool`` instances with HeadOfContext.

    from headofcontext.integrations.crewai import guard_tools
    agent = Agent(..., tools=guard_tools([search_tool, mail_tool], session))

Requires the ``crewai`` extra. Imported lazily.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from headofcontext.core.errors import ConfigurationError
from headofcontext.integrations.guard import OnDeny, ToolGuard, _run_sync
from headofcontext.integrations.session import AgentSession


def guard_tools(
    tools: Sequence[Any],
    session: AgentSession,
    *,
    on_deny: OnDeny = "message",
    tool_map: dict[str, str] | None = None,
) -> list[Any]:
    """Return CrewAI tools that authorize each call before delegating to the original."""
    try:
        from crewai.tools import BaseTool  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigurationError("install the 'crewai' extra to use this integration") from exc

    guard = ToolGuard(session, on_deny=on_deny, tool_map=tool_map)

    class GuardedTool(BaseTool):
        inner: Any
        hoc_guard: Any

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            call_args = dict(kwargs)
            if args:
                call_args["args"] = list(args)
            try:
                _run_sync(self.hoc_guard.authorize(str(self.name), call_args))
            except Exception as exc:  # ActionDenied / ApprovalPending
                return self.hoc_guard.refuse(str(self.name), exc)
            return self.inner._run(*args, **kwargs)

    guarded: list[Any] = []
    for tool in tools:
        if not isinstance(tool, BaseTool):
            raise TypeError(f"expected a CrewAI BaseTool, got {type(tool).__name__}")
        guarded.append(
            GuardedTool(
                name=str(tool.name),
                description=str(tool.description),
                args_schema=tool.args_schema,
                inner=tool,
                hoc_guard=guard,
            )
        )
    return guarded
