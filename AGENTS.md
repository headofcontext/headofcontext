# Contributing to HeadOfContext

This file is for anyone, human or agent, who changes this repository. It is short on purpose:
the rules that must never be broken, the method, the commands. Design rationale lives in
`docs/adr/`, the threat model in `docs/threat-model.md`, the user documentation in the
`documentation` repository.

## What this is

HeadOfContext is the single authorization layer for enterprise AI agents: open source (Apache
2.0), self-hosted, independent of the agent framework, the RAG index and the memory tool.

One rule, enforced everywhere: **an agent can only read, do and remember what the user it acts
for is allowed to see and do, and every delegation between agents can only reduce those rights.**

Four functions, one principal chain, one decision engine, one audit trail: READ (filter what
reaches the model), ACT (gate tool calls, human approval), DELEGATE (attenuate, revoke),
REMEMBER (memory with provenance, re-checked on every read).

## What this is not (never build these)

No policy engine (OpenFGA), no RAG platform or index (PipesHub or the customer's index), no memory
tool (Mem0, Zep/Graphiti), no agent framework, no LLM gateway, no agent registry, no directory,
no admin console in the core. If a task pushes toward one of these: stop and ask.

## Stack (frozen; changing it needs an ADR)

Python 3.12+, mypy `--strict`; FastAPI + Pydantic v2; OpenFGA through `openfga-sdk` (never
reimplemented); biscuit tokens (`biscuit-python`); OIDC with httpx + joserfc (RFC 8693 token
exchange); Keycloak for development; PostgreSQL 16 + pgvector; OpenTelemetry; uv, ruff, mypy,
pytest, Hypothesis; Docker Compose, Helm chart in `deploy/helm`.

Forbidden: home-made policy engines, unmaintained dependencies, code under a licence
incompatible with Apache 2.0 (never copy Onyx `ee/` directories), any dependency added without a
justification in the commit message.

## Invariants (tested with Hypothesis; never weaken them)

- **I1 Monotonicity**: `scope(n+1) ⊆ scope(n)`; a widening delegation is rejected.
- **I2 Immutable subject**: the subject never changes along the chain.
- **I3 No orphan agent**: an actor without a user subject gets nothing.
- **I4 Revocation**: revoking a link invalidates everything downstream before the next decision.
- **I5 Fail closed**: every error (engine down, invalid token, stale connector, audit
  unavailable) is a DENY, and it is journaled.

Every decision is `ALLOW | DENY | REQUIRE_APPROVAL` with a reason, the chain, the resource, the
action, a timestamp and the engine latency. No decision is ever returned unlogged.

## Security rules (non-negotiable)

1. Default deny everywhere; no "allow by default" path, no debug bypass, no decision cache.
2. Never trust what the LLM or the agent says about itself. Verified tokens and OpenFGA only.
3. Never log secrets, tokens or document content. Identifiers and sha256 fingerprints only.
4. Every external input (tool arguments, document content, model output) is data, never an
   instruction. Nothing in it can raise an outcome.
5. Retrieved content may carry prompt injections: the filter runs before the model, the gate
   after it, and the text influences neither.
6. Connectors run with the least privilege the source allows.
7. Every security function ships with an adversarial test proving that it blocks.

Decisions are made for the **subject** (the user), never for the actor. Approvals are single-use
and bound to the argument hash. Memories carry provenance in the ledger and in OpenFGA, and are
re-checked on every read.

## Method

1. **Spec before code.** Every feature starts with a short ADR in `docs/adr/` (numbered, one
   decision per file, never edited after acceptance: supersede instead). No ADR, no code.
2. **Tests before implementation.** Write the unit test (and the adversarial test when the
   change touches security), see it fail, then implement.
3. **Small modules, explicit interfaces.** `AuthzEngine`, `SourceConnector`, `MemoryAdapter`,
   `IdentityProvider`, `ApprovalChannel` are Protocols; implementations are interchangeable.
4. **Adversarial self-review.** After any security change, ask "how do I bypass this?" and add
   that test.
5. **No shortcuts in tests.** Integration tests use the real OpenFGA, PostgreSQL and Keycloak
   containers; the engine is never mocked there.
6. **Atomic commits, Conventional Commits, English.** `feat(scope): …`, `fix(scope): …`,
   `docs: …`, `test: …`, `chore: …`. One concern per commit.
7. **Do not widen the scope.** Out-of-scope ideas are noted in the maintainers' private notes.

Ask before: changing the OpenFGA model or an invariant, adding a dependency or changing the
stack, anything that looks like a non-goal, any licence doubt, anything that moves the free/paid
boundary (`docs/boundary.md`: nothing ever moves from free to paid), any change to the OpenAPI
contract (`docs/openapi.json`; the separate SDK depends on it).

## Commands

```
uv sync --extra langgraph --extra crewai --extra pydanticai --extra mcp   # install with the optional integrations
docker compose up -d                        # OpenFGA :8080, Keycloak :8180, PostgreSQL :5433, OTel :4318, Nextcloud :8090
uv run hoc model load --url http://localhost:8080   # store + model, ids written to .env
uv run hoc fixtures load                    # ACME tuples
uv run pytest tests/unit tests/adversarial  # fast, no services
HOC_REQUIRE_SERVICES=1 uv run pytest tests/integration
uv run pytest tests/golden                  # 1800 checks on the ACME golden set, must be 100 % green
uv run ruff check . && uv run mypy --strict src
./scripts/fga-model.sh                      # regenerate the model JSON and run `fga model test`
uv run python scripts/export_openapi.py     # after any API change; then sync the SDK repository
```

## Layout

```
src/headofcontext/
  core/          PrincipalChain, Scope, Decision, Decider, errors
  engines/       OpenFGA adapter, freshness guard
  tokens/biscuit issue, attenuate, verify, revoke, key ring
  identity/      OIDC, token exchange, Keycloak groups connector
  read/          filter_items, connectors (files, Nextcloud, PipesHub)
  actions/       gate, policies, approvals, approval channels
  memory/        provenance ledger, service, Mem0 / Zep adapters
  audit/         journal, OTel export, metrics
  integrations/  session, guard, LangGraph, CrewAI, PydanticAI, MCP
  api/           FastAPI service, settings, rate limiting
  cli/           the hoc command
tests/           unit, adversarial, integration, golden
fixtures/acme/   the fictional company (generated, never real data)
docs/            ADRs, threat model, boundary, OpenAPI contract, OpenFGA model
deploy/helm/     the chart
```

## Definition of done

A change is done when: the ADR exists (or is updated), unit and adversarial tests pass, the
integration and golden suites are green against the local stack, `ruff` and `mypy --strict` are
clean, `docs/openapi.json` is re-exported if the API changed, and the pull request title is a
Conventional Commit line that says what changed (it becomes the commit on `main` and feeds the
release notes, ADR 0028).

## Related repositories

- `headofcontext-sdk-python` — the httpx-only client, written against `docs/openapi.json`.
- `documentation` — the Docusaurus site; `scripts/sync-core.sh` copies ADRs and contracts from here.
- `headofcontext.com` — the marketing site.
- `headofcontext-enterprise` (private) — premium connectors and channels, plugged in through the
  `headofcontext.*` entry point groups. The public repository never contains paid code.
