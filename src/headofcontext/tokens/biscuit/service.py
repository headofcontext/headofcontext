"""Issue, attenuate, verify and revoke biscuit tokens (ADR 0003)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import biscuit_auth

from headofcontext.core import MAX_DELEGATION_DEPTH, Clock, Kind, PrincipalChain, Scope, SystemClock
from headofcontext.core.errors import HocError, InvariantViolation, TokenInvalid, TokenRevoked
from headofcontext.core.events import AuditEvent, AuditSink, EventKind, fingerprint
from headofcontext.core.refs import validate_ref, validate_resource_pattern
from headofcontext.tokens.biscuit.datalog import (
    AUTHORIZER_POLICIES,
    attenuation_builder,
    authority_builder,
    parse_trail_block,
    read_authority,
    read_mandate,
)
from headofcontext.tokens.biscuit.keys import KeyRing
from headofcontext.tokens.biscuit.revocation import RevocationStore

DEFAULT_TTL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    chain: PrincipalChain
    expires_at: datetime
    revocation_ids: tuple[str, ...]
    mandate_id: str | None = None

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.token)


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    chain: PrincipalChain
    expires_at: datetime
    revocation_ids: tuple[str, ...]
    fingerprint: str
    mandate_id: str | None = None

    @property
    def subject(self) -> str:
        return self.chain.subject

    @property
    def actor(self) -> str:
        return self.chain.actor


@dataclass(frozen=True, slots=True)
class _Loaded:
    biscuit: biscuit_auth.Biscuit
    chain: PrincipalChain
    expires_at: datetime
    mandate_id: str | None = None

    @property
    def revocation_ids(self) -> tuple[str, ...]:
        """Block ids plus the mandate's own id, so one revocation covers every derived token."""
        ids = tuple(self.biscuit.revocation_ids)
        if self.mandate_id is not None:
            ids += (mandate_revocation_id(self.mandate_id),)
        return ids

    @property
    def own_revocation_id(self) -> str:
        """The id of this token's last block: what revoking *this token* means, and nothing more."""
        return str(tuple(self.biscuit.revocation_ids)[-1])


def mandate_revocation_id(mandate_id: str) -> str:
    return f"mandate:{mandate_id}"


def load(token: str, keyring: KeyRing) -> _Loaded:
    """Parse and cryptographically verify a token, then rebuild its PrincipalChain.

    Any structural or cryptographic problem raises TokenInvalid; the caller maps it to DENY.
    """
    try:
        unverified = biscuit_auth.UnverifiedBiscuit.from_base64(token)
    except Exception as exc:
        raise TokenInvalid("token is not a biscuit") from exc
    public_key = keyring.public_key(unverified.root_key_id())
    try:
        biscuit = biscuit_auth.Biscuit.from_base64(token, public_key)
    except Exception as exc:
        raise TokenInvalid("signature verification failed") from exc

    depth = biscuit.block_count() - 1
    if depth > MAX_DELEGATION_DEPTH:
        raise TokenInvalid(f"delegation depth {depth} exceeds {MAX_DELEGATION_DEPTH}")

    authorizer = biscuit_auth.AuthorizerBuilder("allow if true;").build(biscuit)
    subject, actor, scope, expires_at = read_authority(authorizer)
    mandate_id = read_mandate(authorizer)
    try:
        chain = PrincipalChain.root(subject, actor, scope)
        for index in range(1, biscuit.block_count()):
            parsed = parse_trail_block(biscuit.block_source(index))
            if parsed is None:
                continue  # opaque attenuation: restricts, never changes the chain
            (from_actor, to_actor), declared = parsed
            if from_actor != chain.actor:
                raise TokenInvalid("delegation trail is not contiguous")
            chain = chain.delegate(to_actor, declared)
    except InvariantViolation as exc:
        raise TokenInvalid(
            f"delegation trail violates an invariant: {exc}", reason=exc.reason
        ) from exc
    return _Loaded(biscuit, chain, expires_at, mandate_id)


def attenuate(
    token: str,
    keyring: KeyRing,
    *,
    to_actor: str,
    scope: Scope,
) -> IssuedToken:
    """Offline delegation: needs the public key only. Raises ScopeEscalation on widening (I1)."""
    validate_ref(to_actor, "agent")
    loaded = load(token, keyring)
    if loaded.chain.depth >= MAX_DELEGATION_DEPTH:
        raise TokenInvalid(f"delegation depth would exceed {MAX_DELEGATION_DEPTH}")
    child_chain = loaded.chain.delegate(to_actor, scope)  # I1 enforced here, before signing
    block = attenuation_builder(loaded.chain.actor, to_actor, scope)
    child = loaded.biscuit.append(block)
    revocation_ids = tuple(child.revocation_ids)
    if loaded.mandate_id is not None:
        revocation_ids += (mandate_revocation_id(loaded.mandate_id),)
    return IssuedToken(
        token=child.to_base64(),
        chain=child_chain,
        expires_at=loaded.expires_at,
        revocation_ids=revocation_ids,
        mandate_id=loaded.mandate_id,
    )


class TokenService:
    def __init__(
        self,
        keyring: KeyRing,
        revocations: RevocationStore,
        audit: AuditSink,
        clock: Clock | None = None,
        *,
        key_id: int = 1,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._keyring = keyring
        self._revocations = revocations
        self._audit = audit
        self._clock = clock or SystemClock()
        self._key_id = key_id
        self._ttl = ttl

    def issue(
        self,
        chain: PrincipalChain,
        *,
        ttl: timedelta | None = None,
        mandate_id: str | None = None,
    ) -> IssuedToken:
        """Issue a root token. Only chains of depth 0 are issued; delegation uses ``attenuate``."""
        if chain.depth != 0:
            raise TokenInvalid("only root chains are issued; use attenuate for delegation")
        issued_at = self._clock.now()
        expires_at = issued_at + (ttl or self._ttl)
        builder = authority_builder(
            chain.subject, chain.actor, chain.scope, issued_at, expires_at, mandate_id=mandate_id
        )
        builder.set_root_key_id(self._key_id)
        biscuit = builder.build(self._keyring.private_key(self._key_id))
        revocation_ids = tuple(biscuit.revocation_ids)
        if mandate_id is not None:
            revocation_ids += (mandate_revocation_id(mandate_id),)
        issued = IssuedToken(
            token=biscuit.to_base64(),
            chain=chain,
            expires_at=expires_at,
            revocation_ids=revocation_ids,
            mandate_id=mandate_id,
        )
        self._audit.record(self._token_event(EventKind.TOKEN_ISSUED, issued, "issued"))
        return issued

    def attenuate(self, token: str, *, to_actor: str, scope: Scope) -> IssuedToken:
        issued = attenuate(token, self._keyring, to_actor=to_actor, scope=scope)
        self._audit.record(self._token_event(EventKind.TOKEN_DELEGATED, issued, "delegated"))
        return issued

    def verify(self, token: str, *, caller: str, operation: Kind, resource: str) -> VerifiedToken:
        """Verify that ``caller`` may perform ``operation`` on ``resource`` with this token.

        The effective scope is the intersection of what the authority grants and what every
        attenuation check allows; the declared trail is only used for holder binding and audit.
        """
        validate_ref(caller, "agent")
        validate_resource_pattern(resource)
        loaded = load(token, self._keyring)
        revocation_ids = loaded.revocation_ids
        if self.is_revoked(revocation_ids):
            raise TokenRevoked("token contains a revoked block")
        if caller != loaded.chain.actor:
            raise TokenInvalid(f"caller {caller!r} is not the token holder")

        builder = biscuit_auth.AuthorizerBuilder(
            "caller({caller}); operation({operation}); resource({resource}); time({now});"
            + AUTHORIZER_POLICIES,
            {
                "caller": caller,
                "operation": str(operation),
                "resource": resource,
                "now": self._clock.now(),
            },
        )
        try:
            builder.build(loaded.biscuit).authorize()
        except biscuit_auth.AuthorizationError as exc:
            message = str(exc)
            if "check if time" in message:
                raise TokenInvalid("token expired") from exc
            raise TokenInvalid("operation not permitted by token checks") from exc
        return VerifiedToken(
            chain=loaded.chain,
            expires_at=loaded.expires_at,
            revocation_ids=revocation_ids,
            fingerprint=fingerprint(token),
            mandate_id=loaded.mandate_id,
        )

    def verify_many(
        self, token: str, *, caller: str, operation: Kind, resources: Sequence[str]
    ) -> tuple[VerifiedToken, dict[str, bool]]:
        """Like ``verify`` for many resources at once: load once, run the authorizer per resource.

        Returns the verified token and, for each distinct resource, whether the token's checks
        permit ``operation`` on it. Signature, revocation, expiry and holder binding are verified
        even when ``resources`` is empty; the per-resource answers never widen anything.
        """
        validate_ref(caller, "agent")
        loaded = load(token, self._keyring)
        revocation_ids = loaded.revocation_ids
        if self.is_revoked(revocation_ids):
            raise TokenRevoked("token contains a revoked block")
        if caller != loaded.chain.actor:
            raise TokenInvalid(f"caller {caller!r} is not the token holder")
        now = self._clock.now()
        if now >= loaded.expires_at:
            raise TokenInvalid("token expired")
        permits: dict[str, bool] = {}
        for resource in resources:
            if resource in permits:
                continue
            validate_resource_pattern(resource)
            permits[resource] = self._authorized(loaded, caller, operation, resource, now)
        return (
            VerifiedToken(
                chain=loaded.chain,
                expires_at=loaded.expires_at,
                revocation_ids=revocation_ids,
                fingerprint=fingerprint(token),
                mandate_id=loaded.mandate_id,
            ),
            permits,
        )

    def _authorized(
        self, loaded: _Loaded, caller: str, operation: Kind, resource: str, now: datetime
    ) -> bool:
        builder = biscuit_auth.AuthorizerBuilder(
            "caller({caller}); operation({operation}); resource({resource}); time({now});"
            + AUTHORIZER_POLICIES,
            {"caller": caller, "operation": str(operation), "resource": resource, "now": now},
        )
        try:
            builder.build(loaded.biscuit).authorize()
        except biscuit_auth.AuthorizationError:
            return False
        return True

    def now(self) -> datetime:
        return self._clock.now()

    def inspect(self, token: str, *, caller: str) -> VerifiedToken:
        """Verify signature, revocation, expiry and holder binding without an operation.

        Use it to rebuild the chain for display or delegation; every actual operation must go
        through ``verify`` with its operation and resource.
        """
        validate_ref(caller, "agent")
        loaded = load(token, self._keyring)
        revocation_ids = loaded.revocation_ids
        if self.is_revoked(revocation_ids):
            raise TokenRevoked("token contains a revoked block")
        if caller != loaded.chain.actor:
            raise TokenInvalid(f"caller {caller!r} is not the token holder")
        if self._clock.now() >= loaded.expires_at:
            raise TokenInvalid("token expired")
        return VerifiedToken(
            chain=loaded.chain,
            expires_at=loaded.expires_at,
            revocation_ids=revocation_ids,
            fingerprint=fingerprint(token),
            mandate_id=loaded.mandate_id,
        )

    def revoke(self, token: str, *, caller: str, reason: str) -> str:
        """Revoke the token ``caller`` holds and return the block id that was revoked.

        Only the token's own last block is revoked: never a `mandate:` id, which belongs to the
        subject (ADR 0016 amendment). A delegate can kill its copy, not its principal's grant.
        """
        loaded = load(token, self._keyring)
        if caller != loaded.chain.actor:
            raise TokenInvalid(f"caller {caller!r} is not the token holder")
        self.revoke_id(loaded.own_revocation_id, reason=reason)
        return loaded.own_revocation_id

    def is_revoked(self, revocation_ids: tuple[str, ...]) -> bool:
        """True if any id is revoked; raises TokenInvalid if the store cannot answer (I5)."""
        try:
            return self._revocations.is_revoked(revocation_ids)
        except HocError:
            raise
        except Exception as exc:  # I5: an unreachable revocation store means the token is unusable
            raise TokenInvalid("revocation store unavailable") from exc

    def revoke_id(self, revocation_id: str, *, reason: str) -> None:
        now = self._clock.now()
        self._revocations.revoke(revocation_id, reason=reason, at=now)
        self._audit.record(
            AuditEvent(
                kind=EventKind.TOKEN_REVOKED,
                timestamp=now,
                subject="-",
                actor="-",
                delegation_depth=0,
                action="token:revoke",
                resource=f"revocation:{revocation_id}",
                outcome="revoked",
                reason=reason,
            )
        )

    def _token_event(self, kind: EventKind, issued: IssuedToken, outcome: str) -> AuditEvent:
        return AuditEvent(
            kind=kind,
            timestamp=self._clock.now(),
            subject=issued.chain.subject,
            actor=issued.chain.actor,
            delegation_depth=issued.chain.depth,
            action=f"token:{outcome}",
            resource=issued.chain.actor,
            outcome=outcome,
            reason=f"expires_at={issued.expires_at.isoformat()}"
            + (f" mandate={issued.mandate_id}" if issued.mandate_id else ""),
            token_fingerprint=issued.fingerprint,
        )
