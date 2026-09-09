"""PipesHub as the index: semantic search hits become items for ``filter_items``.

``POST /api/v1/search`` (SDK 1.x) returns ``searchResponse.searchResults[]`` whose
``metadata.recordId`` identifies the record. Items carry ``id = document:ph-<recordId>`` so the
filter can ask OpenFGA about them; the connector in ``read.connectors.pipeshub`` uses the same
reference scheme.
"""

from __future__ import annotations

from typing import Any

import httpx

from headofcontext.core.errors import Unavailable
from headofcontext.core.refs import validate_ref


class PipesHubIndexUnavailable(Unavailable):
    reason = "index_unavailable"


class PipesHubIndex:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._token = token
        self._transport = transport
        self._timeout = timeout_seconds

    async def search(
        self, query: str, *, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if filters:
            body["filters"] = filters
        async with httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._token}"},
            transport=self._transport,
            timeout=self._timeout,
        ) as http:
            try:
                response = await http.post("/search", json=body)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise PipesHubIndexUnavailable("pipeshub search failed") from exc
        results = (
            ((data.get("searchResponse") or {}).get("searchResults"))
            if isinstance(data, dict)
            else None
        )
        items: list[dict[str, Any]] = []
        for hit in results or []:
            if not isinstance(hit, dict):
                continue
            metadata = hit.get("metadata") or {}
            record_id = metadata.get("recordId") if isinstance(metadata, dict) else None
            if not isinstance(record_id, str) or not record_id:
                continue  # a chunk without a record cannot be authorized: dropped here already
            items.append(
                {
                    "id": validate_ref(f"document:ph-{record_id}", "document"),
                    "record_id": record_id,
                    "name": metadata.get("recordName"),
                    "score": metadata.get("score"),
                    "content": hit.get("content"),
                }
            )
        return items
