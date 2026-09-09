"""Nextcloud connector: WebDAV listing + OCS share API of a service account (ADR 0010).

The service account owns the document tree (least privilege: it is not an admin). Every
first-level folder under ``root`` is a source; deeper folders inherit it. Shares on a source
folder become ``source#viewer/editor``; shares on files become ``document#viewer/editor``.
Group shares map to ``group:<gid>#member``, user shares to ``user:<uid>``; link, mail and
federated shares are ignored (they carry no principal HeadOfContext can vouch for).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import unquote

import httpx

from headofcontext.core import Tuple
from headofcontext.core.errors import InvariantViolation, Unavailable
from headofcontext.core.refs import validate_ref
from headofcontext.read.connectors.base import DocumentMeta, Snapshot

log = logging.getLogger(__name__)

SHARE_USER, SHARE_GROUP = 0, 1
PERMISSION_UPDATE = 2
IdStrategy = Literal["stem", "backend"]
_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns"}


class NextcloudUnavailable(Unavailable):
    reason = "connector_unavailable"


class NextcloudConnector:
    def __init__(
        self,
        base_url: str,
        *,
        user: str,
        password: str,
        root: str = "/",
        name: str = "nextcloud",
        id_strategy: IdStrategy = "stem",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._root = "/" + root.strip("/")
        self._name = name
        self._id_strategy = id_strategy
        self._transport = transport
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    async def snapshot(self) -> Snapshot:
        async with httpx.AsyncClient(
            base_url=self._base,
            auth=(self._user, self._password),
            transport=self._transport,
            timeout=self._timeout,
            headers={"OCS-APIRequest": "true"},
        ) as http:
            entries = await self._list_files(http)
            shares = await self._list_shares(http)

        tuples: set[Tuple] = set()
        documents: list[DocumentMeta] = []
        doc_by_path: dict[str, str] = {}
        source_by_path: dict[str, str] = {}

        for path, is_dir, file_id in entries:
            rel = path.removeprefix(self._root).strip("/")
            if not rel:
                continue
            top = rel.split("/", 1)[0]
            source_id = self._source_id(top)
            if is_dir:
                if "/" not in rel:
                    source_by_path[path] = source_id
                continue
            doc_id = self._document_id(rel, file_id)
            if doc_id is None:
                continue
            tuples.add((source_id, "parent", doc_id))
            doc_by_path[path] = doc_id
            documents.append(
                DocumentMeta(id=doc_id, title=rel.rsplit("/", 1)[-1], path=rel, source=source_id)
            )

        for share in shares:
            principal = _principal(share)
            if principal is None:
                continue
            path = str(share.get("path", ""))
            relation = (
                "editor" if int(share.get("permissions", 0)) & PERMISSION_UPDATE else "viewer"
            )
            if path in source_by_path:
                tuples.add((principal, relation, source_by_path[path]))
            elif path in doc_by_path:
                tuples.add((principal, relation, doc_by_path[path]))
            # Shares on deeper folders or outside the root are not modelled: log, never guess.
            elif path.startswith(self._root + "/"):
                log.info("ignoring share on unmodelled path under root")

        return Snapshot(
            connector=self._name,
            tuples=frozenset(tuples),
            documents=tuple(documents),
            taken_at=datetime.now(UTC),
        )

    # -- HTTP --------------------------------------------------------------------------------

    async def _list_files(self, http: httpx.AsyncClient) -> list[tuple[str, bool, str]]:
        body = (
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            "<d:prop><oc:fileid/><d:resourcetype/></d:prop></d:propfind>"
        )
        url = f"/remote.php/dav/files/{self._user}{self._root}"
        try:
            response = await http.request(
                "PROPFIND",
                url,
                content=body,
                headers={"Depth": "infinity", "Content-Type": "application/xml"},
            )
            if response.status_code != 207:
                raise NextcloudUnavailable(f"nextcloud PROPFIND returned {response.status_code}")
            tree = ET.fromstring(response.content)  # noqa: S314 — see module docstring
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise NextcloudUnavailable("nextcloud listing failed") from exc
        prefix = f"/remote.php/dav/files/{self._user}"
        entries: list[tuple[str, bool, str]] = []
        for item in tree.findall("d:response", _NS):
            href = unquote(item.findtext("d:href", default="", namespaces=_NS))
            path = href.removeprefix(prefix).rstrip("/") or "/"
            is_dir = item.find("d:propstat/d:prop/d:resourcetype/d:collection", _NS) is not None
            file_id = item.findtext("d:propstat/d:prop/oc:fileid", default="", namespaces=_NS)
            entries.append((path, is_dir, file_id))
        return entries

    async def _list_shares(self, http: httpx.AsyncClient) -> list[dict[str, Any]]:
        try:
            response = await http.get(
                "/ocs/v2.php/apps/files_sharing/api/v1/shares", params={"format": "json"}
            )
            response.raise_for_status()
            data = response.json()["ocs"]["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise NextcloudUnavailable("nextcloud share listing failed") from exc
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    # -- mapping -----------------------------------------------------------------------------

    def _source_id(self, top_folder: str) -> str:
        return validate_ref(f"source:{self._name}-{top_folder}", "source")

    def _document_id(self, rel: str, file_id: str) -> str | None:
        if self._id_strategy == "backend":
            candidate = f"document:nc-{file_id}"
        else:
            candidate = "document:" + rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        try:
            return validate_ref(candidate, "document")
        except InvariantViolation:
            log.warning("dropping a file whose name cannot be a document reference")
            return None


def _principal(share: dict[str, Any]) -> str | None:
    share_type = int(share.get("share_type", -1))
    target = share.get("share_with")
    if not isinstance(target, str) or not target:
        return None
    try:
        if share_type == SHARE_GROUP:
            return validate_ref(f"group:{target}", "group") + "#member"
        if share_type == SHARE_USER:
            return validate_ref(f"user:{target}", "user")
    except InvariantViolation:
        log.warning("dropping a share whose principal cannot be a reference")
    return None
