"""visible_tools: the tools a session may be shown (ADR 0030).

The catalog is a courtesy to the context window, never an authorization: the gate runs on every
call whatever the model was shown. It asks the same two sources as the gate, the biscuit per
resource and the decider (scope, OpenFGA ``can_invoke`` for the subject, approval policy), so a
visible tool is one the gate would at least consider. Every failure hides everything (I5), and
one ``tools_listed`` event is journaled per listing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from headofcontext.actions import ActionGate
from headofcontext.core import Action, Decider, Kind, Outcome
from headofcontext.core.blocking import offload
from headofcontext.core.errors import ActionDenied, TokenInvalid
from headofcontext.core.events import AuditEvent, EventKind, fingerprint
from headofcontext.integrations.session import AgentSession, tool_resource

_TOOL_TYPE = "tool"


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    """``visible`` and ``hidden`` hold the names as given, in order; ``outcomes`` is keyed by
    name. ``token_refused`` counts the resources the biscuit refused before the engine was
    asked."""

    visible: list[str]
    hidden: list[str]
    token_refused: int = 0
    outcomes: dict[str, Outcome] = field(default_factory=dict, repr=False)


async def visible_tools(
    session: AgentSession, tools: Sequence[str], *, gate: ActionGate | None = None
) -> ToolCatalog:
    """Keep the tools the session's chain may invoke. Names are ``tool:<id>`` or bare ids."""
    if not tools:
        return ToolCatalog(visible=[], hidden=[])
    gate = gate or session.gate
    decider: Decider = gate.decider
    resources = {name: _resource(name) for name in tools}
    wanted = sorted({r for r in resources.values() if r is not None})
    try:
        chain = await session.chain()
        answers = await offload(session.permits, Kind.ACT, wanted) if wanted else {}
    except TokenInvalid as exc:
        # No chain to attach: the token did not prove one. Still never an unlogged denial.
        await offload(
            gate.audit_raw,
            AuditEvent(
                kind=EventKind.TOOLS_LISTED,
                timestamp=gate.now(),
                subject="-",
                actor=session.caller,
                delegation_depth=0,
                action="act:can_invoke",
                resource=f"{_TOOL_TYPE}:*",
                outcome="DENY",
                reason=exc.reason,
                token_fingerprint=fingerprint(session.token),
            ),
        )
        return ToolCatalog(
            visible=[], hidden=list(tools), outcomes=dict.fromkeys(tools, Outcome.DENY)
        )
    token_refused = {r for r in wanted if not answers.get(r, False)}
    candidates = [r for r in wanted if r not in token_refused]
    by_resource: dict[str, Outcome] = dict.fromkeys(token_refused, Outcome.DENY)
    for decision in await decider.decide_each(chain, Action.invoke(), candidates, audit_each=False):
        by_resource[decision.resource] = decision.outcome

    outcomes: dict[str, Outcome] = {}
    visible: list[str] = []
    hidden: list[str] = []
    for name in tools:
        resource = resources[name]
        outcome = by_resource.get(resource, Outcome.DENY) if resource else Outcome.DENY
        outcomes[name] = outcome
        (hidden if outcome is Outcome.DENY else visible).append(name)
    await offload(
        gate.audit_raw,
        AuditEvent(
            kind=EventKind.TOOLS_LISTED,
            timestamp=gate.now(),
            subject=chain.subject,
            actor=chain.actor,
            delegation_depth=chain.depth,
            action="act:can_invoke",
            resource=f"{_TOOL_TYPE}:*",
            outcome="ALLOW" if visible else "DENY",
            reason=(
                f"visible={len(visible)} hidden={len(hidden)} token_refused={len(token_refused)} "
                f"visible_sha256={_digest(resources[n] or n for n in visible)[:16]}"
            ),
            args_hash=_digest(resources[n] or n for n in hidden),
        ),
    )
    return ToolCatalog(
        visible=visible, hidden=hidden, token_refused=len(token_refused), outcomes=outcomes
    )


def _resource(name: str) -> str | None:
    try:
        return tool_resource(name)
    except ActionDenied:
        return None


def _digest(refs: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()


__all__ = ["ToolCatalog", "visible_tools"]
