"""Materialize the ACME documents as a files tree with a ``hoc-acl.json`` manifest.

    uv run python fixtures/acme/materialize.py /tmp/acme-files

Used by the files connector end-to-end golden run and by the Nextcloud loader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

GENERATED = Path(__file__).parent / "generated"


def load(name: str) -> Any:
    return json.loads((GENERATED / name).read_text(encoding="utf-8"))


def folder_for(doc: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    """Relative folder of a document: its source folder, or ``restricted/<dept>`` without one."""
    source = sources.get(doc["source"])
    if source is None:
        return f"restricted/{doc['department']}"
    return source["path"].removeprefix("/ACME/")


def build_manifest(
    sources: list[dict[str, Any]], documents: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {s["id"]: s for s in sources}
    folders: dict[str, Any] = {}
    for source in sources:
        folders[source["path"].removeprefix("/ACME/")] = {
            "source": source["id"],
            "viewers": source["viewers"],
            "editors": source["editors"],
        }
    docs: dict[str, Any] = {}
    for doc in documents:
        folder = folder_for(doc, by_id)
        if folder.startswith("restricted/"):
            folders.setdefault(folder, {"source": None})
        if doc["viewers"] or doc["editors"]:
            docs[f"{folder}/{doc['id'].split(':', 1)[1]}.md"] = {
                "viewers": doc["viewers"],
                "editors": doc["editors"],
            }
    return {"folders": folders, "documents": docs}


def write_files_tree(dest: Path) -> Path:
    sources = load("sources.json")
    documents = load("documents.json")
    by_id = {s["id"]: s for s in sources}
    dest.mkdir(parents=True, exist_ok=True)
    for doc in documents:
        folder = dest / folder_for(doc, by_id)
        folder.mkdir(parents=True, exist_ok=True)
        stem = doc["id"].split(":", 1)[1]
        (folder / f"{stem}.md").write_text(f"# {doc['title']}\n\n{doc['body']}\n", encoding="utf-8")
    (dest / "hoc-acl.json").write_text(
        json.dumps(build_manifest(sources, documents), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return dest


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/acme-files")
    print(write_files_tree(target))
