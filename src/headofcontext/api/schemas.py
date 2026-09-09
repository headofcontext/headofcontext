"""Request and response models: the contract the SDK is written against (ADR 0011)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from headofcontext.models import (
    ApprovalModel,
    CapabilityModel,
    ChainModel,
    DecisionModel,
    DelegationModel,
    KindName,
    MemoryModel,
    ScopeModel,
)
from headofcontext.tokens.biscuit import IssuedToken, VerifiedToken
from headofcontext.tokens.mandates import Mandate


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=65536)


class IssueRequest(BaseModel):
    """Exactly one of ``user_token`` (the human is present) or ``mandate_id`` (ADR 0016)."""

    model_config = ConfigDict(extra="forbid")
    user_token: str | None = Field(default=None, min_length=16, max_length=65536)
    mandate_id: str | None = Field(default=None, min_length=8, max_length=64)
    scope: ScopeModel
    ttl_minutes: int | None = Field(default=None, ge=1, le=24 * 60)

    @model_validator(mode="after")
    def _one_source(self) -> IssueRequest:
        if (self.user_token is None) == (self.mandate_id is None):
            raise ValueError("provide exactly one of user_token or mandate_id")
        return self


class MandateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = Field(min_length=7, max_length=256)
    scope: ScopeModel
    expires_in_hours: int = Field(ge=1, le=24 * 366)
    max_token_ttl_minutes: int | None = Field(default=None, ge=1, le=24 * 60)


class MandateModel(BaseModel):
    mandate_id: str
    subject: str
    agent: str
    scope: ScopeModel
    status: str
    created_at: datetime
    expires_at: datetime
    max_token_ttl_minutes: int
    created_by: str
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    @classmethod
    def from_core(cls, m: Mandate) -> MandateModel:
        return cls(
            mandate_id=m.mandate_id,
            subject=m.subject,
            agent=m.agent,
            scope=ScopeModel.from_core(m.scope),
            status=m.status.value,
            created_at=m.created_at,
            expires_at=m.expires_at,
            max_token_ttl_minutes=int(m.max_token_ttl.total_seconds() // 60),
            created_by=m.created_by,
            revoked_at=m.revoked_at,
            revocation_reason=m.revocation_reason,
        )


class MandatesResponse(BaseModel):
    mandates: list[MandateModel]


class MandateRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="revoked by subject", max_length=512)


class AttenuateRequest(TokenBody):
    to_actor: str = Field(min_length=7, max_length=256)
    scope: ScopeModel


class RevokeRequest(TokenBody):
    reason: str = Field(default="revoked by holder", max_length=512)


class TokenResponse(BaseModel):
    token: str
    chain: ChainModel
    expires_at: datetime
    revocation_ids: list[str]

    @classmethod
    def from_issued(cls, issued: IssuedToken) -> TokenResponse:
        return cls(
            token=issued.token,
            chain=ChainModel.from_core(issued.chain),
            expires_at=issued.expires_at,
            revocation_ids=list(issued.revocation_ids),
        )


class InspectResponse(BaseModel):
    chain: ChainModel
    expires_at: datetime
    revocation_ids: list[str]

    @classmethod
    def from_verified(cls, verified: VerifiedToken) -> InspectResponse:
        return cls(
            chain=ChainModel.from_core(verified.chain),
            expires_at=verified.expires_at,
            revocation_ids=list(verified.revocation_ids),
        )


class FilterItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Any = None


class FilterRequest(TokenBody):
    items: list[FilterItem] = Field(max_length=5000)
    relation: Literal["viewer", "editor"] = "viewer"
    strategy: Literal["auto", "batch", "list_objects"] = "auto"


class FilterResponse(BaseModel):
    kept: list[str]
    dropped: list[str]
    strategy: str


class GateRequest(TokenBody):
    tool: str = Field(min_length=6, max_length=512)
    args: dict[str, Any] = Field(default_factory=dict)


class GateResponse(BaseModel):
    decision: DecisionModel
    approval: ApprovalModel | None = None


class RedeemRequest(GateRequest):
    request_id: str = Field(min_length=1, max_length=128)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    reason: str = Field(default="", max_length=1024)


class ApprovalsResponse(BaseModel):
    requests: list[ApprovalModel]


class RememberRequest(TokenBody):
    content: str = Field(min_length=1, max_length=65536)
    derived_from: list[str] = Field(default_factory=list, max_length=256)


class RecallRequest(TokenBody):
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=10, ge=1, le=50)


class RecallResponse(BaseModel):
    memories: list[MemoryModel]


class ForgetRequest(TokenBody):
    memory_id: str = Field(min_length=8, max_length=256)


class HealthResponse(BaseModel):
    status: str
    service: str
    connectors: str


class ReadyResponse(BaseModel):
    """Readiness (ADR 0020): ``ready`` with every check ``ok``/``fresh``, else ``not_ready``."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class ErrorResponse(BaseModel):
    reason: str
    detail: str


__all__ = [
    "ApprovalModel",
    "ApprovalsResponse",
    "AttenuateRequest",
    "CapabilityModel",
    "ChainModel",
    "DecisionModel",
    "DelegationModel",
    "ErrorResponse",
    "FilterItem",
    "FilterRequest",
    "FilterResponse",
    "ForgetRequest",
    "GateRequest",
    "GateResponse",
    "HealthResponse",
    "InspectResponse",
    "IssueRequest",
    "KindName",
    "MandateCreateRequest",
    "MandateModel",
    "MandateRevokeRequest",
    "MandatesResponse",
    "MemoryModel",
    "ReadyResponse",
    "RecallRequest",
    "RecallResponse",
    "RedeemRequest",
    "RememberRequest",
    "ResolveRequest",
    "RevokeRequest",
    "ScopeModel",
    "TokenBody",
    "TokenResponse",
]
