"""OIDC verification and RFC 8693 token exchange against a mocked issuer (ADR 0004)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from headofcontext.core.errors import IdentityError
from headofcontext.identity import KeycloakProvider, OidcConfig, VerifiedIdentity
from tests.conftest import FrozenClock

ISSUER = "http://keycloak.test/realms/acme"
AUDIENCE = "headofcontext"


class FakeIssuer:
    """Serves discovery, JWKS and a token endpoint over an httpx MockTransport."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, {"kid": "k1", "alg": "RS256", "use": "sig"})
        self.rotated = RSAKey.generate_key(2048, {"kid": "k2", "alg": "RS256", "use": "sig"})
        self.jwks_hits = 0
        self.token_requests: list[dict[str, str]] = []
        self.exchange_response: dict[str, Any] = {
            "access_token": "exchanged-token-xyz",
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": "openid",
        }
        self.exchange_status = 200

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                },
            )
        if path.endswith("/certs"):
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if path.endswith("/token"):
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_requests.append(form)
            return httpx.Response(self.exchange_status, json=self.exchange_response)
        return httpx.Response(404)

    def mint(self, key: RSAKey | None = None, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "0f9c-alice",
            "preferred_username": "alice",
            "groups": ["/rh", "/direction-elargie"],
            "exp": now + 300,
            "iat": now,
            "nbf": now - 5,
        }
        claims.update(overrides)
        key = key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


@pytest.fixture
def issuer() -> FakeIssuer:
    return FakeIssuer()


@pytest.fixture
def provider(issuer: FakeIssuer, clock: FrozenClock) -> KeycloakProvider:
    config = OidcConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        client_id="hoc",
        client_secret="not-a-real-secret",
        subject_claim="preferred_username",
        groups_claim="groups",
    )
    return KeycloakProvider(config, transport=issuer.transport())


class TestVerify:
    async def test_valid_token(self, provider: KeycloakProvider, issuer: FakeIssuer) -> None:
        identity = await provider.verify_user_token(issuer.mint())
        assert isinstance(identity, VerifiedIdentity)
        assert identity.subject == "user:alice"
        assert identity.groups == ("group:rh", "group:direction-elargie")
        assert identity.issuer == ISSUER
        assert "exp" in identity.claims

    async def test_subject_falls_back_to_sub(self, issuer: FakeIssuer) -> None:
        config = OidcConfig(issuer=ISSUER, audience=AUDIENCE, client_id="hoc", client_secret="x")
        provider = KeycloakProvider(config, transport=issuer.transport())
        identity = await provider.verify_user_token(issuer.mint())
        assert identity.subject == "user:0f9c-alice"

    async def test_jwks_cached(self, provider: KeycloakProvider, issuer: FakeIssuer) -> None:
        await provider.verify_user_token(issuer.mint())
        await provider.verify_user_token(issuer.mint())
        assert issuer.jwks_hits == 1

    async def test_unknown_kid_triggers_one_refresh_then_fails(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        await provider.verify_user_token(issuer.mint())
        with pytest.raises(IdentityError):
            await provider.verify_user_token(issuer.mint(key=issuer.rotated))
        assert issuer.jwks_hits == 2

    async def test_forged_tokens_do_not_refetch_jwks_on_every_call(
        self, issuer: FakeIssuer
    ) -> None:
        """T18: a forced refresh happens once per window, not once per bad token."""
        config = OidcConfig(
            issuer=ISSUER, audience=AUDIENCE, client_id="hoc", client_secret="not-a-real-secret"
        )
        # Minted claims use wall-clock time; the provider clock starts there and is advanced.
        clock = FrozenClock(datetime.now(UTC))
        provider = KeycloakProvider(config, transport=issuer.transport(), now=clock.now)
        await provider.verify_user_token(issuer.mint())
        for _ in range(5):
            with pytest.raises(IdentityError):
                await provider.verify_user_token(issuer.mint(key=issuer.rotated))
        assert issuer.jwks_hits == 2
        clock.tick(seconds=31)
        with pytest.raises(IdentityError):
            await provider.verify_user_token(issuer.mint(key=issuer.rotated))
        assert issuer.jwks_hits == 3

    @pytest.mark.parametrize(
        "overrides",
        [
            {"iss": "http://evil.test/realms/acme"},
            {"aud": "someone-else"},
            {"exp": int(time.time()) - 10},
            {"nbf": int(time.time()) + 600},
            {"sub": ""},
        ],
    )
    async def test_bad_claims_rejected(
        self, provider: KeycloakProvider, issuer: FakeIssuer, overrides: dict[str, Any]
    ) -> None:
        with pytest.raises(IdentityError):
            await provider.verify_user_token(issuer.mint(**overrides))

    async def test_wrong_signature_rejected(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        forged_key = RSAKey.generate_key(2048, {"kid": "k1"})  # same kid, different key
        with pytest.raises(IdentityError):
            await provider.verify_user_token(issuer.mint(key=forged_key))

    async def test_alg_none_rejected(self, provider: KeycloakProvider) -> None:
        header = httpx.Headers()  # noqa: F841 — keep httpx import used in both branches
        payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "exp": int(time.time()) + 60}
        import base64

        def b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        token = ".".join(
            [b64(json.dumps({"alg": "none"}).encode()), b64(json.dumps(payload).encode()), ""]
        )
        with pytest.raises(IdentityError):
            await provider.verify_user_token(token)

    async def test_garbage_rejected(self, provider: KeycloakProvider) -> None:
        with pytest.raises(IdentityError):
            await provider.verify_user_token("not.a.jwt")

    async def test_identity_repr_has_no_token(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        token = issuer.mint()
        identity = await provider.verify_user_token(token)
        assert token not in repr(identity)


class TestExchange:
    async def test_rfc8693_request_shape(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        exchanged = await provider.exchange(
            subject_token=issuer.mint(),
            actor_token="agent-client-token",
            audience="nextcloud",
            scope="files:read",
        )
        assert exchanged.access_token == "exchanged-token-xyz"
        assert exchanged.expires_in == 300
        form = issuer.token_requests[-1]
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert form["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert form["actor_token"] == "agent-client-token"
        assert form["actor_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert form["audience"] == "nextcloud"
        assert form["scope"] == "files:read"
        assert form["client_id"] == "hoc"

    async def test_exchange_failure_is_identity_error(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        issuer.exchange_status = 403
        issuer.exchange_response = {"error": "access_denied"}
        with pytest.raises(IdentityError):
            await provider.exchange(subject_token=issuer.mint(), audience="nextcloud")

    async def test_exchanged_token_never_in_repr(
        self, provider: KeycloakProvider, issuer: FakeIssuer
    ) -> None:
        exchanged = await provider.exchange(subject_token=issuer.mint(), audience="nextcloud")
        assert "exchanged-token-xyz" not in repr(exchanged)
        assert exchanged.fingerprint != "exchanged-token-xyz"


class TestDiscoveryFailure:
    async def test_unreachable_issuer_is_identity_error(self, issuer: FakeIssuer) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        config = OidcConfig(issuer=ISSUER, audience=AUDIENCE, client_id="hoc", client_secret="x")
        provider = KeycloakProvider(config, transport=httpx.MockTransport(down))
        with pytest.raises(IdentityError):
            await provider.verify_user_token(issuer.mint())


class TestInternalUrl:
    async def test_discovery_and_jwks_use_internal_base(self, issuer: FakeIssuer) -> None:
        """Tokens say http://keycloak.test (public); the service reaches the IdP at another host."""
        seen: list[str] = []

        def spy(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return issuer._handle(request)

        config = OidcConfig(
            issuer=ISSUER,
            audience=AUDIENCE,
            client_id="hoc",
            client_secret="x",
            internal_base_url="http://kc-internal:8080/realms/acme",
        )
        provider = KeycloakProvider(config, transport=httpx.MockTransport(spy))
        identity = await provider.verify_user_token(issuer.mint())
        assert identity.issuer == ISSUER
        assert all(url.startswith("http://kc-internal:8080/") for url in seen), seen
        await provider.exchange(subject_token=issuer.mint(), audience="nextcloud")
        assert seen[-1].startswith("http://kc-internal:8080/")
