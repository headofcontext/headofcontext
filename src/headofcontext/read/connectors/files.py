"""Files connector: a directory tree and a ``hoc-acl.json`` manifest (ADR 0010).

Manifest shape::

    {
      "folders": {
        "rh": {"source": "source:files-rh", "viewers": ["group:rh"], "editors": ["user:x"]},
        "restricted/rh": {"source": null}
      },
      "documents": {
        "rh/acme-0002.md": {"viewers": ["user:ines"], "editors": []}
      }
    }

A file belongs to the nearest ancestor folder listed in ``folders``; ``"source": null`` means
the folder's files have no parent source and are reachable only through direct entries.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from headofcontext.core import Tuple
from headofcontext.core.errors import InvariantViolation
from headofcontext.core.refs import validate_ref
from headofcontext.read.connectors.base import DocumentMeta, Snapshot, principal

MANIFEST_NAME = "hoc-acl.json"
IdStrategy = Literal["stem", "path"]


class FilesConnector:
    def __init__(
        self,
        root: Path,
        *,
        name: str,
        id_strategy: IdStrategy = "stem",
        manifest_name: str = MANIFEST_NAME,
    ) -> None:
        self._root = Path(root)
        self._name = name
        self._id_strategy = id_strategy
        self._manifest_name = manifest_name

    @property
    def name(self) -> str:
        return self._name

    async def snapshot(self) -> Snapshot:
        manifest = self._load_manifest()
        folders: dict[str, dict[str, Any]] = manifest.get("folders", {})
        documents: dict[str, dict[str, Any]] = manifest.get("documents", {})
        tuples: set[Tuple] = set()
        metas: list[DocumentMeta] = []

        for spec in folders.values():
            folder_source = spec.get("source")
            if folder_source is None:
                continue
            validate_ref(str(folder_source), "source")
            for ref in spec.get("viewers", []):
                tuples.add((_principal(ref), "viewer", str(folder_source)))
            for ref in spec.get("editors", []):
                tuples.add((_principal(ref), "editor", str(folder_source)))

        for path in sorted(p for p in self._root.rglob("*") if p.is_file()):
            rel = path.relative_to(self._root).as_posix()
            if path.name == self._manifest_name or any(part.startswith(".") for part in path.parts):
                continue
            folder = _nearest_folder(rel, folders)
            source: str | None = None
            if folder is not None and folders[folder].get("source") is not None:
                source = str(folders[folder]["source"])
            doc_id = self._document_id(path, rel)
            if source is not None:
                tuples.add((source, "parent", doc_id))
            for ref in documents.get(rel, {}).get("viewers", []):
                tuples.add((_principal(ref), "viewer", doc_id))
            for ref in documents.get(rel, {}).get("editors", []):
                tuples.add((_principal(ref), "editor", doc_id))
            metas.append(DocumentMeta(id=doc_id, title=path.stem, path=rel, source=source))

        return Snapshot(
            connector=self._name,
            tuples=frozenset(tuples),
            documents=tuple(metas),
            taken_at=datetime.now(UTC),
        )

    def _load_manifest(self) -> dict[str, Any]:
        path = self._root / self._manifest_name
        if not path.is_file():
            raise FileNotFoundError(f"missing {self._manifest_name} in {self._root}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest must be a JSON object")
        return data

    def _document_id(self, path: Path, rel: str) -> str:
        if self._id_strategy == "path":
            candidate = f"document:{rel.rsplit('.', 1)[0]}"
        else:
            candidate = f"document:{path.stem}"
        return validate_ref(candidate, "document")


def _principal(ref: object) -> str:
    try:
        return principal(str(ref))
    except InvariantViolation as exc:
        raise ValueError(f"invalid principal in manifest: {ref!r}") from exc


def _nearest_folder(rel: str, folders: dict[str, dict[str, Any]]) -> str | None:
    parts = rel.split("/")[:-1]
    while parts:
        candidate = "/".join(parts)
        if candidate in folders:
            return candidate
        parts.pop()
    return None
