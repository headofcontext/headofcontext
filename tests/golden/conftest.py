from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from headofcontext.core import Capability, Kind, PrincipalChain, Scope

GENERATED = Path(__file__).resolve().parents[2] / "fixtures" / "acme" / "generated"


@dataclass(frozen=True)
class GoldenCase:
    id: str
    subject: str
    actor: str
    question: str
    expected_visible: tuple[str, ...]
    expected_invisible: tuple[str, ...]

    def chain(self) -> PrincipalChain:
        return PrincipalChain.root(
            self.subject, self.actor, Scope.of(Capability(Kind.READ, "document:*"))
        )


def load_golden() -> list[GoldenCase]:
    raw = json.loads((GENERATED / "golden.json").read_text(encoding="utf-8"))
    return [
        GoldenCase(
            id=q["id"],
            subject=q["subject"],
            actor=q["actor"],
            question=q["question"],
            expected_visible=tuple(q["expected_visible"]),
            expected_invisible=tuple(q["expected_invisible"]),
        )
        for q in raw
    ]


GOLDEN = load_golden()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "case" in metafunc.fixturenames:
        metafunc.parametrize("case", GOLDEN, ids=[c.id for c in GOLDEN])
