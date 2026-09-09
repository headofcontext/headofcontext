"""Bearer tokens → callers. Agents come from client credentials, humans from their own login."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from headofcontext.core.errors import IdentityError, InvariantViolation, TokenInvalid
from headofcontext.core.refs import validate_ref
from headofcontext.identity import VerifiedIdentity

SERVICE_ACCOUNT_PREFIX = "service-account-"


@dataclass(frozen=True, slots=True)
class Caller:
    kind: Literal["agent", "user"]
    ref: str


@dataclass(frozen=True, slots=True)
class AgentDetection:
    """How an agent (client credentials) token is told apart from a human's (ADR 0019).

    ``marker_claim`` present (and equal to ``marker_value`` when one is given) marks an agent;
    ``id_claim`` (falling back to ``azp``) carries the client id. Keycloak: ``clientId``.
    Entra ID: ``idtyp=app`` with ``azp``.
    """

    marker_claim: str = "clientId"
    marker_value: str | None = None
    id_claim: str = "clientId"
    # Keycloak names service accounts `service-account-<client>`; the fallback is only for it,
    # and only when `azp` names the same client, so a human cannot pick that username.
    keycloak_fallback: bool = True

    @classmethod
    def from_settings(
        cls, marker: str, id_claim: str, *, keycloak_fallback: bool = True
    ) -> AgentDetection:
        claim, sep, value = marker.partition("=")
        return cls(
            marker_claim=claim.strip() or "clientId",
            marker_value=value.strip() if sep else None,
            id_claim=id_claim.strip() or "clientId",
            keycloak_fallback=keycloak_fallback,
        )


DEFAULT_DETECTION = AgentDetection()


def caller_from_identity(
    identity: VerifiedIdentity, detection: AgentDetection = DEFAULT_DETECTION
) -> Caller:
    """An agent per ``detection`` (or a Keycloak service account) is named after its client id;
    anyone else is a human."""
    claims = identity.claims
    username = str(claims.get("preferred_username") or "")
    marker = claims.get(detection.marker_claim)
    marked = (
        marker is not None
        and marker != ""
        and (detection.marker_value is None or str(marker) == detection.marker_value)
    )
    if marked:
        raw_id = claims.get(detection.id_claim) or claims.get("azp")
        if not isinstance(raw_id, str) or not raw_id:
            # Marked as an agent but unnamed: never downgraded to a human.
            raise IdentityError("agent token without a client id")
        return _agent(raw_id)
    if detection.keycloak_fallback and username.startswith(SERVICE_ACCOUNT_PREFIX):
        name = username.removeprefix(SERVICE_ACCOUNT_PREFIX)
        if not name:
            raise IdentityError("service account token without a client id")
        if claims.get("azp") == name:
            return _agent(name)
    return Caller(kind="user", ref=identity.subject)


def require_human(identity: VerifiedIdentity, detection: AgentDetection = DEFAULT_DETECTION) -> str:
    """The subject of a *human* identity; an agent's token is refused (T20, I3).

    Used where a user token stands for the human's presence (`user_token` on issue): a
    service-account JWT must never mint a root biscuit for a pseudo-user.
    """
    caller = caller_from_identity(identity, detection)
    if caller.kind != "user":
        raise TokenInvalid(
            "user_token belongs to an agent, not a human", reason="user_token_not_human"
        )
    return caller.ref


def _agent(client_id: str) -> Caller:
    try:
        return Caller(kind="agent", ref=validate_ref(f"agent:{client_id}", "agent"))
    except InvariantViolation as exc:
        raise IdentityError("client id cannot be an agent reference") from exc
