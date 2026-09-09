"""Caller derivation from OIDC tokens: agents (client credentials) versus humans (ADR 0011)."""

from datetime import UTC, datetime

import pytest

from headofcontext.core.errors import IdentityError, TokenInvalid
from headofcontext.identity import VerifiedIdentity
from headofcontext.identity.callers import (
    AgentDetection,
    Caller,
    caller_from_identity,
    require_human,
)


def identity(**claims: object) -> VerifiedIdentity:
    base: dict[str, object] = {"sub": "uuid-1", "azp": "headofcontext"}
    base.update(claims)
    return VerifiedIdentity(
        subject="user:" + str(base.get("preferred_username", base["sub"])),
        issuer="http://kc/realms/acme",
        groups=("group:rh",),
        expires_at=datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
        claims=base,
    )


def test_service_account_is_an_agent() -> None:
    caller = caller_from_identity(
        identity(azp="assistant", preferred_username="service-account-assistant")
    )
    assert caller == Caller(kind="agent", ref="agent:assistant")


def test_client_id_claim_wins() -> None:
    caller = caller_from_identity(
        identity(azp="x", clientId="mailer", preferred_username="service-account-mailer")
    )
    assert caller.ref == "agent:mailer"


def test_human_is_a_user() -> None:
    caller = caller_from_identity(identity(preferred_username="alice.martin"))
    assert caller == Caller(kind="user", ref="user:alice.martin")


def test_agent_name_must_be_a_reference() -> None:
    with pytest.raises(IdentityError):
        caller_from_identity(
            identity(azp="bad client", preferred_username="service-account-bad client")
        )


def test_service_account_without_client_hint_is_refused() -> None:
    with pytest.raises(IdentityError):
        caller_from_identity(identity(preferred_username="service-account-"))


def test_require_human_refuses_service_accounts() -> None:
    """T20: an agent's JWT can never stand in for the human in `user_token`."""
    assert require_human(identity(preferred_username="alice.martin")) == "user:alice.martin"
    with pytest.raises(TokenInvalid) as exc:
        require_human(identity(azp="assistant", preferred_username="service-account-assistant"))
    assert exc.value.reason == "user_token_not_human"
    with pytest.raises(TokenInvalid):
        require_human(identity(clientId="mailer", preferred_username="someone"))


class TestAgentDetection:
    """ADR 0019: agent detection is configuration, so Entra ID / Okta client credentials work."""

    def test_entra_shaped_client_credentials_token(self) -> None:
        detection = AgentDetection.from_settings("idtyp=app", "azp")
        token = identity(idtyp="app", azp="8f2c-app-id", preferred_username="")
        assert caller_from_identity(token, detection) == Caller(
            kind="agent", ref="agent:8f2c-app-id"
        )

    def test_marker_value_must_match(self) -> None:
        detection = AgentDetection.from_settings("idtyp=app", "azp")
        human = identity(idtyp="user", azp="8f2c-app-id", preferred_username="alice.martin")
        assert caller_from_identity(human, detection).kind == "user"

    def test_marker_without_id_is_refused_not_downgraded(self) -> None:
        detection = AgentDetection.from_settings("idtyp=app", "appid")
        with pytest.raises(IdentityError):
            caller_from_identity(identity(idtyp="app", azp="", preferred_username=""), detection)

    def test_default_reproduces_keycloak_behaviour(self) -> None:
        default = AgentDetection.from_settings("clientId", "clientId")
        assert default == AgentDetection()
        assert caller_from_identity(identity(clientId="mailer"), default).ref == "agent:mailer"
        assert caller_from_identity(identity(preferred_username="bob"), default).kind == "user"


class TestServiceAccountFallback:
    """The `service-account-` username fallback is Keycloak's and needs `azp` to agree."""

    def test_human_named_like_a_service_account_stays_a_human(self) -> None:
        impostor = identity(preferred_username="service-account-assistant", azp="headofcontext")
        assert caller_from_identity(impostor).kind == "user"

    def test_genuine_keycloak_service_account(self) -> None:
        genuine = identity(preferred_username="service-account-assistant", azp="assistant")
        assert caller_from_identity(genuine) == Caller(kind="agent", ref="agent:assistant")

    def test_fallback_disabled_for_other_providers(self) -> None:
        detection = AgentDetection.from_settings("idtyp=app", "azp", keycloak_fallback=False)
        genuine = identity(preferred_username="service-account-assistant", azp="assistant")
        assert caller_from_identity(genuine, detection).kind == "user"
