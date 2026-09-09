"""Decision policies: extra constraints evaluated after scope and OpenFGA (ADR 0008).

A policy can only lower an outcome. It sees tool arguments as data and may constrain them, but
nothing in the arguments can turn a denial into an allowance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from headofcontext.core.chain import PrincipalChain
from headofcontext.core.decision import Action, Outcome


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    reason: str


ALLOW = Verdict(Outcome.ALLOW, "policy_allowed")

_SEVERITY = {Outcome.ALLOW: 0, Outcome.REQUIRE_APPROVAL: 1, Outcome.DENY: 2}


def most_restrictive(a: Verdict, b: Verdict) -> Verdict:
    return b if _SEVERITY[b.outcome] > _SEVERITY[a.outcome] else a


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """What the caller can legitimately assert about this call. Set by the gate, never by args."""

    approval_id: str | None = None


class DecisionPolicy(Protocol):
    def evaluate(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> Verdict: ...
