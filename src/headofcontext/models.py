"""Wire models shared by the HTTP layer and the integrations (ADR 0023): how a chain, a decision,
an approval and a memory look once serialized. Request bodies stay in ``api.schemas``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from headofcontext.actions import ApprovalRequest
from headofcontext.core import Capability, Decision, Kind, PrincipalChain, Scope
from headofcontext.core.errors import InvalidResource
from headofcontext.memory import Memory

KindName = Literal["read", "act", "remember"]


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: KindName
    resource: str = Field(min_length=3, max_length=512)

    @field_validator("resource")
    @classmethod
    def _valid_resource(cls, value: str) -> str:
        try:
            Capability(Kind.READ, value)
        except InvalidResource as exc:
            raise ValueError(str(exc)) from exc
        return value

    def to_core(self) -> Capability:
        return Capability(Kind(self.kind), self.resource)


class ScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: list[CapabilityModel] = Field(max_length=256)

    def to_core(self) -> Scope:
        return Scope.from_iterable(c.to_core() for c in self.capabilities)

    @classmethod
    def from_core(cls, scope: Scope) -> ScopeModel:
        return cls(
            capabilities=[
                CapabilityModel(kind=c.kind.value, resource=c.resource) for c in sorted(scope)
            ]
        )


class DelegationModel(BaseModel):
    from_actor: str
    to_actor: str
    scope: ScopeModel


class ChainModel(BaseModel):
    subject: str
    actor: str
    root_actor: str
    depth: int
    scope: ScopeModel
    delegation: list[DelegationModel]

    @classmethod
    def from_core(cls, chain: PrincipalChain) -> ChainModel:
        return cls(
            subject=chain.subject,
            actor=chain.actor,
            root_actor=chain.root_actor,
            depth=chain.depth,
            scope=ScopeModel.from_core(chain.scope),
            delegation=[
                DelegationModel(
                    from_actor=d.from_actor,
                    to_actor=d.to_actor,
                    scope=ScopeModel.from_core(d.scope),
                )
                for d in chain.delegation
            ],
        )


class DecisionModel(BaseModel):
    outcome: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reason: str
    decision_id: str
    resource: str
    timestamp: datetime
    engine_latency_ms: float

    @classmethod
    def from_core(cls, decision: Decision) -> DecisionModel:
        return cls(
            outcome=decision.outcome.value,
            reason=decision.reason,
            decision_id=decision.decision_id,
            resource=decision.resource,
            timestamp=decision.timestamp,
            engine_latency_ms=decision.engine_latency_ms,
        )


class ApprovalModel(BaseModel):
    request_id: str
    subject: str
    actor: str
    delegation_depth: int
    tool: str
    args_hash: str
    status: str
    created_at: datetime
    expires_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    approval_reason: str

    @classmethod
    def from_core(cls, request: ApprovalRequest) -> ApprovalModel:
        return cls(
            request_id=request.request_id,
            subject=request.subject,
            actor=request.actor,
            delegation_depth=request.delegation_depth,
            tool=request.tool,
            args_hash=request.args_hash,
            status=request.status.value,
            created_at=request.created_at,
            expires_at=request.expires_at,
            resolved_by=request.resolved_by,
            resolved_at=request.resolved_at,
            resolution_reason=request.resolution_reason,
            approval_reason=request.approval_reason,
        )


class MemoryModel(BaseModel):
    memory_id: str
    content: str
    derived_from: list[str]
    written_for: str
    written_by: str
    created_at: datetime | None

    @classmethod
    def from_core(cls, memory: Memory) -> MemoryModel:
        return cls(
            memory_id=memory.memory_id,
            content=memory.content,
            derived_from=list(memory.derived_from),
            written_for=memory.written_for,
            written_by=memory.written_by,
            created_at=memory.created_at,
        )


# -- requests ----------------------------------------------------------------------------------


__all__ = [
    "ApprovalModel",
    "CapabilityModel",
    "ChainModel",
    "DecisionModel",
    "DelegationModel",
    "KindName",
    "MemoryModel",
    "ScopeModel",
]
