"""The model `hoc model load` ships must be the one the tests and the docs use."""

import json
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_model_matches_docs() -> None:
    packaged = resources.files("headofcontext.engines.openfga").joinpath("authz-model.json")
    assert json.loads(packaged.read_text()) == json.loads(
        (ROOT / "docs" / "authz-model.json").read_text()
    ), "run ./scripts/fga-model.sh"
