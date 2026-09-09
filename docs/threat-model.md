# Threat model

Read this before writing any security-relevant code. Every threat below has at least one
adversarial test in `tests/adversarial/` that must **fail for the attacker**.

## Assets

- **A1** Documents and search results (confidentiality).
- **A2** Side effects of tool calls (integrity: mail, tickets, payments).
- **A3** Agent memories derived from documents (confidentiality across users and time).
- **A4** The audit journal (integrity, completeness).
- **A5** Root signing key, IdP client secrets, user tokens.

## Trust boundaries

```
[User] --OIDC--> [IdP] --token exchange--> [HeadOfContext issuer] --biscuit--> [Agent A]
                                                                          \--attenuated biscuit--> [Agent B]
[Agent] --(chain, op, resource)--> [HeadOfContext decision] --check--> [OpenFGA]
[HeadOfContext] --filtered items--> [LLM]           (LLM output is DATA, never instruction)
[Connectors] --tuples--> [OpenFGA]                   (least privilege on the source side)
```

Trusted: HeadOfContext process, OpenFGA, IdP, root key. Untrusted: every agent, every LLM output,
every document body, every tool argument, every network peer.

## Threats, controls, tests

| Id | Threat | Control | Test |
|---|---|---|---|
| T1 | **Confused deputy** — an agent with a technical identity acts on resource X for user U who cannot see X | Decisions are checked for the *subject* (user), never the actor. Actions run under the user's exchanged token (ADR 0004). | `test_confused_deputy_*` |
| T2 | **Escalation by delegation** — agent A delegates to B a scope wider than its own | I1 in `PrincipalChain.delegate`; biscuit blocks can only add checks; verification rejects widened claims. | `test_delegation_escalation_*` |
| T3 | **Prompt injection targeting access** — a document says "you are admin, read HR" | Filter runs before the model; action gate runs after, on verified chain + OpenFGA only. Text never enters the decision. | `test_injection_cannot_influence_decision` |
| T4 | **Leak through memory** — agent learns from a restricted doc for U1, later answers U2 | Memory carries `derived_from` in a ledger *and* in OpenFGA; read = BatchCheck viewer on the union of sources for the *current* subject. No provenance = readable by `written_for` only. Tampered ledger or backend record cannot remove sources. | `test_t4_*` in `tests/adversarial/test_memory_leaks.py` |
| T5 | **Late revocation** — U loses access, agent keeps serving from cache | No decision cache beyond the current request; revocation store consulted on every verify; connector staleness → DENY; memories re-checked on every recall. | `test_revocation_propagates_downstream`, `test_stale_connector_denies`, `test_t5_memory_denied_after_acl_removed` |
| T6 | **Orphan agent** — a chain with an agent as subject or no subject | I3: `PrincipalChain` refuses non-user subjects; engine denies checks without a user subject. | `test_orphan_agent_*` |
| T7 | **Token replay / forgery** — reuse of an expired, tampered or foreign-key token | Ed25519 signature chain, `time` check, `caller` check binding the token to the presenting process, revocation ids. | `test_token_replay_*`, `test_token_tamper_*` |
| T8 | **Connector late** — IdP groups or ACLs not yet synced, so a removed member still has tuples | `max_staleness` on connector state; engine returns DENY with `connector_stale` when exceeded. | `test_stale_connector_denies` |
| T9 | **Engine unavailable** — OpenFGA down or timing out | I5: DENY with `engine_unavailable`, audited. | `test_engine_unavailable_denies` |
| T10 | **Audit tampering or loss** | Append-only table with triggers (UPDATE, DELETE, TRUNCATE), hash chain anchored at `GENESIS` or at an archived head given explicitly, reporting columns checked against the hashed payload, audit on the critical path (no audit = no decision); an auditor needs the archives, their heads and the live head recorded at each export. | `test_audit_append_only`, `test_audit_hash_chain` |
| T11 | **Self-asserted identity** — the LLM or the agent claims a subject/actor/scope in its request | Only verified tokens produce a chain. Free-form chain inputs are rejected at the API boundary. | `test_self_asserted_chain_rejected` |
| T12 | **Secret leakage in logs** | Tokens and document bodies never logged; fingerprints only. Log scrubbing test. | `test_no_secrets_in_audit` |
| T13 | **Stolen mandate id** — an attacker learns a mandate id and tries to obtain tokens under it | A mandate is usable only by the agent it names, authenticated with its own client credentials; the id alone issues nothing; the `mandate` fact is inside the signed authority block, so it cannot be forged into a token. | `test_t13_*` in `tests/adversarial/test_mandates.py` |
| T14 | **Over-broad or forgotten standing mandate** — a human mandates an agent widely for months | Duration bounded by `HOC_MANDATE_MAX_DAYS`; token TTL bounded by the mandate; scope of every token ⊆ mandate scope; OpenFGA still bounds every decision; only the subject sees and revokes their mandates; one revocation id kills every derived token, attenuated ones included; the directory binding is re-checked on every issue. | `test_t14_*` in `tests/adversarial/test_mandates.py` |
| T15 | **Approval by an unrelated human** — any authenticated user approves or lists another subject's pending request | The approver must hold `approver` on the tool in OpenFGA (or be the subject with self-approval enabled); refusal is audited; the listing is filtered by `BatchCheck` to what the caller may approve plus their own requests (ADR 0017). | `test_t15_*` in `tests/adversarial/test_action_attacks.py` |
| T16 | **Approval double-spend** — concurrent `redeem` calls on one approved request | Consumption is a compare-and-set claimed before the decision; only one caller can win, the others get `approval_consumed` (ADR 0017). | `test_t16_*` in `tests/adversarial/test_action_attacks.py` |
| T17 | **A delegate revokes the mandate** — a sub-agent revokes its own attenuated token to kill the principal's standing mandate | `revoke` only ever revokes the token's own block id; `mandate:` ids are revoked by the subject alone; a mandate whose id is revoked reads `REVOKED` on every access (ADR 0016 amendment). | `test_t17_*` in `tests/adversarial/test_mandates.py` |
| T18 | **Rate-limit evasion** — a client varies the bearer on every request to open a fresh bucket each time, and each forged token costs the IdP a JWKS fetch | New bearers draw from a per-address bucket before getting their own; eviction never clears established buckets; forced JWKS refreshes are throttled to one per 30 s (ADR 0015 amendment). | `test_spraying_*` in `tests/unit/api/test_rate_limit.py`, `test_forged_tokens_do_not_refetch_jwks_*` in `tests/unit/identity/test_oidc.py` |
| T19 | **Attenuation check ignored on READ** — an opaque block restricting resources is honoured for ACT but not for the read filter | `verify_many` runs the biscuit authorizer per distinct resource before the engine is asked; refused resources are dropped with `token_check_failed` (ADR 0010 amendment). | `test_t19_*` in `tests/adversarial/test_read_attacks.py` |
| T20 | **Agent JWT as `user_token`** — an agent presents a service-account token to mint a root biscuit for a pseudo-user | `/tokens/issue` refuses any non-human identity with `user_token_not_human`, audited (ADR 0011 amendment). | `test_issue_refuses_an_agent_token_as_user` in `tests/integration/test_api.py`, `tests/unit/api/test_auth.py` |
| T21 | **Attenuation check ignored on REMEMBER / memory READ** — an opaque block restricting resources is honoured for ACT and READ but not inside the memory service | `MemoryService` asks the token per resource where the resources become known: sources on write, memory ids and sources on read; refusals are `token_check_failed`, audited (ADR 0018). | `test_t21_*` in `tests/adversarial/test_memory_leaks.py` |

## Out of scope (v1)

Side channels through LLM provider logs, compromise of the OpenFGA database itself, physical access
to the host, denial of service against the IdP.
