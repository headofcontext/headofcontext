"""Datalog fragments for authority and attenuation blocks, and the signed delegation trail.

Facts we write and read back:

- authority: ``subject``, ``actor``, ``issued_at``, ``expires_at``, ``cap_exact(kind, res)``,
  ``cap_prefix(kind, prefix)``;
- attenuation: ``delegation(from, to)`` and ``scope_cap(kind, res)`` as a signed trail, plus the
  checks that actually restrict what the holder may ask.

Facts in attenuation blocks are never trusted by the authorizer; they are parsed from the block
source only to rebuild the PrincipalChain for audit and holder binding. Everything a caller may
*do* is bounded by the authority facts and by the checks.
"""

from __future__ import annotations

import re
from datetime import datetime

import biscuit_auth

from headofcontext.core import Capability, Kind, Scope
from headofcontext.core.errors import TokenInvalid

AUTHORITY_SOURCE = """
subject({subject});
actor({actor});
issued_at({issued_at});
expires_at({expires_at});
check if time($t), $t < {expires_at};
"""

AUTHORIZER_POLICIES = """
allow if operation($op), resource($r), cap_exact($op, $r);
allow if operation($op), resource($r), cap_prefix($op, $p), $r.starts_with($p);
deny if true;
"""

_DELEGATION_RE = re.compile(r'^delegation\("([^"\\]+)", "([^"\\]+)"\);$')
_SCOPE_CAP_RE = re.compile(r'^scope_cap\("([^"\\]+)", "([^"\\]+)"\);$')


def authority_builder(
    subject: str,
    actor: str,
    scope: Scope,
    issued_at: datetime,
    expires_at: datetime,
    *,
    mandate_id: str | None = None,
) -> biscuit_auth.BiscuitBuilder:
    builder = biscuit_auth.BiscuitBuilder(
        AUTHORITY_SOURCE,
        {"subject": subject, "actor": actor, "issued_at": issued_at, "expires_at": expires_at},
    )
    for cap in sorted(scope):
        builder.add_fact(_cap_fact(cap))
    if mandate_id is not None:
        # Signed with the authority: a revoked mandate kills this token and every attenuation.
        builder.add_fact(biscuit_auth.Fact("mandate({id})", {"id": mandate_id}))
    return builder


def attenuation_builder(from_actor: str, to_actor: str, scope: Scope) -> biscuit_auth.BlockBuilder:
    block = biscuit_auth.BlockBuilder(
        "delegation({from_actor}, {to_actor});", {"from_actor": from_actor, "to_actor": to_actor}
    )
    for cap in sorted(scope):
        block.add_fact(
            biscuit_auth.Fact(
                "scope_cap({kind}, {resource})", {"kind": str(cap.kind), "resource": cap.resource}
            )
        )
    block.add_check(_scope_check(scope))
    return block


def _cap_fact(cap: Capability) -> biscuit_auth.Fact:
    if cap.is_pattern:
        return biscuit_auth.Fact(
            "cap_prefix({kind}, {prefix})", {"kind": str(cap.kind), "prefix": cap.resource[:-1]}
        )
    return biscuit_auth.Fact(
        "cap_exact({kind}, {resource})", {"kind": str(cap.kind), "resource": cap.resource}
    )


def _scope_check(scope: Scope) -> biscuit_auth.Check:
    """One check whose alternatives are the capabilities; an empty scope can never satisfy it."""
    if len(scope) == 0:
        return biscuit_auth.Check("check if false")
    clauses: list[str] = []
    params: dict[str, str] = {}
    for i, cap in enumerate(sorted(scope)):
        params[f"k{i}"] = str(cap.kind)
        if cap.is_pattern:
            params[f"r{i}"] = cap.resource[:-1]
            clauses.append(
                f"operation($op), resource($r), $op == {{k{i}}}, $r.starts_with({{r{i}}})"
            )
        else:
            params[f"r{i}"] = cap.resource
            clauses.append(f"operation($op), resource($r), $op == {{k{i}}}, $r == {{r{i}}}")
    return biscuit_auth.Check("check if " + " or ".join(clauses), params)


def parse_trail_block(source: str) -> tuple[tuple[str, str], Scope] | None:
    """Return ``((from, to), declared_scope)`` for a delegation block, ``None`` for an opaque one.

    An opaque block (no ``delegation`` fact) can only add checks, so it never changes the chain.
    """
    delegation: tuple[str, str] | None = None
    caps: list[Capability] = []
    for raw in source.splitlines():
        line = raw.strip()
        if m := _DELEGATION_RE.match(line):
            if delegation is not None:
                raise TokenInvalid("malformed delegation block: several delegation facts")
            delegation = (m.group(1), m.group(2))
        elif m := _SCOPE_CAP_RE.match(line):
            caps.append(_capability(m.group(1), m.group(2)))
    if delegation is None:
        return None
    return delegation, Scope.from_iterable(caps)


def read_mandate(authorizer: biscuit_auth.Authorizer) -> str | None:
    facts = authorizer.query(biscuit_auth.Rule("out($m) <- mandate($m)"))
    if not facts:
        return None
    if len(facts) != 1:
        raise TokenInvalid("authority block must carry at most one mandate fact")
    return str(facts[0].terms[0])


def read_authority(authorizer: biscuit_auth.Authorizer) -> tuple[str, str, Scope, datetime]:
    subject = _single(authorizer, "subject")
    actor = _single(authorizer, "actor")
    expires_raw = _single(authorizer, "expires_at")
    caps: list[Capability] = []
    for fact in authorizer.query(biscuit_auth.Rule("out($k, $r) <- cap_exact($k, $r)")):
        caps.append(_capability(str(fact.terms[0]), str(fact.terms[1])))
    for fact in authorizer.query(biscuit_auth.Rule("out($k, $p) <- cap_prefix($k, $p)")):
        caps.append(_capability(str(fact.terms[0]), str(fact.terms[1]) + "*"))
    if not isinstance(expires_raw, datetime):
        raise TokenInvalid("authority block has no valid expires_at")
    return str(subject), str(actor), Scope.from_iterable(caps), expires_raw


def _single(authorizer: biscuit_auth.Authorizer, name: str) -> object:
    facts = authorizer.query(biscuit_auth.Rule(f"out($x) <- {name}($x)"))
    if len(facts) != 1:
        raise TokenInvalid(f"authority block must carry exactly one {name} fact")
    return facts[0].terms[0]


def _capability(kind: str, resource: str) -> Capability:
    try:
        return Capability(Kind(kind), resource)
    except ValueError as exc:
        raise TokenInvalid(f"unknown capability kind {kind!r}") from exc
