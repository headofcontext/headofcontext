"""Built-in decision policies for tool calls (ADR 0008)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from headofcontext.core import (
    ALLOW,
    Action,
    Capability,
    DecisionContext,
    DecisionPolicy,
    Kind,
    Outcome,
    PrincipalChain,
    Verdict,
    most_restrictive,
)

Predicate = Callable[[Mapping[str, Any]], bool]


def _matches(patterns: Sequence[Capability], resource: str) -> bool:
    return any(p.covers(Capability(Kind.ACT, resource)) for p in patterns)


class RequireApproval:
    """REQUIRE_APPROVAL on matching tools unless the context carries a validated approval."""

    def __init__(
        self,
        tools: Sequence[str],
        *,
        when: Predicate | None = None,
        reason: str = "approval_required",
    ) -> None:
        self._patterns = [Capability(Kind.ACT, t) for t in tools]
        self._when = when
        self._reason = reason

    def evaluate(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> Verdict:
        if action.kind is not Kind.ACT or not _matches(self._patterns, resource):
            return ALLOW
        if self._when is not None:
            try:
                if not self._when(args):
                    return ALLOW
            except Exception:
                return Verdict(Outcome.DENY, "policy_error")
        if context.approval_id is not None:
            return ALLOW
        return Verdict(Outcome.REQUIRE_APPROVAL, self._reason)


class DenyWhen:
    """DENY on matching tools when the predicate over the arguments holds."""

    def __init__(self, tools: Sequence[str], predicate: Predicate, *, reason: str) -> None:
        self._patterns = [Capability(Kind.ACT, t) for t in tools]
        self._predicate = predicate
        self._reason = reason

    def evaluate(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> Verdict:
        if action.kind is not Kind.ACT or not _matches(self._patterns, resource):
            return ALLOW
        try:
            hit = self._predicate(args)
        except Exception:
            return Verdict(Outcome.DENY, "policy_error")
        return Verdict(Outcome.DENY, self._reason) if hit else ALLOW


class CompositePolicy:
    """Evaluates every policy; the most restrictive verdict wins."""

    def __init__(self, policies: Sequence[DecisionPolicy]) -> None:
        self._policies = tuple(policies)

    def evaluate(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> Verdict:
        verdict = ALLOW
        for policy in self._policies:
            verdict = most_restrictive(
                verdict, policy.evaluate(chain, action, resource, args, context)
            )
        return verdict
