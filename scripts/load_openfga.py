"""Create (or reuse) the ACME store in OpenFGA, write the model and the fixture tuples.

    uv run python scripts/load_openfga.py [--url http://localhost:8080]

Writes .hoc-local.json with the store and model ids for local tooling.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "docs" / "authz-model.json"
TUPLES = ROOT / "fixtures" / "acme" / "generated" / "tuples.json"
STORE_NAME = "headofcontext-acme"


def ensure_store(client: httpx.Client, name: str) -> str:
    stores = client.get("/stores").json().get("stores", [])
    for store in stores:
        if store["name"] == name:
            return str(store["id"])
    return str(client.post("/stores", json={"name": name}).json()["id"])


def write_model(client: httpx.Client, store_id: str) -> str:
    model = json.loads(MODEL.read_text())
    response = client.post(f"/stores/{store_id}/authorization-models", json=model)
    response.raise_for_status()
    return str(response.json()["authorization_model_id"])


def write_tuples(
    client: httpx.Client, store_id: str, model_id: str, tuples: list[dict[str, str]]
) -> int:
    written = 0
    for start in range(0, len(tuples), 100):
        chunk = tuples[start : start + 100]
        response = client.post(
            f"/stores/{store_id}/write",
            json={"writes": {"tuple_keys": chunk}, "authorization_model_id": model_id},
        )
        if response.status_code == 400 and "already exists" in response.text:
            # Re-running the loader: write the chunk one tuple at a time, skipping duplicates.
            for tuple_key in chunk:
                single = client.post(
                    f"/stores/{store_id}/write",
                    json={
                        "writes": {"tuple_keys": [tuple_key]},
                        "authorization_model_id": model_id,
                    },
                )
                if single.status_code == 200:
                    written += 1
            continue
        response.raise_for_status()
        written += len(chunk)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--store-name", default=STORE_NAME)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        store_id = ensure_store(client, args.store_name)
        model_id = write_model(client, store_id)
        tuples = json.loads(TUPLES.read_text())
        written = write_tuples(client, store_id, model_id, tuples)
    (ROOT / ".hoc-local.json").write_text(
        json.dumps({"openfga_url": args.url, "store_id": store_id, "model_id": model_id}, indent=1)
    )
    print(f"store={store_id} model={model_id} tuples_written={written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
