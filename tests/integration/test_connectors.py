"""Nextcloud connector against the real container loaded with ACME (scripts/load_nextcloud.py)."""

import os

import httpx
import pytest

from headofcontext.read.connectors import NextcloudConnector
from tests.services import load_json, unavailable

pytestmark = pytest.mark.integration

NEXTCLOUD_URL = os.environ.get("HOC_NEXTCLOUD_URL", "http://localhost:8090")


@pytest.fixture(scope="session")
def nextcloud_url() -> str:
    try:
        with httpx.Client(auth=("admin", "admin"), timeout=10) as client:
            response = client.request(
                "PROPFIND",
                f"{NEXTCLOUD_URL}/remote.php/dav/files/admin/ACME",
                headers={"Depth": "0"},
            )
            if response.status_code != 207:
                unavailable("Nextcloud has no /ACME tree; run scripts/load_nextcloud.py")
    except httpx.HTTPError as exc:
        unavailable(f"Nextcloud not reachable at {NEXTCLOUD_URL}: {exc}")
    return NEXTCLOUD_URL


async def test_nextcloud_snapshot_matches_fixture_acls(nextcloud_url: str) -> None:
    connector = NextcloudConnector(nextcloud_url, user="admin", password="admin", root="/ACME")
    snapshot = await connector.snapshot()
    expected = {
        (t["user"], t["relation"], t["object"])
        for t in load_json("tuples.json")  # type: ignore[union-attr]
        if t["relation"] in ("viewer", "editor", "parent")
        and t["object"].startswith(("source:", "document:"))
    }
    # Restricted documents live under /ACME/restricted/<dept> in Nextcloud: a parent source with
    # no share, equivalent to the fixture's "no parent".
    got = {
        t
        for t in snapshot.tuples
        if not (t[1] == "parent" and t[0] == "source:nextcloud-restricted")
    }
    missing = expected - got
    extra = got - expected
    assert not missing, f"missing {len(missing)}: {sorted(missing)[:5]}"
    assert not extra, f"extra {len(extra)}: {sorted(extra)[:5]}"
    assert len(snapshot.documents) == 500
