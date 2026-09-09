"""OIDC verification and token exchange against the real Keycloak with the ACME realm."""

import httpx
import pytest

from headofcontext.core.errors import IdentityError
from headofcontext.identity import KeycloakProvider, OidcConfig
from tests.services import load_json

pytestmark = pytest.mark.integration

CLIENT_ID = "headofcontext"
CLIENT_SECRET = "hoc-dev-secret"


def _password_grant(keycloak_url: str, username: str) -> str:
    response = httpx.post(
        f"{keycloak_url}/realms/acme/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": username,
            "password": "password",
            "scope": "openid",
        },
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


@pytest.fixture
def provider(keycloak_url: str) -> KeycloakProvider:
    return KeycloakProvider(
        OidcConfig(
            issuer=f"{keycloak_url}/realms/acme",
            audience=CLIENT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            subject_claim="preferred_username",
        )
    )


def _first_user() -> dict[str, object]:
    users = load_json("users.json")
    assert isinstance(users, list)
    return dict(users[0])


async def test_verify_real_token(provider: KeycloakProvider, keycloak_url: str) -> None:
    user = _first_user()
    token = _password_grant(keycloak_url, str(user["username"]))
    identity = await provider.verify_user_token(token)
    assert identity.subject == user["id"]
    assert set(identity.groups) == set(user["groups"])


async def test_tampered_real_token_rejected(provider: KeycloakProvider, keycloak_url: str) -> None:
    token = _password_grant(keycloak_url, str(_first_user()["username"]))
    head, payload, sig = token.split(".")
    with pytest.raises(IdentityError):
        await provider.verify_user_token(f"{head}.{payload}.{sig[:-4]}AAAA")


async def test_token_exchange_to_nextcloud_audience(
    provider: KeycloakProvider, keycloak_url: str
) -> None:
    token = _password_grant(keycloak_url, str(_first_user()["username"]))
    exchanged = await provider.exchange(subject_token=token, audience="nextcloud")
    assert exchanged.access_token
    assert exchanged.access_token != token
