"""Golden set through the HTTP service: one biscuit per subject, read/filter per question."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest

from headofcontext.api import Settings, build_services, create_app
from tests.golden.conftest import GoldenCase
from tests.integration.test_api import FULL_SCOPE, _agent_token, _user_token
from tests.services import FgaStore, load_json

pytestmark = [pytest.mark.golden, pytest.mark.integration]


@pytest.fixture(scope="module")
def api_settings(openfga_store: FgaStore, pg_dsn: str, keycloak_url: str) -> Settings:
    return Settings(
        allow_ephemeral_root_key=True,
        postgres_dsn=pg_dsn,
        openfga_url=openfga_store.url,
        openfga_store_id=openfga_store.store_id,
        openfga_model_id=openfga_store.model_id,
        oidc_issuer=f"{keycloak_url}/realms/acme",
        oidc_audience="headofcontext",
        oidc_client_id="headofcontext",
        oidc_client_secret="hoc-dev-secret",
        token_ttl=timedelta(hours=1),
        connectors_required=False,
    )


@pytest.fixture(scope="module")
async def api(api_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    services = build_services(api_settings)
    app = create_app(api_settings, services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hoc"
    ) as client:
        yield client
    await services.aclose()


@pytest.fixture(scope="module")
def agent_header(keycloak_url: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_agent_token(keycloak_url, 'assistant')}"}


class BiscuitCache:
    def __init__(
        self, api: httpx.AsyncClient, keycloak_url: str, agent_header: dict[str, str]
    ) -> None:
        self.api, self.keycloak_url, self.agent_header = api, keycloak_url, agent_header
        self.tokens: dict[str, str] = {}
        users = load_json("users.json")
        assert isinstance(users, list)
        self.usernames = {u["id"]: u["username"] for u in users}

    async def token(self, subject: str) -> str:
        if subject not in self.tokens:
            response = await self.api.post(
                "/v1/tokens/issue",
                headers=self.agent_header,
                json={
                    "user_token": _user_token(self.keycloak_url, self.usernames[subject]),
                    "scope": FULL_SCOPE,
                },
            )
            assert response.status_code == 200, response.text
            self.tokens[subject] = str(response.json()["token"])
        return self.tokens[subject]


@pytest.fixture(scope="module")
def biscuits(
    api: httpx.AsyncClient, keycloak_url: str, agent_header: dict[str, str]
) -> BiscuitCache:
    return BiscuitCache(api, keycloak_url, agent_header)


@pytest.mark.asyncio(loop_scope="module")
async def test_golden_over_http(
    api: httpx.AsyncClient, biscuits: BiscuitCache, agent_header: dict[str, str], case: GoldenCase
) -> None:
    token = await biscuits.token(case.subject)
    items = [{"id": d} for d in (*case.expected_visible, *case.expected_invisible)]
    response = await api.post(
        "/v1/read/filter", headers=agent_header, json={"token": token, "items": items}
    )
    assert response.status_code == 200, response.text
    kept = set(response.json()["kept"])
    assert kept.isdisjoint(case.expected_invisible), "leak"
    assert kept == set(case.expected_visible)
