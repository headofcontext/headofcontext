"""Service fixtures shared by tests/integration and tests/golden (OpenFGA, PostgreSQL, Keycloak).

Services are located through HOC_OPENFGA_URL, HOC_PG_DSN and HOC_KEYCLOAK_URL. When a service is
unreachable the tests are skipped, unless HOC_REQUIRE_SERVICES=1 turns that into a failure (CI).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import psycopg
import pytest

OPENFGA_URL = os.environ.get("HOC_OPENFGA_URL", "http://localhost:8080")
PG_DSN = os.environ.get("HOC_PG_DSN", "postgresql://hoc:hoc@localhost:5433/hoc")
KEYCLOAK_URL = os.environ.get("HOC_KEYCLOAK_URL", "http://localhost:8180")
REQUIRE_SERVICES = os.environ.get("HOC_REQUIRE_SERVICES") == "1"

ROOT = Path(__file__).resolve().parents[1]
MODEL_JSON = ROOT / "docs" / "authz-model.json"
GENERATED = ROOT / "fixtures" / "acme" / "generated"


def unavailable(reason: str) -> None:
    if REQUIRE_SERVICES:
        pytest.fail(reason)
    pytest.skip(reason)


def load_json(name: str) -> object:
    return json.loads((GENERATED / name).read_text(encoding="utf-8"))


class FgaStore:
    def __init__(self, url: str, store_id: str, model_id: str) -> None:
        self.url = url
        self.store_id = store_id
        self.model_id = model_id


@pytest.fixture(scope="session")
def openfga_store() -> Iterator[FgaStore]:
    """A throw-away store loaded with the ACME model and tuples, deleted afterwards."""
    try:
        with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
            client.get("/healthz").raise_for_status()
            store_id = client.post(
                "/stores", json={"name": f"hoc-test-{uuid.uuid4().hex[:8]}"}
            ).json()["id"]
            model = json.loads(MODEL_JSON.read_text())
            model_id = client.post(f"/stores/{store_id}/authorization-models", json=model).json()[
                "authorization_model_id"
            ]
            tuples = load_json("tuples.json")
            assert isinstance(tuples, list)
            for start in range(0, len(tuples), 100):
                client.post(
                    f"/stores/{store_id}/write",
                    json={
                        "writes": {"tuple_keys": tuples[start : start + 100]},
                        "authorization_model_id": model_id,
                    },
                ).raise_for_status()
    except (httpx.HTTPError, KeyError, OSError) as exc:
        unavailable(f"OpenFGA not reachable at {OPENFGA_URL}: {exc}")
        raise
    yield FgaStore(OPENFGA_URL, store_id, model_id)
    with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
        client.delete(f"/stores/{store_id}")


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """A throwaway database for this run, migrated, dropped at the end.

    Nothing accumulates between runs (journal rows, approvals, mandates), so absence checks and
    the O(n) chain verification stay deterministic; the developer's own database is untouched.
    """
    try:
        with psycopg.connect(PG_DSN, connect_timeout=3):
            pass
    except psycopg.Error as exc:
        unavailable(f"PostgreSQL not reachable at {PG_DSN}: {exc}")
    name = f"hoc_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    dsn = PG_DSN.rsplit("/", 1)[0] + f"/{name}"
    from headofcontext.db import migrate

    migrate(dsn)
    try:
        yield dsn
    finally:
        from headofcontext.db.pool import close_pools

        close_pools()
        with psycopg.connect(PG_DSN, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.fixture(scope="session")
def keycloak_url() -> str:
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(f"{KEYCLOAK_URL}/realms/acme/.well-known/openid-configuration")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        unavailable(f"Keycloak realm acme not reachable at {KEYCLOAK_URL}: {exc}")
    return KEYCLOAK_URL
