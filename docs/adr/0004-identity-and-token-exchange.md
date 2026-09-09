# ADR 0004 — OIDC identity and RFC 8693 token exchange

Status: Accepted — 2026-09-07

## Context

The `subject` of a chain must come from a verified identity, and actions must run under that
identity, not under a technical account (confused deputy). Keycloak is the dev IdP; Entra ID is a
premium connector.

## Decision

- `IdentityProvider` is a Protocol with two operations:
  - `verify_user_token(jwt) -> VerifiedIdentity(subject, groups, issuer, expires_at, claims)`.
    Validates signature against the issuer's JWKS, `iss`, `aud`, `exp`, `nbf`. Any failure raises
    `IdentityError` → DENY.
  - `exchange(subject_token, actor_token, audience, scope) -> ExchangedToken` implementing
    RFC 8693 with `subject_token_type=urn:ietf:params:oauth:token-type:access_token` and an
    `actor_token` so the issued token carries an `act` claim naming the agent. The delegated token
    is what connectors use to call downstream APIs on the user's behalf.
- `KeycloakProvider` implements both against Keycloak's OIDC discovery document. Client credentials
  are loaded from environment, never logged.
- The `act` claim chain in the exchanged token mirrors the `PrincipalChain.delegation` list. It is
  informative for downstream services; authorization inside HeadOfContext relies on the biscuit
  (ADR 0003), not on the `act` claim.
- Group membership from the IdP is synchronized into OpenFGA tuples (`group:X#member@user:Y`) by an
  identity connector; a stale connector (last sync older than `max_staleness`) makes the engine
  answer DENY (I5, "connector late").

## Amendment (2026-09-07, phase 2)

The RFC 8693 request is sent as a plain form POST through `httpx` with `client_secret_post`
authentication. Authlib's httpx client integration silently switches to the `httpx2` package
when it is installed (agent frameworks such as CrewAI pull it in) and then rejects httpx 0.x
transports. Authlib is therefore no longer a dependency; `joserfc` covers JWT verification.

## Consequences

- No user token ever leaves the process except in the token exchange request to the IdP.
- Token strings are never logged; only `sha256` fingerprints.
