"""PipesHub connector: record permissions → tuples (ADR 0010).

Enumerates records through the knowledge-hub nodes API, then reads each record's permission
entries (``GET /knowledgeBase/record/{recordId}``, SDK 1.x). Mapping:

- ``relationship`` ``OWNER`` / ``WRITER`` → ``editor``, ``READER`` → ``viewer``;
- ``type`` ``USER`` → ``user:<local part of name>``, ``GROUP`` → ``group:<name>#member``;
  anything else (organization-wide entries, links) is not a principal we can vouch for and is
  ignored. ``principal_of`` overrides the mapping when the deployment's identifiers differ.
- the record's knowledge base becomes the parent source ``source:pipeshub-<kbId>``.

Validated against the SDK's recorded response shapes; a running PipesHub is not part of CI.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from headofcontext.core import Tuple
from headofcontext.core.errors import InvariantViolation, Unavailable
from headofcontext.core.refs import validate_ref
from headofcontext.read.connectors.base import DocumentMeta, Snapshot

log = logging.getLogger(__name__)

PrincipalOf = Callable[[dict[str, Any]], str | None]
_EDITOR_RELATIONSHIPS = {"OWNER", "WRITER"}
_VIEWER_RELATIONSHIPS = {"READER"}


class PipesHubUnavailable(Unavailable):
    reason = "connector_unavailable"


def default_principal_of(entry: dict[str, Any]) -> str | None:
    kind = str(entry.get("type", "")).upper()
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None
    if kind == "USER":
        return f"user:{name.split('@', 1)[0]}"
    if kind == "GROUP":
        return f"group:{name}"
    return None


class PipesHubConnector:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        name: str = "pipeshub",
        principal_of: PrincipalOf | None = None,
        page_size: int = 100,
        concurrency: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._token = token
        self._name = name
        self._principal_of = principal_of or default_principal_of
        self._page_size = page_size
        self._concurrency = concurrency
        self._transport = transport
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    async def snapshot(self) -> Snapshot:
        tuples: set[Tuple] = set()
        documents: list[DocumentMeta] = []
        async with httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self._token}"},
            transport=self._transport,
            timeout=self._timeout,
        ) as http:
            record_ids = await self._record_ids(http)
            semaphore = asyncio.Semaphore(self._concurrency)

            async def fetch(record_id: str) -> dict[str, Any] | None:
                async with semaphore:
                    return await self._record(http, record_id)

            details = await asyncio.gather(*(fetch(r) for r in record_ids))
        for record_id, detail in zip(record_ids, details, strict=True):
            if detail is None:
                continue
            doc_id = validate_ref(f"document:ph-{record_id}", "document")
            kb = detail.get("knowledgeBase") or detail.get("knowledge_base") or {}
            source = None
            if isinstance(kb, dict) and isinstance(kb.get("id"), str):
                source = validate_ref(f"source:{self._name}-{kb['id']}", "source")
                tuples.add((source, "parent", doc_id))
            for entry in detail.get("permissions") or []:
                if not isinstance(entry, dict):
                    continue
                relation = _relation(str(entry.get("relationship", "")))
                principal = self._principal(entry)
                if relation is None or principal is None:
                    continue
                tuples.add((principal, relation, doc_id))
            record = detail.get("record") or {}
            title = str(record.get("recordName") or record.get("name") or record_id)
            documents.append(DocumentMeta(id=doc_id, title=title, path=record_id, source=source))
        return Snapshot(
            connector=self._name,
            tuples=frozenset(tuples),
            documents=tuple(documents),
            taken_at=datetime.now(UTC),
        )

    async def _record_ids(self, http: httpx.AsyncClient) -> list[str]:
        ids: list[str] = []
        page = 1
        while True:
            try:
                response = await http.get(
                    "/knowledgeBase/knowledge-hub/nodes",
                    params={"nodeTypes": "record", "page": page, "limit": self._page_size},
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise PipesHubUnavailable("pipeshub node listing failed") from exc
            items = data.get("items") if isinstance(data, dict) else None
            if not items:
                break
            for item in items:
                if isinstance(item, dict) and item.get("nodeType") == "record":
                    ids.append(str(item["id"]))
            if len(items) < self._page_size:
                break
            page += 1
        return ids

    async def _record(self, http: httpx.AsyncClient, record_id: str) -> dict[str, Any] | None:
        try:
            response = await http.get(f"/knowledgeBase/record/{record_id}")
            if response.status_code == 404:
                log.info("pipeshub record vanished between listing and read")
                return None
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PipesHubUnavailable("pipeshub record read failed") from exc
        return data if isinstance(data, dict) else None

    def _principal(self, entry: dict[str, Any]) -> str | None:
        try:
            ref = self._principal_of(entry)
        except Exception:
            return None
        if ref is None:
            return None
        try:
            if ref.startswith("group:"):
                return validate_ref(ref, "group") + "#member"
            return validate_ref(ref, "user")
        except InvariantViolation:
            log.warning("dropping a pipeshub permission entry whose principal is not a reference")
            return None


def _relation(relationship: str) -> str | None:
    upper = relationship.upper()
    if upper in _EDITOR_RELATIONSHIPS:
        return "editor"
    if upper in _VIEWER_RELATIONSHIPS:
        return "viewer"
    return None
