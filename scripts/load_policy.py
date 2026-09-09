"""Write the ACME organization policy tuples (hierarchy, tools, agent bindings) into a store.

uv run python scripts/load_policy.py --store-id <id> [--url http://localhost:8080]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

POLICY = (
    Path(__file__).resolve().parents[1] / "fixtures" / "acme" / "generated" / "policy-tuples.json"
)


def load_policy(client: httpx.Client, store_id: str) -> int:
    tuples = json.loads(POLICY.read_text())
    written = 0
    for start in range(0, len(tuples), 100):
        chunk = tuples[start : start + 100]
        response = client.post(f"/stores/{store_id}/write", json={"writes": {"tuple_keys": chunk}})
        if response.status_code == 400 and "already exists" in response.text:
            for t in chunk:
                if (
                    client.post(
                        f"/stores/{store_id}/write", json={"writes": {"tuple_keys": [t]}}
                    ).status_code
                    == 200
                ):
                    written += 1
            continue
        response.raise_for_status()
        written += len(chunk)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--store-id", required=True)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        print(f"policy tuples written: {load_policy(client, args.store_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
