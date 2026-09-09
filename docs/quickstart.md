# Quickstart

You need Docker, `uv` and Python 3.12+. Everything below runs locally with the fictional ACME
company (50 users, 8 groups, 500 documents). Part A gets you a first authorization decision over
HTTP in five minutes; part B is the provenance-memory demo.

## A. From zero to an HTTP decision (5 min)

```bash
uv sync
docker compose up -d                                        # OpenFGA :8080, Keycloak :8180, PostgreSQL :5433
uv run hoc model load --url http://localhost:8080           # store + model; .env gets the ids and the dev values
uv run hoc fixtures load                                    # ACME tuples
docker compose --profile api up -d --build                  # API on :8000, migrations applied at start
curl -s localhost:8000/v1/ready                              # {"status":"ready","checks":{...}}
```

An agent authenticates with its own client credentials; a human is represented by their token.
Issue a root biscuit for the HR employee `alice.martin` acting through the `assistant` agent,
then ask for a decision:

```bash
BISCUIT=$(uv run hoc token issue --issuer http://localhost:8180/realms/acme --username alice.martin --password password --agent assistant)
AGENT_JWT=$(curl -s -d grant_type=client_credentials -d client_id=assistant \
  -d client_secret=assistant-dev-secret \
  localhost:8180/realms/acme/protocol/openid-connect/token | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/v1/actions/gate -H "Authorization: Bearer $AGENT_JWT" \
  -H 'content-type: application/json' \
  -d "{\"token\": \"$BISCUIT\", \"tool\": \"tool:mail.send\", \"args\": {\"to\": \"bob@acme.example\"}}"
# → {"decision": {"outcome": "ALLOW", ...}}
curl -s localhost:8000/v1/actions/gate -H "Authorization: Bearer $AGENT_JWT" \
  -H 'content-type: application/json' \
  -d "{\"token\": \"$BISCUIT\", \"tool\": \"tool:hr.export\", \"args\": {\"year\": 2026}}"
# → REQUIRE_APPROVAL: a human holding `approver` on the tool resolves it (POST /v1/approvals/{id}/resolve)
uv run hoc journal tail -n 5                                 # every decision, journaled
```

Every route is in `docs/openapi.json` (also served at `/docs`); a Python client written against it
is published separately.

## B. Provenance-aware memory in 10 minutes

### 1. Start the engine (if you skipped part A)

```bash
uv sync
docker compose up -d openfga          # the demo only needs the authorization engine
```

### 2. Run the demo (1 min)

```bash
uv run python scripts/demo_memory.py
```

You will see, in order:

1. the assistant agent, acting for an HR employee, reads an internal HR document and stores a
   memory derived from it;
2. the HR employee asks the agent about the topic and gets the memory back;
3. a store employee asks the same agent the same question and gets **nothing**;
4. the HR employee is removed from the HR group; their own memory goes dark on the next question;
5. the audit trail: one line per decision, with hashes, never content.

### 3. Use it in your own agent (5 min)

```python
from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.memory import InMemoryLedger, InMemoryMemoryAdapter, MemoryService

engine = OpenFgaEngine.connect("http://localhost:8080", STORE_ID, AlwaysFresh())
audit = InMemoryAuditSink()  # PostgresAuditSink in production
service = MemoryService(
    engine=engine,
    tuples=engine,
    ledger=InMemoryLedger(),  # PostgresLedger in production
    adapter=InMemoryMemoryAdapter(),  # Mem0Adapter / ZepAdapter in production
    decider=Decider(engine, audit),
    audit=audit,
)

# Who are we acting for, and what may this agent do?
chain = PrincipalChain.root(
    subject="user:alice",
    actor="agent:assistant",
    scope=Scope.of(
        Capability(Kind.READ, "document:*"),
        Capability(Kind.READ, "memory:*"),
        Capability(Kind.REMEMBER, "document:*"),
    ),
)

# The agent learned something from two documents alice can read today.
memory = await service.remember(chain, "Summary…", derived_from=["document:hr-1", "document:hr-2"])

# Later, for anyone: only returned if that subject can still view BOTH documents.
hits = await service.recall(chain, "salary grid")
```

`PrincipalChain` values in production come from a verified biscuit token (`TokenService.verify`),
never from the request body. See `docs/adr/0003-biscuit-delegation-tokens.md`.

### 4. Swap the memory backend

```bash
uv sync --extra mem0     # or --extra zep
```

```python
from headofcontext.memory.mem0 import Mem0Adapter

adapter = Mem0Adapter.from_config({...})  # stores verbatim (infer=False), tagged with the memory id
```

In the service the default is `HOC_MEMORY_BACKEND=postgres`: durable, shared between replicas,
full-text search, optional embeddings through `HOC_MEMORY_EMBEDDER` (ADR 0021). `mem0`
(`HOC_MEM0_CONFIG`, a JSON object) and `zep` (`HOC_ZEP_API_KEY`) add extraction and
consolidation (ADR 0018). `inmemory` is per process: fine for this demo, not for two replicas.

The backend only ever sees content. Provenance stays in HeadOfContext's ledger and in OpenFGA.

## What to read next

- `docs/adr/0007-provenance-memory.md` — the design and its trust model.
- `docs/threat-model.md` — T4 and T5, and the adversarial tests that pin them.
- `tests/adversarial/test_memory_leaks.py` — the attacks that must fail.
