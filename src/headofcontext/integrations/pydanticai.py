"""PydanticAI integration (ADR 0014): a toolset wrapper that gates every call.

Requires the optional ``pydanticai`` extra.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from headofcontext.core.errors import ActionDenied
from headofcontext.integrations.guard import ApprovalPending, OnDeny, ToolGuard, refusal_message
from headofcontext.integrations.session import AgentSession


class HocToolset[AgentDepsT](WrapperToolset[AgentDepsT]):
    """Wrap any toolset; definitions pass through, calls are authorized first."""

    def __init__(
        self,
        wrapped: AbstractToolset[AgentDepsT],
        session: AgentSession,
        *,
        on_deny: OnDeny = "message",
        tool_map: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(wrapped=wrapped)
        self._guard = ToolGuard(session, on_deny=on_deny, tool_map=tool_map)
        self._on_deny: OnDeny = on_deny

    @property
    def session(self) -> AgentSession:
        return self._guard.session

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        try:
            await self._guard.authorize(name, tool_args)
        except (ActionDenied, ApprovalPending) as exc:
            if self._on_deny == "raise":
                raise
            return refusal_message(name, exc)
        return await super().call_tool(name, tool_args, ctx, tool)


def guard_toolset[AgentDepsT](
    toolset: AbstractToolset[AgentDepsT],
    session: AgentSession,
    *,
    on_deny: OnDeny = "message",
    tool_map: Mapping[str, str] | None = None,
) -> HocToolset[AgentDepsT]:
    return HocToolset(toolset, session, on_deny=on_deny, tool_map=tool_map)
