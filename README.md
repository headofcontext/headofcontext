# HeadOfContext

**The single authorization layer for enterprise AI agents.** Open source, self-hosted,
independent of the agent framework, the RAG index and the memory tool.

One rule, enforced everywhere:

> An agent can only read, do and remember what the user it acts for is allowed to see and do,
> and every delegation between agents can only reduce those rights.

| Function | What it covers |
|---|---|
| **READ** | Filter documents, search results and context before they reach the model |
| **ACT** | Authorize writes and tool calls at execution time, under the user's identity, with optional human approval |
| **DELEGATE** | Agent → sub-agent delegation with strict attenuation and propagated revocation |
| **REMEMBER** | Provenance-aware agent memory: a memory inherits the rights of the documents it derives from, re-checked on every read |

Status: phases 0 to 5 delivered (core, provenance memory, actions and delegation, connectors,
HTTP service with Docker, Helm and SDK, CLI, MCP, PydanticAI, standing mandates, hardening);
premium connectors are developed separately. Decisions in `docs/adr/`, contributor rules in `AGENTS.md`.

Start with **[the quickstart](docs/quickstart.md)** (an HTTP decision in five minutes, then the
memory demo) or the
[technical article](docs/articles/provenance-memory.md) on why agent memory needs provenance.

## Running the service

```bash
uv run hoc model load --url http://localhost:8080          # store + model, ids and dev values recorded in .env
uv run hoc fixtures load                                    # ACME tuples (dev)
docker compose --profile api up -d --build                  # API on :8000 (/docs), sync runner every 5 min
uv run hoc journal tail -f                                  # watch decisions
```

`hoc` also runs connectors on demand (`hoc sync --once --config hoc.connectors.json`), manages
approvals (`hoc approvals list|approve|reject`), generates the root key (`hoc keys generate`) and
issues a dev biscuit (`hoc token issue`). See ADR 0012.

Agents authenticate with OIDC client credentials (Keycloak); humans with their own token when
they issue a root biscuit or resolve an approval (ADR 0011). The contract is `docs/openapi.json`;
the Python client lives in the separate `headofcontext-sdk-python` repository and depends on
`httpx` only. A Helm chart is under `deploy/helm/headofcontext`.

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
uv sync                                # add --extra langgraph / --extra crewai / --extra pydanticai / --extra mcp / --extra mem0 / --extra zep as needed
docker compose up -d                   # OpenFGA :8080, Keycloak :8180, PostgreSQL :5433, OTel :4318, Nextcloud :8090
uv run pytest tests/unit tests/adversarial
uv run pytest tests/integration        # real OpenFGA / PostgreSQL / Keycloak (skipped when down, HOC_REQUIRE_SERVICES=1 to fail)
./scripts/nextcloud-dev-setup.sh && uv run python scripts/load_nextcloud.py   # optional: ACME into Nextcloud
uv run pytest tests/golden             # ACME golden set: engine level, files end to end, Nextcloud end to end
uv run ruff check . && uv run mypy --strict src
./scripts/fga-model.sh                 # regenerate docs/authz-model.json and run the OpenFGA model tests
```

## Layout

See `AGENTS.md`. Decisions live in `docs/adr/`, the threat model in `docs/threat-model.md`,
the OpenFGA model in `docs/authz-model.fga`, the fictional ACME company in `fixtures/acme/`.

License: Apache 2.0.
