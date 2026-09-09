"""Files connector: a directory tree plus a JSON ACL manifest becomes tuples."""

import json
from pathlib import Path

import pytest

from headofcontext.read.connectors import FilesConnector

MANIFEST = {
    "folders": {
        "rh": {"source": "source:files-rh", "viewers": ["group:rh"], "editors": ["user:samir"]},
        "public": {
            "source": "source:files-public",
            "viewers": ["group:rh", "group:it"],
            "editors": [],
        },
        "restricted/rh": {"source": None},
    },
    "documents": {
        "rh/acme-0002.md": {"viewers": ["user:ines"]},
        "restricted/rh/acme-0003.md": {"editors": ["user:samir", "user:carol"]},
    },
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for rel in [
        "rh/acme-0001.md",
        "rh/acme-0002.md",
        "public/acme-0010.md",
        "restricted/rh/acme-0003.md",
        "rh/.hidden.md",
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n")
    (tmp_path / "hoc-acl.json").write_text(json.dumps(MANIFEST))
    return tmp_path


async def test_snapshot_tuples(tree: Path) -> None:
    snapshot = await FilesConnector(tree, name="files-acme").snapshot()
    t = snapshot.tuples
    assert ("group:rh#member", "viewer", "source:files-rh") in t
    assert ("user:samir", "editor", "source:files-rh") in t
    assert ("group:it#member", "viewer", "source:files-public") in t
    assert ("source:files-rh", "parent", "document:acme-0001") in t
    assert ("source:files-rh", "parent", "document:acme-0002") in t
    assert ("user:ines", "viewer", "document:acme-0002") in t
    assert ("source:files-public", "parent", "document:acme-0010") in t
    assert not any(o == "document:acme-0003" and r == "parent" for _, r, o in t)
    assert ("user:samir", "editor", "document:acme-0003") in t
    assert ("user:carol", "editor", "document:acme-0003") in t
    assert not any("hidden" in o for _, _, o in t)
    ids = {d.id for d in snapshot.documents}
    assert ids == {
        "document:acme-0001",
        "document:acme-0002",
        "document:acme-0010",
        "document:acme-0003",
    }
    assert next(d for d in snapshot.documents if d.id == "document:acme-0003").source is None


async def test_path_id_strategy(tree: Path) -> None:
    snapshot = await FilesConnector(tree, name="files-acme", id_strategy="path").snapshot()
    assert ("source:files-rh", "parent", "document:rh/acme-0001") in snapshot.tuples


async def test_invalid_refs_in_manifest_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "hoc-acl.json").write_text(
        json.dumps({"folders": {"x": {"source": "source:x", "viewers": ["everyone"]}}})
    )
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.md").write_text("a")
    with pytest.raises(ValueError, match="everyone"):
        await FilesConnector(tmp_path, name="f").snapshot()


async def test_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await FilesConnector(tmp_path, name="f").snapshot()
