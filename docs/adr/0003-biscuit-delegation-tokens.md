# ADR 0003 — Biscuit tokens for delegation, attenuation and revocation

Status: Accepted — 2026-09-07

## Context

A `PrincipalChain` (ADR 0002) is a claim. Something must make it *unforgeable* when it crosses a
process boundary between agents, and must make attenuation impossible to undo. Biscuit tokens
provide exactly this: an authority block signed by HeadOfContext's root key, plus attenuation blocks
that can only add restrictions, each block carrying a unique revocation id.

## Decision

### Root key

HeadOfContext owns an Ed25519 keypair. Only the issuer holds the private key. Verifiers only need
the public key (offline verification). Key rotation uses the `root_key_id` field; a `KeyRing`
resolves it.

### Authority block (issued by HeadOfContext after OIDC login + token exchange)

```
subject("user:alice");
actor("agent:assistant");
cap("read", "document:*");
cap("act", "tool:mail.send");
issued_at(<ts>);
expires_at(<expiry>);
check if time($t), $t < <expiry>;
```

Capabilities are stored as `cap_exact(kind, resource)` or `cap_prefix(kind, prefix)` facts.

### Attenuation block (delegation from agent A to agent B)

```
delegation("agent:assistant", "agent:mailer");
scope_cap("act", "tool:mail.*");
check if operation($op), resource($r), $op == "act", $r.starts_with("tool:mail.");
```

Facts in attenuation blocks are *not* visible to the authorizer (biscuit trust rules), which is what
we want: a sub-agent cannot assert new rights. Checks are what restrict. The `delegation` fact is
kept as a signed, human-readable trail and is parsed from the block source only to reconstruct the
audit chain, never to make a decision.

The Python `Scope` of the delegated token is computed and validated in `PrincipalChain.delegate`
(I1) *before* the block is built, then encoded as checks. Both layers must agree; the token layer is
the one an adversary cannot bypass.

### Verification

`verify(token_b64, caller, operation, resource) -> VerifiedToken`:

1. parse, resolve the root key id, verify the signature chain, bound the depth;
2. rebuild the `PrincipalChain` from the authority facts and the signed `delegation` /
   `scope_cap` trail of each block, re-running `PrincipalChain.delegate` (I1) on every hop;
3. reject if any block's revocation id is revoked (I4);
4. **holder binding**: `caller` — the verified identity of the process presenting the token
   (mTLS SAN or OIDC client id, never something the LLM says) — must equal the last actor of the
   signed trail;
5. run the authorizer with facts `caller`, `operation`, `resource`, `time` and the policies
   `allow if cap_exact($op, $r)` / `allow if cap_prefix($op, $p), $r.starts_with($p)` /
   `deny if true`, so every attenuation check must also pass.

Any failure raises `TokenInvalid` or `TokenRevoked`, which the caller maps to DENY (I5).

Holder binding is checked in Python from the signed trail rather than by a per-block
`check if caller(...)`, because a per-block caller check cannot survive a second hop (block 1 would
pin the caller to agent B while block 2 pins it to agent C). Consequence, accepted for v1: whoever
holds a token can append a delegation block, so a stolen token can be *narrowed* and used under an
identity the thief can authenticate as, never widened. Mitigations: short TTL, revocation, and
third-party blocks signed by the delegating agent's key, planned for v2 (see `docs/ideas.md`).

### Revocation (I4)

Every block has a revocation id. `RevocationStore.revoke(revocation_id)` marks it. Verification
rejects a token if *any* of its blocks' revocation ids is revoked, which by construction invalidates
every token derived downstream. The store is in-memory for tests and Postgres-backed in deployment;
propagation target is under 60 s.

## Consequences

- Delegation depth is bounded (`MAX_DELEGATION_DEPTH = 8`) to keep verification cheap and to
  frustrate chain-stuffing.
- No third-party blocks in v1. If an external issuer needs to attenuate, a new ADR.
