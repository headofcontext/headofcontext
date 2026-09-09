"""Layer boundaries (ADR 0023), enforced on every module's imports.

core depends on nothing; db on core; nothing outside the HTTP layer depends on it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "headofcontext"

ALLOWED: dict[str, set[str]] = {
    "core": {"core"},
    "db": {"core", "db"},
}
FORBIDDEN_EVERYWHERE_BUT_API = "api"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("headofcontext")
        ):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("headofcontext"):
                    found.add(alias.name)
    return found


def _package(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def test_layers_only_depend_downwards() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        package = rel.parts[0] if len(rel.parts) > 1 else ""
        for imported in _imports(path):
            target = _package(imported)
            if package in ALLOWED and target and target not in ALLOWED[package]:
                violations.append(f"{rel}: {package} imports {imported}")
            if package != FORBIDDEN_EVERYWHERE_BUT_API and target == FORBIDDEN_EVERYWHERE_BUT_API:
                violations.append(f"{rel}: {package or 'root'} imports the HTTP layer ({imported})")
    assert violations == [], "\n".join(violations)
