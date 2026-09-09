# ADR 0009 — Framework integrations (LangGraph, CrewAI)

Status: Accepted — 2026-09-07

## Context

HeadOfContext is framework-independent (`AGENTS.md`) but must be a five-line change in the
frameworks people actually use. The integration surface is deliberately tiny: wrap tools.

## Decision

- A framework-agnostic `ToolGuard` (`headofcontext.integrations.guard`) turns an
  `AgentSession` into a function wrapper: `guard.wrap(name, fn)` authorizes every call through
  the session (biscuit verify + action gate) before invoking `fn`. On DENY it either raises
  `ActionDenied` or returns a short denial message to the model (`on_deny="message"`, default),
  never leaking the reason details beyond a reason code. On REQUIRE_APPROVAL it returns or raises
  `ApprovalPending` with the request id, so the framework can park the run.
- `headofcontext.integrations.langgraph.guard_tools(tools, session)` returns LangChain
  `StructuredTool`s wrapping each input tool (sync and async paths), usable in a LangGraph
  `ToolNode`.
- `headofcontext.integrations.crewai.guard_tools(tools, session)` returns CrewAI `BaseTool`
  subclasses delegating to the original tool after authorization.
- Tool naming: a framework tool `send_mail` maps to the resource `tool:send_mail` unless a
  `tool_map` overrides it. The name comes from the framework's tool object, never from the LLM
  text.
- Both frameworks are **optional extras** (`langgraph`, `crewai`). Their modules import them
  lazily; their tests are skipped when the extra is absent. The core test suite never depends on
  them.

## Consequences

- Arguments handed to the guard are the framework-parsed tool arguments (data). The guard hashes
  them for audit and passes them to policies; it never logs them.
- Memory (`MemoryService`) is not wrapped by the guard: it already carries its own decision path.
