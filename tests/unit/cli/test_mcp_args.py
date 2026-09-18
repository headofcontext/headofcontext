"""`hoc mcp proxy --upstream NAME=TARGET` parsing (ADR 0030)."""

from __future__ import annotations

import pytest

from headofcontext.cli.main import _upstreams
from headofcontext.core.errors import ConfigurationError


def test_targets_are_kept_verbatim_by_name() -> None:
    assert _upstreams(["finance=npx -y finance-mcp --flag", "crm=https://crm.example/mcp"]) == {
        "finance": "npx -y finance-mcp --flag",
        "crm": "https://crm.example/mcp",
    }


@pytest.mark.parametrize(
    "pair", ["finance", "=cmd", "finance=", "a__b=cmd", "Finance=cmd", "fin ance=cmd"]
)
def test_malformed_pairs_are_refused(pair: str) -> None:
    with pytest.raises(ConfigurationError):
        _upstreams([pair])


def test_duplicate_names_are_refused() -> None:
    with pytest.raises(ConfigurationError):
        _upstreams(["a=x", "a=y"])
