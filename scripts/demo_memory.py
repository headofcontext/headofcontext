"""Phase 1 demo: provenance-aware memory, end to end against a real OpenFGA.

    docker compose up -d openfga
    uv run python scripts/demo_memory.py

Story: the assistant agent reads an HR document while acting for an HR employee and remembers a
summary. A store employee asks the same agent the same question: nothing comes out. Then the HR
employee leaves HR: even their own memory goes dark.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from headofcontext.engines.freshness import AlwaysFresh
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.memory import InMemoryLedger, InMemoryMemoryAdapter, MemoryService

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "fixtures" / "acme" / "generated"
OPENFGA_URL = os.environ.get("HOC_OPENFGA_URL", "http://localhost:8080")
SCOPE = Scope.of(
    Capability(Kind.READ, "document:*"),
    Capability(Kind.READ, "memory:*"),
    Capability(Kind.REMEMBER, "document:*"),
)


def say(step: str, detail: str = "") -> None:
    print(f"\n▶ {step}")
    if detail:
        print(f"  {detail}")


def load_store(client: httpx.Client) -> tuple[str, str]:
    store_id = client.post("/stores", json={"name": f"hoc-demo-{uuid.uuid4().hex[:6]}"}).json()[
        "id"
    ]
    model = json.loads((ROOT / "docs" / "authz-model.json").read_text())
    model_id = client.post(f"/stores/{store_id}/authorization-models", json=model).json()[
        "authorization_model_id"
    ]
    tuples = json.loads((GENERATED / "tuples.json").read_text())
    for start in range(0, len(tuples), 100):
        client.post(
            f"/stores/{store_id}/write",
            json={
                "writes": {"tuple_keys": tuples[start : start + 100]},
                "authorization_model_id": model_id,
            },
        ).raise_for_status()
    return store_id, model_id


async def run(store_id: str, model_id: str) -> None:
    users = json.loads((GENERATED / "users.json").read_text())
    docs = json.loads((GENERATED / "documents.json").read_text())
    hr = next(u for u in users if u["department"] == "rh" and not u["intern"])
    store = next(u for u in users if u["department"] == "magasin-lille" and not u["intern"])
    doc = next(d for d in docs if d["department"] == "rh" and d["confidentiality"] == "internal")

    engine = OpenFgaEngine.connect(
        OPENFGA_URL, store_id, AlwaysFresh(), authorization_model_id=model_id
    )
    audit = InMemoryAuditSink()
    service = MemoryService(
        engine=engine,
        tuples=engine,
        ledger=InMemoryLedger(),
        adapter=InMemoryMemoryAdapter(),
        decider=Decider(engine, audit),
        audit=audit,
    )
    hr_chain = PrincipalChain.root(hr["id"], "agent:assistant", SCOPE)
    store_chain = PrincipalChain.root(store["id"], "agent:assistant", SCOPE)
    question = doc["topic"]

    say(f"Agent acts for {hr['id']} (HR) and reads '{doc['title']}' ({doc['id']})")
    memory = await service.remember(
        hr_chain, f"Résumé pour {hr['first_name']}: {doc['body'][:80]}…", derived_from=[doc["id"]]
    )
    say("Memory written", f"{memory.memory_id} derived_from={memory.derived_from}")

    say(f"{hr['id']} asks: '{question}'")
    for m in await service.recall(hr_chain, question):
        print(f"  ✓ recalled {m.memory_id}: {m.content[:60]}…")

    say(f"{store['id']} (store employee) asks the same agent: '{question}'")
    hits = await service.recall(store_chain, question)
    print("  ✗ nothing comes out" if not hits else f"  LEAK: {hits}")

    say(f"{hr['id']} leaves the HR group (tuple removed in OpenFGA)")
    await engine.delete_tuples([(hr["id"], "member", "group:rh")])
    hits = await service.recall(hr_chain, question)
    print("  ✗ their own memory is now dark" if not hits else f"  LEAK: {hits}")

    say("Audit trail (no content, only hashes)")
    for e in audit.events:
        print(f"  {e.kind:15} {e.subject:28} {e.outcome:6} {e.reason}")
    await engine.close()


def main() -> int:
    with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
        try:
            client.get("/healthz").raise_for_status()
        except httpx.HTTPError:
            print(
                f"OpenFGA not reachable at {OPENFGA_URL}; run `docker compose up -d openfga`",
                file=sys.stderr,
            )
            return 1
        store_id, model_id = load_store(client)
        try:
            asyncio.run(run(store_id, model_id))
        finally:
            client.delete(f"/stores/{store_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
