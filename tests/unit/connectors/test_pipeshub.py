"""PipesHub connector and index adapter against a recorded API shape (SDK 1.x)."""

import httpx
import pytest

from headofcontext.read.connectors import PipesHubConnector
from headofcontext.read.pipeshub import PipesHubIndex

BASE = "http://ph.test"

NODES_PAGE_1 = {
    "items": [
        {"id": "r-1", "name": "Grille salaires.md", "nodeType": "record", "recordType": "FILE"},
        {"id": "f-1", "name": "RH", "nodeType": "folder"},
        {"id": "r-2", "name": "Planning Lille.md", "nodeType": "record", "recordType": "FILE"},
    ],
    "pagination": {"page": 1, "limit": 100, "totalItems": 3, "totalPages": 1},
}
RECORDS = {
    "r-1": {
        "record": {"id": "r-1", "recordName": "Grille salaires.md"},
        "knowledgeBase": {"id": "kb-rh", "name": "RH"},
        "folder": None,
        "metadata": {},
        "permissions": [
            {
                "id": "u-1",
                "name": "samir.vincent@acme.example",
                "type": "USER",
                "relationship": "OWNER",
                "access_type": "direct",
            },
            {
                "id": "g-1",
                "name": "rh",
                "type": "GROUP",
                "relationship": "READER",
                "access_type": "direct",
            },
            {
                "id": "u-2",
                "name": "ines.leroy@acme.example",
                "type": "USER",
                "relationship": "WRITER",
                "access_type": "direct",
            },
            {
                "id": "x-1",
                "name": "everyone",
                "type": "ORG",
                "relationship": "READER",
                "access_type": "direct",
            },
        ],
    },
    "r-2": {
        "record": {"id": "r-2", "recordName": "Planning Lille.md"},
        "knowledgeBase": {"id": "kb-lille", "name": "Lille"},
        "folder": None,
        "metadata": {},
        "permissions": [
            {
                "id": "g-2",
                "name": "magasin-lille",
                "type": "GROUP",
                "relationship": "READER",
                "access_type": "direct",
            }
        ],
    },
}
SEARCH = {
    "searchId": "s-1",
    "searchResponse": {
        "searchResults": [
            {
                "content": "La grille prévoit +3 %",
                "citationType": "vectordb|document",
                "metadata": {"recordId": "r-1", "recordName": "Grille salaires.md", "score": 0.91},
            },
            {
                "content": "Ouverture 9h",
                "metadata": {"recordId": "r-2", "recordName": "Planning Lille.md", "score": 0.4},
            },
            {"content": "orphan chunk", "metadata": {"recordId": None}},
        ],
        "records": [],
        "status": "success",
        "statusCode": 200,
        "message": "ok",
    },
}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("Authorization") == "Bearer pat-123"
    path = request.url.path
    if path == "/api/v1/knowledgeBase/knowledge-hub/nodes":
        page = int(request.url.params.get("page", 1))
        assert request.url.params.get("nodeTypes") == "record"
        return httpx.Response(
            200, json=NODES_PAGE_1 if page == 1 else {"items": [], "pagination": {}}
        )
    if path.startswith("/api/v1/knowledgeBase/record/"):
        record_id = path.rsplit("/", 1)[-1]
        if record_id in RECORDS:
            return httpx.Response(200, json=RECORDS[record_id])
        return httpx.Response(404, json={"error": "not found"})
    if path == "/api/v1/search" and request.method == "POST":
        body = request.read().decode()
        assert '"query"' in body and '"limit"' in body
        return httpx.Response(200, json=SEARCH)
    return httpx.Response(404)


async def test_connector_snapshot() -> None:
    connector = PipesHubConnector(BASE, token="pat-123", transport=httpx.MockTransport(handler))
    snapshot = await connector.snapshot()
    t = snapshot.tuples
    assert ("user:samir.vincent", "editor", "document:ph-r-1") in t  # OWNER → editor
    assert ("user:ines.leroy", "editor", "document:ph-r-1") in t  # WRITER → editor
    assert ("group:rh#member", "viewer", "document:ph-r-1") in t
    assert ("group:magasin-lille#member", "viewer", "document:ph-r-2") in t
    assert ("source:pipeshub-kb-rh", "parent", "document:ph-r-1") in t
    assert not any(
        "everyone" in u for u, _, _ in t
    )  # ORG-wide entries are not principals we can vouch for
    assert {d.id for d in snapshot.documents} == {"document:ph-r-1", "document:ph-r-2"}


async def test_connector_custom_principal_mapping() -> None:
    def principal(entry: dict[str, object]) -> str | None:
        if entry.get("type") == "USER":
            return "user:" + str(entry["id"])
        return None

    connector = PipesHubConnector(
        BASE, token="pat-123", principal_of=principal, transport=httpx.MockTransport(handler)
    )
    snapshot = await connector.snapshot()
    assert ("user:u-1", "editor", "document:ph-r-1") in snapshot.tuples
    assert not any(u.startswith("group:") for u, _, _ in snapshot.tuples)


async def test_connector_failure_raises() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    connector = PipesHubConnector(BASE, token="pat-123", transport=httpx.MockTransport(down))
    with pytest.raises(Exception, match="pipeshub"):
        await connector.snapshot()


async def test_index_search_yields_filterable_items() -> None:
    index = PipesHubIndex(BASE, token="pat-123", transport=httpx.MockTransport(handler))
    hits = await index.search("grille", limit=5)
    assert [h["id"] for h in hits] == ["document:ph-r-1", "document:ph-r-2"]
    assert hits[0]["content"].startswith("La grille") and hits[0]["score"] == pytest.approx(0.91)
    assert hits[0]["record_id"] == "r-1"
