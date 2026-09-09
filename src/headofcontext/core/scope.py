"""Scope = frozen set of capabilities. Comparison is syntactic; OpenFGA decides the rest."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from headofcontext.core.refs import validate_resource_pattern


class Kind(StrEnum):
    READ = "read"
    ACT = "act"
    REMEMBER = "remember"


@dataclass(frozen=True, slots=True, order=True)
class Capability:
    kind: Kind
    resource: str

    def __post_init__(self) -> None:
        validate_resource_pattern(self.resource)

    @property
    def is_pattern(self) -> bool:
        return self.resource.endswith("*")

    def covers(self, other: Capability) -> bool:
        """True if every resource matched by ``other`` is matched by ``self``."""
        if self.kind is not other.kind:
            return False
        if not self.is_pattern:
            return self.resource == other.resource
        prefix = self.resource[:-1]
        # A wildcard other is covered only when its own prefix is at least as long as ours.
        return other.resource.startswith(prefix)


@dataclass(frozen=True, slots=True)
class Scope:
    capabilities: frozenset[Capability]

    @classmethod
    def of(cls, *caps: Capability) -> Scope:
        return cls(frozenset(caps))

    @classmethod
    def empty(cls) -> Scope:
        return cls(frozenset())

    @classmethod
    def from_iterable(cls, caps: Iterable[Capability]) -> Scope:
        return cls(frozenset(caps))

    def covers(self, cap: Capability) -> bool:
        return any(own.covers(cap) for own in self.capabilities)

    def is_subset_of(self, other: Scope) -> bool:
        return all(other.covers(cap) for cap in self.capabilities)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self.capabilities)

    def __len__(self) -> int:
        return len(self.capabilities)

    def __contains__(self, cap: object) -> bool:
        return cap in self.capabilities
