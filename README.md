# HeadOfContext

[![ci](https://github.com/headofcontext/headofcontext/actions/workflows/ci.yml/badge.svg)](https://github.com/headofcontext/headofcontext/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/headofcontext/headofcontext?sort=semver)](https://github.com/headofcontext/headofcontext/releases)
[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.12%2B-blue.svg)

**One permission model for humans and their agents.** Open source, self-hosted, Apache 2.0.

Five hundred people and forty agents, or one founder and a dozen: each agent reads, does,
delegates and remembers exactly what the human behind it may, and nothing more. Every decision
is journaled.

> An agent can only read, do and remember what the human it acts for is allowed to see and do,
> however many agents sit in between.

| Function | What it covers |
|---|---|
| **READ** | Filter documents, search results and context before they reach the model: only what the person may see |
| **ACT** | Authorize every tool call as it runs, under the identity of the human, with approvals from a person, bound to the exact call, single use |
| **DELEGATE** | Hand off to sub-agents without widening: one attenuated token per hop, scope can only shrink, revocation propagates downstream |
| **REMEMBER** | Memories that inherit their sources: reading one re-checks every document it derives from, for whoever asks, now |

## Who it is for

Your workforce is now people and agents, in whatever ratio. HeadOfContext does not care how many
of each: every agent, at every level of delegation, holds a subset of one human's rights.

- **An IT department** that must answer for every agent the business plugs in: one model for the
  directory, the documents, the tools and the agents, one journal for the auditors.
- **A small AI team** shipping agents faster than access reviews can follow: rights come from the
  directory that already exists, nothing to re-declare per agent.
- **A solo founder** running a business with agents: each one acts for you with the slice of your
  rights you chose, approvals on your phone, one call to revoke.

## How it works

1. **Verify.** Your identity provider authenticates the human. The agent is an identity of its
   own, a confidential OIDC client, and gets a root [biscuit](https://www.biscuitsec.org) token
   to act for that human only if your directory binds the two. Sub-agents get attenuated
   copies, never more.
2. **Decide.** Scope from the token, relationship from [OpenFGA](https://openfga.dev) (your
   ACLs, synced from your directory, your documents and your tools), then policies. Default
   deny, fail closed.
3. **Journal.** `ALLOW`, `DENY` or `REQUIRE_APPROVAL`, with the reason and the chain, never the
   content. Append-only, hash-chained, exported to OpenTelemetry.

Every call carries a **principal chain**: the human subject, the agent acting, and the ordered
delegations between agents. Decisions are made for the subject, never for the agent. Five
invariants are tested with Hypothesis and never weakened: monotonic delegation, immutable
subject, no orphan agent, propagated revocation, fail closed.

**Provenance-aware memory** is what nobody else ships: a memory keeps the list of documents it
derives from, in a ledger the memory tool cannot rewrite. When the human loses access to a
source, the memory goes dark for them, with nothing purged and nothing re-indexed.

HeadOfContext is **not** a policy engine, a RAG platform, a memory tool, an agent framework or an
LLM gateway: it plugs into the ones you already run. The non-goals and the free / paid boundary
are in [`AGENTS.md`](AGENTS.md) and [`docs/boundary.md`](docs/boundary.md); the core is free and
stays free.

## Proof, not promises

The repository ships with ACME, a fictional company: 50 people, 8 groups, 500 documents, 3
agents and 300 reference questions, replayed against a real OpenFGA in `tests/golden`.

| Claim | Where it is checked |
|---|---|
| 0 leaks on the 300 golden questions | `uv run pytest tests/golden`, 1800 checks, must be 100 % green |
| Decision latency under 20 ms at p95, engine on the same host | `scripts/load_test.py`, latency test (ADR 0015) |
| Under 60 s from revoking a right to the agent losing it | connector freshness and revocation tests, invariant I4 |
| One journal, append-only and hash-chained | `hoc journal verify`, `tests/adversarial` |

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/).
- OpenFGA, PostgreSQL 16 (pgvector optional) and an OIDC identity provider. The local stack
  ships them in `docker-compose.yml` (OpenFGA, Keycloak, PostgreSQL, Nextcloud, an OTel
  collector); Docker is only needed for that stack and for the service image.

## Install

```bash
git clone https://github.com/headofcontext/headofcontext.git && cd headofcontext
uv sync --extra langgraph --extra crewai --extra pydanticai --extra mcp   # extras: mem0, zep too
```

The service image is published on each release as `ghcr.io/headofcontext/headofcontext:vX.Y.Z`;
the Helm chart is in [`deploy/helm/headofcontext`](deploy/helm/headofcontext/README.md).

## Quickstart

```bash
docker compose up -d                                        # OpenFGA :8080, Keycloak :8180, PostgreSQL :5433
uv run hoc model load --url http://localhost:8080           # store + model; ids and dev values written to .env
uv run hoc fixtures load                                    # the fictional ACME company (50 users, 500 documents)
export HOC_OPENFGA_STORE_ID=$(grep HOC_OPENFGA_STORE_ID .env | cut -d= -f2)
docker compose --profile api up -d --build                  # API on :8000 (/docs), sync runner every 5 min
curl -s localhost:8000/v1/ready
uv run hoc journal tail -n 5                                # every decision, journaled
```

[`docs/quickstart.md`](docs/quickstart.md) continues from here: a first decision over HTTP, then
the provenance-memory demo (`scripts/demo_memory.py`). The
[technical article](docs/articles/provenance-memory.md) explains why agent memory needs
provenance.

`hoc` also runs connectors on demand (`hoc sync --once --config hoc.connectors.json`), manages
approvals (`hoc approvals list|approve|reject`) and standing mandates (`hoc mandates`),
generates the root key (`hoc keys generate`), issues a dev biscuit (`hoc token issue`) and
applies migrations (`hoc db migrate|status`). See ADR 0012.

Agents authenticate with OIDC client credentials; humans with their own token when they issue a
root biscuit or resolve an approval (ADR 0011). The HTTP contract is
[`docs/openapi.json`](docs/openapi.json); a Python client written against it is published
separately.

## Integrations

| Where | What |
|---|---|
| Agent frameworks | LangGraph and CrewAI (`guard_tools`), PydanticAI (`guard_toolset`), any MCP client or server (`hoc mcp proxy`, `hoc mcp serve`) |
| Sources | Files, Nextcloud, PipesHub; Keycloak groups. Connectors write the sources' own ACLs into OpenFGA |
| Memory | PostgreSQL (default), Mem0, Zep; any adapter through the `headofcontext.memory_adapters` entry point |
| Identity | Keycloak, any OIDC provider with client credentials (Entra ID client tokens recognised) |
| Approvals | Log, signed webhook, plugins under `headofcontext.approval_channels` |

## Filtering what the model reads

```python
from headofcontext.read import filter_items
from headofcontext.read.pipeshub import PipesHubIndex

hits = await PipesHubIndex(url, token=pat).search("salary grid", limit=20)  # or any index
result = await filter_items(
    chain, hits, engine=engine, audit=audit
)  # only what the subject may view
context = [h["content"] for h in result.kept]
```

Connectors keep OpenFGA in line with the sources' own ACLs (`read.connectors`: files, Nextcloud,
PipesHub; `identity.KeycloakGroupsConnector` for group membership). `reconcile()` diffs a
connector snapshot against the previous one; a connector that has not synced in time turns every
decision into a denial. See ADR 0010.

## Guarding an agent's tools

```python
from headofcontext.integrations import AgentSession
from headofcontext.integrations.langgraph import guard_tools  # or .crewai

session = AgentSession(token=biscuit, caller="agent:assistant", token_service=tokens, gate=gate)
tools = guard_tools([send_mail, export_hr], session)  # every call: verify token → gate → run
sub_session = session.delegate("agent:mailer", Scope.of(Capability(Kind.ACT, "tool:mail.*")))
```

Denied calls return a short message to the model (or raise with `on_deny="raise"`); calls that
need a human land in the approval store as single-use, argument-bound requests
(`ActionGate.resolve`, `ActionGate.redeem`). Who may approve is a tuple, not a setting: the
approver must hold `approver` on the tool in OpenFGA (`tool:hr.export#approver@group:direction#member`),
the listing is filtered to what the caller may approve, and consumption is an atomic
compare-and-set so concurrent redeems allow exactly once (ADR 0017). See ADR 0008 and 0009. PydanticAI users wrap a
toolset instead: `guard_toolset(toolset, session)` from `headofcontext.integrations.pydanticai`
(extra `pydanticai`).

Pending requests are pushed to approval channels (`HOC_APPROVAL_CHANNELS=log,webhook`): a log
line, a signed webhook (`HOC_APPROVAL_WEBHOOK_URL`, `HOC_APPROVAL_WEBHOOK_SECRET`), or any
plugin registered under the `headofcontext.approval_channels` entry point. A channel that fails
is audited and skipped; it never changes the decision. See ADR 0014.

## MCP

Two commands, both acting for one user (`HOC_MCP_TOKEN` is the biscuit, `HOC_MCP_AGENT` the
caller):

```
uv sync --extra mcp
hoc mcp proxy --upstream-stdio "npx -y @modelcontextprotocol/server-filesystem ." \
              --tool-map read_file=fs.read     # every upstream tool gated; denials are MCP errors
hoc mcp serve                                  # hoc_filter / hoc_gate / hoc_redeem / hoc_recall / hoc_remember / hoc_whoami
```

The proxy exposes the upstream's tools verbatim plus `hoc_redeem` for calls a human approved;
resources and prompts are not forwarded. See ADR 0013.

## Operating it

- **Telemetry**: with `HOC_OTEL_ENABLED=true` the service exports audit spans and the
  `hoc.decisions`, `hoc.decision.duration`, `hoc.engine.duration` and
  `hoc.approvals.notify_failed` metrics over OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`;
  attributes are outcomes and reasons only.
- **Root key**: `HOC_ROOT_KEY_HEX` is required (`hoc keys generate`); the service refuses to
  start without it unless `HOC_ALLOW_EPHEMERAL_ROOT_KEY=true`, which only the dev compose sets.
- **Solo operators**: `HOC_ALLOW_SELF_APPROVAL=true` lets the human approve their own agents'
  actions (default keeps four eyes). **Standing mandates** (ADR 0016) let an agent work while the
  human is away: `POST /v1/mandates` once with the human present, then the agent issues its own
  short tokens with `mandate_id`; `DELETE /v1/mandates/{id}` kills every token under it, while
  `POST /v1/tokens/revoke` only ever kills the token presented, never the mandate behind it.
  `HOC_MANDATE_MAX_DAYS` bounds the duration.
- **Rate limit**: `HOC_RATE_LIMIT_PER_MINUTE` (default 600) per caller and replica; `429` with
  `Retry-After`, health exempt. A never-seen bearer first draws from a per-address bucket, so
  spraying garbage tokens opens no fresh budget; behind a proxy run uvicorn with
  `--proxy-headers --forwarded-allow-ips`.
- **Root key rotation**: `HOC_ROOT_PUBLIC_KEYS="1=<hex>"` keeps old tokens verifiable while a
  new key signs; procedure in [docs/key-rotation.md](docs/key-rotation.md).
- **Load test**: `uv run python scripts/load_test.py --token <biscuit> --agent-token <jwt> -n 2000 -c 50`
  prints p50/p95/p99 against a running service. See ADR 0015.
- **Journal tooling**: `hoc journal verify [--anchor HASH | --archive FILE]` recomputes the
  hash chain from `GENESIS` (or from an archived head given explicitly), checks the reporting
  columns against the hashed payload, prints the head, and exits 1 on a break;
  `hoc journal export --since-seq --until-seq --out` writes a verifiable JSON-lines archive.
  Retention is export, verify the archive, record its head, then a DBA prunes the same `seq`
  range with the trigger disabled for that statement (ADR 0025).
- **Logging**: `HOC_LOG_LEVEL`, `HOC_LOG_FORMAT=text|json`; every line carries the request id,
  taken from `X-Request-ID` or generated and echoed back.
- **Image extras**: `docker build --build-arg HOC_EXTRAS="mcp mem0 zep"` bakes optional
  integrations into the image (default `mcp`).
- **Probes**: `GET /v1/health` is liveness; `GET /v1/ready` checks PostgreSQL and the schema
  version, the OpenFGA store and connector freshness, and answers `503` with one state per
  check (`ok`, `error`, `stale`, `behind`) otherwise; details are logged, not served. The chart
  and the image use it for readiness (ADR 0020).
- **Database**: one psycopg pool per process shared by every store (`HOC_DB_POOL_SIZE`, default
  8); store calls run in threads so the event loop keeps serving while PostgreSQL answers
  (ADR 0022).
- **Schema**: one migration ledger (`schema_migrations`). The service applies pending migrations
  at startup (`HOC_DB_AUTO_MIGRATE=true`, the default) or refuses to start when behind; the
  chart runs `hoc db migrate` as a pre-install/pre-upgrade Job and sets the flag to `false`, so
  replicas never need DDL rights. `hoc db status` prints applied and expected versions.
- **Memory backend**: `HOC_MEMORY_BACKEND=postgres|inmemory|mem0|zep|<plugin>`. `postgres`
  (default in compose and the chart) stores content next to the ledger with full-text search,
  and cosine search when `HOC_MEMORY_EMBEDDER=package.module:callable` names your embedding
  function and pgvector is present; it stores and searches, nothing more (ADR 0021). `mem0`
  (`HOC_MEM0_CONFIG`, JSON) and `zep` (`HOC_ZEP_API_KEY`, `HOC_ZEP_BASE_URL`) bring extraction and
  consolidation. `inmemory` is per process, warns at startup, and the chart refuses it with
  several replicas (ADR 0018).
- **Identity**: `HOC_IDENTITY_PROVIDER=keycloak|oidc|<plugin>`; agent tokens are recognised by
  `HOC_OIDC_AGENT_CLAIM` / `HOC_OIDC_AGENT_ID_CLAIM` (`clientId` for Keycloak, `idtyp=app` and
  `azp` for Entra ID), so a client-credentials token from any IdP becomes `agent:<client>`.
- **Plugins**: four entry point groups (`headofcontext.identity_providers`, `source_connectors`,
  `memory_adapters`, `approval_channels`), discovered at startup, built-ins never shadowed, a
  broken plugin fails the start. The contract is `tests/unit/plugins/` (ADR 0019).

## Development

```
uv sync --extra langgraph --extra crewai --extra pydanticai --extra mcp
docker compose up -d                   # OpenFGA :8080, Keycloak :8180, PostgreSQL :5433, OTel :4318, Nextcloud :8090
uv run pytest tests/unit tests/adversarial
HOC_REQUIRE_SERVICES=1 uv run pytest tests/integration   # real OpenFGA / PostgreSQL / Keycloak (skipped when down without the flag)
./scripts/nextcloud-dev-setup.sh && uv run python scripts/load_nextcloud.py   # optional: ACME into Nextcloud
uv run pytest tests/golden             # ACME golden set: engine level, files end to end, Nextcloud end to end
uv run ruff check . && uv run mypy --strict src
./scripts/fga-model.sh                 # regenerate docs/authz-model.json and run the OpenFGA model tests
uv run python scripts/export_openapi.py   # after any API change
```

The rules every contributor follows are in [`AGENTS.md`](AGENTS.md): ADR before code, tests
before implementation, adversarial test for every security function, real services in
integration tests. [`CONTRIBUTING.md`](CONTRIBUTING.md) is the short version.

## Versioning and releases

Pre-1.0. Merges to `main` are squashed and the pull request title is a Conventional Commit
line; [release-please](https://github.com/googleapis/release-please) turns them into a release
pull request, a `vX.Y.Z` tag, generated release notes and the container image (ADR 0028). The
HTTP contract is versioned separately (`/v1`) and changes to it are announced in the release
notes.

## Project

- Decisions: [`docs/adr/`](docs/adr/README.md). Threat model: [`docs/threat-model.md`](docs/threat-model.md).
  OpenFGA model: [`docs/authz-model.fga`](docs/authz-model.fga). Key rotation:
  [`docs/key-rotation.md`](docs/key-rotation.md).
- Security reports: [`SECURITY.md`](SECURITY.md), never the issue tracker.
- Fixtures: the fictional ACME company in `fixtures/acme/`, generated, never real data.
- License: [Apache 2.0](LICENSE).
