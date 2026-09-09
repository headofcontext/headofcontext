"""Golden set end to end: identities from Keycloak, document ACLs from a source connector,
both reconciled into an EMPTY OpenFGA store, then read.filter_items over each question's
candidates. Zero leaks, nothing missing, for all 300 questions, with the files connector and
with the Nextcloud connector."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.identity import KeycloakGroupsConnector
from headofcontext.read import filter_items
from headofcontext.read.connectors import (
    FilesConnector,
    InMemoryConnectorStateStore,
    NextcloudConnector,
    SourceConnector,
    reconcile,
)
from tests.golden.conftest import GoldenCase
from tests.integration.test_connectors import NEXTCLOUD_URL
from tests.services import GENERATED, MODEL_JSON, OPENFGA_URL, FgaStore, unavailable

pytestmark = [pytest.mark.golden, pytest.mark.integration]


def _fresh() -> InMemoryConnectorStateStore:
    return InMemoryConnectorStateStore(max_staleness=timedelta(hours=1))


def _new_store(label: str) -> FgaStore:
    try:
        with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
            client.get("/healthz").raise_for_status()
            store_id = client.post(
                "/stores", json={"name": f"hoc-e2e-{label}-{uuid.uuid4().hex[:6]}"}
            ).json()["id"]
            model_id = client.post(
                f"/stores/{store_id}/authorization-models", json=json.loads(MODEL_JSON.read_text())
            ).json()["authorization_model_id"]
    except (httpx.HTTPError, KeyError, OSError) as exc:
        unavailable(f"OpenFGA not reachable at {OPENFGA_URL}: {exc}")
        raise
    return FgaStore(OPENFGA_URL, store_id, model_id)


def _delete_store(store: FgaStore) -> None:
    with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
        client.delete(f"/stores/{store.store_id}")


def _sync(store: FgaStore, keycloak_url: str, source: SourceConnector) -> None:
    """Keycloak groups + one document source connector + organization policy → the store."""

    async def sync() -> None:
        engine = OpenFgaEngine.connect(
            store.url, store.store_id, _fresh(), authorization_model_id=store.model_id
        )
        try:
            state = InMemoryConnectorStateStore(max_staleness=timedelta(hours=1))
            audit = InMemoryAuditSink()
            groups = KeycloakGroupsConnector(
                keycloak_url, "acme", admin_user="admin", admin_password="admin"
            )
            report = await reconcile(groups, tuples=engine, state=state, audit=audit)
            assert report.added > 0
            report = await reconcile(source, tuples=engine, state=state, audit=audit)
            assert report.added > 0
            policy = json.loads((GENERATED / "policy-tuples.json").read_text())
            await engine.write_tuples([(t["user"], t["relation"], t["object"]) for t in policy])
        finally:
            await engine.close()

    asyncio.run(sync())


@pytest.fixture(scope="session")
def files_store(keycloak_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FgaStore]:
    from fixtures.acme.materialize import write_files_tree

    tree = write_files_tree(tmp_path_factory.mktemp("acme-files"))
    store = _new_store("files")
    _sync(store, keycloak_url, FilesConnector(Path(tree), name="files-acme"))
    yield store
    _delete_store(store)


@pytest.fixture(scope="session")
def nextcloud_store(keycloak_url: str) -> Iterator[FgaStore]:
    try:
        with httpx.Client(auth=("admin", "admin"), timeout=10) as client:
            probe = client.request(
                "PROPFIND",
                f"{NEXTCLOUD_URL}/remote.php/dav/files/admin/ACME",
                headers={"Depth": "0"},
            )
    except httpx.HTTPError as exc:
        unavailable(f"Nextcloud not reachable at {NEXTCLOUD_URL}: {exc}")
        raise
    if probe.status_code != 207:
        unavailable("Nextcloud has no /ACME tree; run scripts/load_nextcloud.py")
    store = _new_store("nextcloud")
    _sync(
        store,
        keycloak_url,
        NextcloudConnector(NEXTCLOUD_URL, user="admin", password="admin", root="/ACME"),
    )
    yield store
    _delete_store(store)


async def _engine(store: FgaStore) -> OpenFgaEngine:
    return OpenFgaEngine.connect(
        store.url, store.store_id, _fresh(), authorization_model_id=store.model_id
    )


@pytest.fixture
async def files_engine(files_store: FgaStore) -> AsyncIterator[OpenFgaEngine]:
    engine = await _engine(files_store)
    yield engine
    await engine.close()


@pytest.fixture
async def nextcloud_engine(nextcloud_store: FgaStore) -> AsyncIterator[OpenFgaEngine]:
    engine = await _engine(nextcloud_store)
    yield engine
    await engine.close()


async def _check(engine: OpenFgaEngine, case: GoldenCase) -> None:
    candidates = [{"id": d} for d in (*case.expected_visible, *case.expected_invisible)]
    result = await filter_items(case.chain(), candidates, engine=engine, audit=InMemoryAuditSink())
    kept = {item["id"] for item in result.kept}
    leaked = kept & set(case.expected_invisible)
    missing = set(case.expected_visible) - kept
    assert not leaked, f"{case.subject} must not see {sorted(leaked)}"
    assert not missing, f"{case.subject} should see {sorted(missing)}"


async def test_files_connector_end_to_end(files_engine: OpenFgaEngine, case: GoldenCase) -> None:
    await _check(files_engine, case)


async def test_nextcloud_connector_end_to_end(
    nextcloud_engine: OpenFgaEngine, case: GoldenCase
) -> None:
    await _check(nextcloud_engine, case)
