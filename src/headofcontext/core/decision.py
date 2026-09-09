"""Action, Outcome and Decision value objects (ADR 0002)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from headofcontext.core.chain import PrincipalChain
from headofcontext.core.scope import Kind


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True, slots=True)
class Action:
    """What the actor wants to do, and the OpenFGA relation that grants it to the subject."""

    kind: Kind
    relation: str

    @classmethod
    def read(cls) -> Action:
        return cls(Kind.READ, "viewer")

    @classmethod
    def edit(cls) -> Action:
        return cls(Kind.ACT, "editor")

    @classmethod
    def invoke(cls) -> Action:
        return cls(Kind.ACT, "can_invoke")

    @classmethod
    def remember(cls) -> Action:
        """Create a memory derived from documents the subject can view."""
        return cls(Kind.REMEMBER, "viewer")

    @classmethod
    def read_memory(cls) -> Action:
        """Recall a memory: READ scope on the memory, viewer on every source document."""
        return cls(Kind.READ, "viewer")


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    reason: str
    chain: PrincipalChain
    resource: str
    action: Action
    timestamp: datetime
    engine_latency_ms: float
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    resources: tuple[str, ...] = ()
    """Every resource the engine was asked about; ``resource`` is the one named in the audit."""

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW
