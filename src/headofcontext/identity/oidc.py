"""Generic OIDC provider (discovery + JWKS + RFC 8693) and the Keycloak flavour."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from headofcontext.core.errors import IdentityError, InvariantViolation
from headofcontext.core.refs import validate_ref
from headofcontext.identity.provider import ExchangedToken, VerifiedIdentity

log = logging.getLogger(__name__)

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105


@dataclass(frozen=True, slots=True)
class OidcConfig:
    issuer: str
    audience: str
    client_id: str
    client_secret: str = field(repr=False)
    subject_claim: str = "sub"
    groups_claim: str = "groups"
    internal_base_url: str | None = None
    """Where to reach the issuer from inside the deployment when it differs from ``issuer``
    (the value tokens carry). Discovery, JWKS and token endpoints are rewritten onto it."""
    algorithms: tuple[str, ...] = ("RS256", "ES256", "PS256")
    jwks_ttl: timedelta = timedelta(hours=1)
    jwks_min_refresh: timedelta = timedelta(seconds=30)
    """Shortest interval between two JWKS refreshes forced by a signature failure: a stream of
    forged tokens must not turn into a stream of fetches (T18). A genuine key rotation is picked
    up by the first forced refresh and waits at most one interval otherwise."""
    timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class _Metadata:
    jwks_uri: str
    token_endpoint: str


class OidcProvider:
    def __init__(
        self,
        config: OidcConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._now = now or (lambda: datetime.now(UTC))
        self._http = httpx.AsyncClient(transport=transport, timeout=config.timeout_seconds)
        self._metadata: _Metadata | None = None
        self._keys: KeySet | None = None
        self._keys_fetched_at: datetime | None = None
        self._last_forced_at: datetime | None = None

    async def verify_user_token(self, token: str) -> VerifiedIdentity:
        keys = await self._key_set()
        try:
            decoded = self._decode(token, keys)
        except IdentityError:
            # One refresh for key rotation, then fail closed; and at most one forced refresh per
            # `jwks_min_refresh`, so forged tokens cannot make the IdP pay for each attempt.
            if not self._may_force_refresh():
                raise
            self._last_forced_at = self._now()
            keys = await self._key_set(force=True)
            decoded = self._decode(token, keys)

        claims = decoded.claims
        self._validate_claims(claims)
        subject = self._subject(claims)
        groups = self._groups(claims)
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        return VerifiedIdentity(
            subject=subject,
            issuer=str(claims["iss"]),
            groups=groups,
            expires_at=expires_at,
            claims=dict(claims),
        )

    async def exchange(
        self,
        *,
        subject_token: str,
        actor_token: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
    ) -> ExchangedToken:
        metadata = await self._discover()
        params: dict[str, str] = {
            "subject_token": subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
        }
        if actor_token is not None:
            params["actor_token"] = actor_token
            params["actor_token_type"] = ACCESS_TOKEN_TYPE
        if audience is not None:
            params["audience"] = audience
        if scope is not None:
            params["scope"] = scope
        # RFC 8693 is a plain form POST with client_secret_post authentication. Done directly
        # over httpx: Authlib's httpx client integration breaks when the httpx2 package is
        # present, which agent frameworks pull in (ADR 0004, amendment).
        form = {
            "grant_type": TOKEN_EXCHANGE_GRANT,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            **params,
        }
        try:
            raw = await self._http.post(metadata.token_endpoint, data=form)
            response = raw.json()
            if raw.status_code != 200 or not isinstance(response, dict):
                error = response.get("error") if isinstance(response, dict) else "http_error"
                log.warning("token exchange refused: %s", error)
                raise IdentityError("token exchange failed")
        except IdentityError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("token exchange failed: %s", type(exc).__name__)
            raise IdentityError("token exchange failed") from exc
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise IdentityError("token exchange returned no access token")
        expires_in = response.get("expires_in")
        return ExchangedToken(
            access_token=access_token,
            token_type=str(response.get("token_type", "Bearer")),
            expires_in=int(expires_in) if expires_in is not None else None,
            issued_token_type=_opt_str(response.get("issued_token_type")),
            scope=_opt_str(response.get("scope")),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- internals -----------------------------------------------------------------------------

    def _decode(self, token: str, keys: KeySet) -> jwt.Token:
        try:
            return jwt.decode(token, keys, algorithms=list(self._config.algorithms))
        except (JoseError, ValueError, KeyError, TypeError) as exc:
            raise IdentityError("token signature or structure invalid") from exc

    def _validate_claims(self, claims: dict[str, Any]) -> None:
        registry = jwt.JWTClaimsRegistry(
            now=int(self._now().timestamp()),
            leeway=0,
            iss={"essential": True, "value": self._config.issuer},
            aud={"essential": True, "value": self._config.audience},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(claims)
        except JoseError as exc:
            raise IdentityError(f"claim validation failed: {type(exc).__name__}") from exc

    def _subject(self, claims: dict[str, Any]) -> str:
        raw = claims.get(self._config.subject_claim) or claims.get("sub")
        if not isinstance(raw, str) or not raw:
            raise IdentityError("token has no usable subject claim")
        try:
            return validate_ref(f"user:{raw}", "user")
        except InvariantViolation as exc:
            raise IdentityError("subject claim contains unsupported characters") from exc

    def _groups(self, claims: dict[str, Any]) -> tuple[str, ...]:
        raw = claims.get(self._config.groups_claim, [])
        if not isinstance(raw, list):
            return ()
        groups: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            name = self._group_name(item)
            try:
                groups.append(validate_ref(f"group:{name}", "group"))
            except InvariantViolation:
                log.warning("ignoring group with unsupported characters")
        return tuple(groups)

    def _group_name(self, raw: str) -> str:
        return raw

    async def _discover(self) -> _Metadata:
        if self._metadata is not None:
            return self._metadata
        base = (self._config.internal_base_url or self._config.issuer).rstrip("/")
        url = base + "/.well-known/openid-configuration"
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            data = response.json()
            metadata = _Metadata(
                jwks_uri=self._internal(str(data["jwks_uri"])),
                token_endpoint=self._internal(str(data["token_endpoint"])),
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise IdentityError("OIDC discovery failed") from exc
        # With an internal URL the IdP reports whichever issuer matches the host it was reached
        # on; the `iss` claim of every token is still checked against the configured issuer.
        if (
            self._config.internal_base_url is None
            and str(data.get("issuer", "")) != self._config.issuer
        ):
            raise IdentityError("OIDC discovery issuer mismatch")
        self._metadata = metadata
        return metadata

    def _internal(self, url: str) -> str:
        """Rewrite an issuer-relative endpoint onto the internal base URL when configured."""
        internal = self._config.internal_base_url
        if internal is None:
            return url
        public = self._config.issuer.rstrip("/")
        if url.startswith(public):
            return internal.rstrip("/") + url[len(public) :]
        # The IdP answered with its own idea of the public host: keep the path, swap the origin.
        parsed = httpx.URL(url)
        base = httpx.URL(internal)
        return str(base.copy_with(path=parsed.path, query=parsed.query or None))

    def _may_force_refresh(self) -> bool:
        # Throttles refreshes *forced by failures* only: a genuine key rotation is picked up by
        # the first forced refresh, and waits at most one window if forged tokens used it up.
        return (
            self._last_forced_at is None
            or self._now() - self._last_forced_at >= self._config.jwks_min_refresh
        )

    async def _key_set(self, *, force: bool = False) -> KeySet:
        fresh = (
            self._keys is not None
            and self._keys_fetched_at is not None
            and self._now() - self._keys_fetched_at < self._config.jwks_ttl
        )
        if self._keys is not None and fresh and not force:
            return self._keys
        metadata = await self._discover()
        try:
            response = await self._http.get(metadata.jwks_uri)
            response.raise_for_status()
            keys = KeySet.import_key_set(response.json())
        except (httpx.HTTPError, ValueError, TypeError, JoseError) as exc:
            raise IdentityError("JWKS fetch failed") from exc
        self._keys = keys
        self._keys_fetched_at = self._now()
        return keys


class KeycloakProvider(OidcProvider):
    """Keycloak: group claims are paths like ``/rh`` or ``/direction/rh``."""

    def _group_name(self, raw: str) -> str:
        return raw.lstrip("/")


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None
