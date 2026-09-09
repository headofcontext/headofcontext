"""PrincipalChain: who we act for, who acts, and what was delegated along the way (ADR 0002)."""

from __future__ import annotations

from dataclasses import dataclass, field

from headofcontext.core.errors import InvariantViolation, ScopeEscalation
from headofcontext.core.refs import validate_ref
from headofcontext.core.scope import Scope

# Bounded so that verification stays cheap and chain-stuffing is not a vector.
MAX_DELEGATION_DEPTH = 8


@dataclass(frozen=True, slots=True)
class Delegation:
    from_actor: str
    to_actor: str
    scope: Scope

    def __post_init__(self) -> None:
        validate_ref(self.from_actor, "agent")
        validate_ref(self.to_actor, "agent")
        if self.from_actor == self.to_actor:
            raise InvariantViolation("an agent cannot delegate to itself")


@dataclass(frozen=True, slots=True)
class PrincipalChain:
    subject: str
    actor: str
    delegation: tuple[Delegation, ...] = field(default=())
    scope: Scope = field(default_factory=Scope.empty)

    def __post_init__(self) -> None:
        # I3: the subject is always a human user. Anything else is an orphan agent.
        validate_ref(self.subject, "user")
        validate_ref(self.actor, "agent")
        if len(self.delegation) > MAX_DELEGATION_DEPTH:
            raise InvariantViolation(f"delegation depth exceeds {MAX_DELEGATION_DEPTH}")
        if not self.delegation:
            return
        if self.delegation[-1].to_actor != self.actor:
            raise InvariantViolation("actor must be the target of the last delegation")
        if self.delegation[-1].scope != self.scope:
            raise InvariantViolation("scope must equal the scope of the last delegation")
        previous = self.delegation[0]
        for link in self.delegation[1:]:
            if link.from_actor != previous.to_actor:
                raise InvariantViolation("delegation links are not contiguous")
            # I1: every hop can only narrow. The root scope is not recorded on purpose: it is
            # whatever the token says; only the links are validated against each other here.
            if not link.scope.is_subset_of(previous.scope):
                raise ScopeEscalation(f"{link.from_actor} -> {link.to_actor} widens the scope")
            previous = link

    @classmethod
    def root(cls, subject: str, actor: str, scope: Scope) -> PrincipalChain:
        return cls(subject=subject, actor=actor, delegation=(), scope=scope)

    @property
    def root_actor(self) -> str:
        return self.delegation[0].from_actor if self.delegation else self.actor

    @property
    def root_scope(self) -> Scope:
        """Scope of the root actor when known; equals ``scope`` for a root chain.

        A chain rebuilt from links only knows the first link's scope as an upper bound.
        """
        return self._root_scope if self._root_scope is not None else self.scope

    _root_scope: Scope | None = field(default=None, repr=False, compare=False)

    @property
    def depth(self) -> int:
        return len(self.delegation)

    def delegate(self, to_actor: str, scope: Scope) -> PrincipalChain:
        """Return a new chain one hop longer. Raises ScopeEscalation if ``scope`` widens (I1)."""
        if not scope.is_subset_of(self.scope):
            raise ScopeEscalation(f"{self.actor} -> {to_actor} widens the scope")
        link = Delegation(self.actor, to_actor, scope)
        return PrincipalChain(
            subject=self.subject,  # I2: the subject never changes.
            actor=to_actor,
            delegation=(*self.delegation, link),
            scope=scope,
            _root_scope=self.root_scope,
        )
