"""Framework integrations. Everything goes through AgentSession and ToolGuard (ADR 0009)."""

from headofcontext.integrations.catalog import ToolCatalog, visible_tools
from headofcontext.integrations.guard import ApprovalPending, ToolGuard
from headofcontext.integrations.session import AgentSession

__all__ = ["AgentSession", "ApprovalPending", "ToolCatalog", "ToolGuard", "visible_tools"]
