"""The HTTP service against the real stack: Keycloak client credentials, biscuits, OpenFGA,
PostgreSQL journal and approvals (ADR 0011)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import biscuit_auth
import httpx
import pytest

from headofcontext.api import Settings, build_services, create_app
from headofcontext.core import Capability, Kind, Scope
from tests.services import FgaStore, load_json

pytestmark = pytest.mark.integration

FULL_SCOPE = {
    "capabilities": [
        {"kind": "read", "resource": "document:*"},
        {"kind": "read", "resource": "memory:*"},
        {"kind": "remember", "resource": "document:*"},
        {"kind": "remember", "resource": "memory:*"},
        {"kind": "act", "resource": "tool:*"},
    ]
}


def _user(department: str) -> dict[str, Any]:
    users = load_json("users.json")
    assert isinstance(users, list)
    return next(u for u in users if u["department"] == department and not u["intern"])


def _doc(confidentiality: str, department: str) -> str:
    docs = load_json("documents.json")
    assert isinstance(docs, list)
    return next(
        d["id"]
        for d in docs
        if d["confidentiality"] == confidentiality and d["department"] == department
    )


def _agent_token(keycloak_url: str, client_id: str) -> str:
    response = httpx.post(
        f"{keycloak_url}/realms/acme/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": f"{client_id}-dev-secret",
        },
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _user_token(keycloak_url: str, username: str) -> str:
    response = httpx.post(
        f"{keycloak_url}/realms/acme/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "headofcontext",
            "client_secret": "hoc-dev-secret",
            "username": username,
            "password": "password",
            "scope": "openid",
        },
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


ROOT_KEY = biscuit_auth.KeyPair()


@pytest.fixture(scope="module")
def settings(openfga_store: FgaStore, pg_dsn: str, keycloak_url: str) -> Settings:
    return Settings(
        root_key_hex=ROOT_KEY.private_key.to_bytes().hex(),
        postgres_dsn=pg_dsn,
        openfga_url=openfga_store.url,
        openfga_store_id=openfga_store.store_id,
        openfga_model_id=openfga_store.model_id,
        oidc_issuer=f"{keycloak_url}/realms/acme",
        oidc_audience="headofcontext",
        oidc_client_id="headofcontext",
        oidc_client_secret="hoc-dev-secret",
        approval_tools=("tool:payment.*", "tool:hr.export"),
        token_ttl=timedelta(minutes=30),
        connectors_required=False,
        memory_backend="postgres",
    )


@pytest.fixture
async def api(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    services = build_services(settings)
    app = create_app(settings, services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hoc"
    ) as client:
        yield client
    await services.aclose()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _issue(
    api: httpx.AsyncClient, keycloak_url: str, username: str, scope: dict[str, Any] = FULL_SCOPE
) -> str:
    response = await api.post(
        "/v1/tokens/issue",
        headers=_bearer(_agent_token(keycloak_url, "assistant")),
        json={"user_token": _user_token(keycloak_url, username), "scope": scope},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["chain"]["subject"] == f"user:{username}"
        and body["chain"]["actor"] == "agent:assistant"
    )
    return str(body["token"])


async def test_health(api: httpx.AsyncClient) -> None:
    response = await api.get("/v1/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


async def test_ready_reports_every_dependency(api: httpx.AsyncClient) -> None:
    response = await api.get("/v1/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "postgres": "ok",
        "openfga": "ok",
        "connectors": "fresh",
        "schema": "ok",
    }


async def test_not_ready_when_engine_unreachable_or_connectors_stale(
    settings: Settings, pg_dsn: str
) -> None:
    """ADR 0020: a pod that would deny every decision must not be in rotation."""
    from dataclasses import replace

    from headofcontext.read.connectors import PostgresConnectorStateStore

    state = PostgresConnectorStateStore(
        pg_dsn, settings.connector_max_staleness, namespace="ready-test"
    )
    state.ensure_schema()
    state.register("never-synced")
    broken = replace(
        settings,
        openfga_url="http://127.0.0.1:1",
        openfga_store_id="ready-test",
        connectors_required=True,
    )
    services = build_services(broken)
    app = create_app(broken, services)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://hoc"
        ) as client:
            alive = await client.get("/v1/health")
            assert alive.status_code == 200
            response = await client.get("/v1/ready")
            assert response.status_code == 503, response.text
            checks = response.json()["checks"]
            assert response.json()["status"] == "not_ready"
            assert checks["postgres"] == "ok" and checks["schema"] == "ok"
            assert checks["openfga"] == "error"
            assert checks["connectors"] == "stale"
    finally:
        await services.aclose()


async def test_not_ready_when_the_pinned_model_is_not_in_the_store(settings: Settings) -> None:
    """A model id from another store answers every check with ValidationException: not ready."""
    from dataclasses import replace

    services = build_services(replace(settings, openfga_model_id="01ARZ3NDEKTSV4RRFFQ69G5FAV"))
    app = create_app(settings, services)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://hoc"
        ) as client:
            response = await client.get("/v1/ready")
            assert response.status_code == 503, response.text
            assert response.json()["checks"]["openfga"] == "error"
    finally:
        await services.aclose()


async def test_unauthenticated_is_401(api: httpx.AsyncClient) -> None:
    response = await api.post("/v1/tokens/inspect", json={"token": "x" * 32})
    assert response.status_code == 401
    response = await api.post(
        "/v1/tokens/inspect", headers=_bearer("garbage"), json={"token": "x" * 32}
    )
    assert response.status_code == 401 and response.json()["reason"] == "identity_error"


async def test_issue_requires_agent_binding(api: httpx.AsyncClient, keycloak_url: str) -> None:
    """The `mailer` agent is not bound to any user in the fixtures: no root biscuit for it."""
    user = _user("rh")
    response = await api.post(
        "/v1/tokens/issue",
        headers=_bearer(_agent_token(keycloak_url, "mailer")),
        json={"user_token": _user_token(keycloak_url, user["username"]), "scope": FULL_SCOPE},
    )
    assert response.status_code == 403, response.text
    assert response.json()["reason"] == "not_related"


async def test_issue_refuses_a_user_as_caller(api: httpx.AsyncClient, keycloak_url: str) -> None:
    user = _user("rh")
    response = await api.post(
        "/v1/tokens/issue",
        headers=_bearer(_user_token(keycloak_url, user["username"])),
        json={"user_token": _user_token(keycloak_url, user["username"]), "scope": FULL_SCOPE},
    )
    assert response.status_code == 403, response.text
    assert response.json()["reason"] == "caller_not_agent"


async def test_read_filter_and_inspect(api: httpx.AsyncClient, keycloak_url: str) -> None:
    rh = _user("rh")
    token = await _issue(api, keycloak_url, rh["username"])
    agent = _bearer(_agent_token(keycloak_url, "assistant"))
    hr_doc, it_doc, public = (
        _doc("internal", "rh"),
        _doc("internal", "it"),
        _doc("public", "finance"),
    )
    response = await api.post(
        "/v1/read/filter",
        headers=agent,
        json={
            "token": token,
            "items": [{"id": it_doc}, {"id": hr_doc}, {"id": public}, {"id": "nope"}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kept"] == [hr_doc, public]
    assert set(body["dropped"]) == {it_doc, "invalid"}
    response = await api.post("/v1/tokens/inspect", headers=agent, json={"token": token})
    assert (
        response.json()["chain"]["depth"] == 0 and response.json()["chain"]["subject"] == rh["id"]
    )


async def test_read_filter_honours_opaque_attenuation_checks(
    api: httpx.AsyncClient, keycloak_url: str
) -> None:
    """T19: a restriction added with plain biscuit tooling narrows the read filter too."""
    rh = _user("rh")
    token = await _issue(api, keycloak_url, rh["username"])
    hr_doc, public = _doc("internal", "rh"), _doc("public", "finance")
    narrowed = (
        biscuit_auth.Biscuit.from_base64(token, ROOT_KEY.public_key)
        .append(biscuit_auth.BlockBuilder(f'check if resource($r), $r == "{public}";'))
        .to_base64()
    )
    response = await api.post(
        "/v1/read/filter",
        headers=_bearer(_agent_token(keycloak_url, "assistant")),
        json={"token": narrowed, "items": [{"id": hr_doc}, {"id": public}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kept"] == [public] and response.json()["dropped"] == [hr_doc]


async def test_memory_honours_opaque_attenuation_checks(
    api: httpx.AsyncClient, keycloak_url: str
) -> None:
    """T21: the same opaque block narrows remember and recall through the service."""
    rh = _user("rh")
    token = await _issue(api, keycloak_url, rh["username"])
    hr_doc = _doc("internal", "rh")
    narrowed = (
        biscuit_auth.Biscuit.from_base64(token, ROOT_KEY.public_key)
        .append(biscuit_auth.BlockBuilder('check if resource($r), $r == "document:none";'))
        .to_base64()
    )
    assistant = _bearer(_agent_token(keycloak_url, "assistant"))
    refused = await api.post(
        "/v1/memory/remember",
        headers=assistant,
        json={"token": narrowed, "content": "x", "derived_from": [hr_doc]},
    )
    assert refused.status_code == 403 and refused.json()["reason"] == "token_check_failed"
    nothing = await api.post(
        "/v1/memory/recall",
        headers=assistant,
        json={"token": narrowed, "query": "salaires", "limit": 5},
    )
    assert nothing.status_code == 200 and nothing.json()["memories"] == []


async def test_issue_refuses_an_agent_token_as_user(
    api: httpx.AsyncClient, keycloak_url: str
) -> None:
    """T20: a service-account JWT is not a human, whatever tuples exist."""
    response = await api.post(
        "/v1/tokens/issue",
        headers=_bearer(_agent_token(keycloak_url, "assistant")),
        json={"user_token": _agent_token(keycloak_url, "mailer"), "scope": FULL_SCOPE},
    )
    assert response.status_code == 403 and response.json()["reason"] == "user_token_not_human"


async def test_wrong_caller_cannot_use_token(api: httpx.AsyncClient, keycloak_url: str) -> None:
    token = await _issue(api, keycloak_url, _user("rh")["username"])
    response = await api.post(
        "/v1/read/filter",
        headers=_bearer(_agent_token(keycloak_url, "mailer")),
        json={"token": token, "items": [{"id": _doc("public", "rh")}]},
    )
    assert response.status_code == 403 and response.json()["reason"] == "token_invalid"


async def test_actions_delegation_and_revocation(api: httpx.AsyncClient, keycloak_url: str) -> None:
    rh = _user("rh")
    token = await _issue(api, keycloak_url, rh["username"])
    assistant, mailer = (
        _bearer(_agent_token(keycloak_url, "assistant")),
        _bearer(_agent_token(keycloak_url, "mailer")),
    )

    gate = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:mail.send", "args": {"to": "x"}},
    )
    assert gate.json()["decision"]["outcome"] == "ALLOW"

    attenuated = await api.post(
        "/v1/tokens/attenuate",
        headers=assistant,
        json={
            "token": token,
            "to_actor": "agent:mailer",
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.*"}]},
        },
    )
    assert attenuated.status_code == 200, attenuated.text
    child = attenuated.json()["token"]
    assert attenuated.json()["chain"]["depth"] == 1

    ok = await api.post(
        "/v1/actions/gate",
        headers=mailer,
        json={"token": child, "tool": "tool:mail.send", "args": {}},
    )
    assert ok.json()["decision"]["outcome"] == "ALLOW"
    denied = await api.post(
        "/v1/actions/gate",
        headers=mailer,
        json={"token": child, "tool": "tool:finance.report", "args": {}},
    )
    assert (
        denied.status_code == 403 and denied.json()["reason"] == "token_invalid"
    )  # scope narrowed in the biscuit

    widen = await api.post(
        "/v1/tokens/attenuate",
        headers=mailer,
        json={"token": child, "to_actor": "agent:sub", "scope": FULL_SCOPE},
    )
    assert widen.status_code == 403 and widen.json()["reason"] == "scope_escalation"

    revoked = await api.post(
        "/v1/tokens/revoke", headers=mailer, json={"token": child, "reason": "done"}
    )
    assert revoked.status_code == 200
    dead = await api.post(
        "/v1/actions/gate",
        headers=mailer,
        json={"token": child, "tool": "tool:mail.send", "args": {}},
    )
    assert dead.status_code == 403 and dead.json()["reason"] == "token_revoked"
    still = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:mail.send", "args": {}},
    )
    assert still.json()["decision"]["outcome"] == "ALLOW"


async def test_approval_roundtrip(api: httpx.AsyncClient, keycloak_url: str) -> None:
    rh, boss = _user("rh"), _user("direction")
    token = await _issue(api, keycloak_url, rh["username"])
    assistant = _bearer(_agent_token(keycloak_url, "assistant"))
    args = {"year": 2026}
    pending = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:hr.export", "args": args},
    )
    body = pending.json()
    assert (
        body["decision"]["outcome"] == "REQUIRE_APPROVAL"
        and body["approval"]["status"] == "pending"
    )
    request_id = body["approval"]["request_id"]

    listed = await api.get(
        "/v1/approvals", headers=_bearer(_user_token(keycloak_url, boss["username"]))
    )
    assert any(r["request_id"] == request_id for r in listed.json()["requests"])

    self_approve = await api.post(
        f"/v1/approvals/{request_id}/resolve",
        headers=_bearer(_user_token(keycloak_url, rh["username"])),
        json={"approved": True},
    )
    assert self_approve.status_code == 403

    # ADR 0017 (T15): a human with no `approver` tuple on the tool neither sees nor resolves it.
    stranger = _bearer(_user_token(keycloak_url, _user("magasin-lille")["username"]))
    unseen = await api.get("/v1/approvals", headers=stranger)
    assert all(r["request_id"] != request_id for r in unseen.json()["requests"])
    refused = await api.post(
        f"/v1/approvals/{request_id}/resolve", headers=stranger, json={"approved": True}
    )
    assert refused.status_code == 403 and refused.json()["reason"] == "approver_not_authorized"

    resolved = await api.post(
        f"/v1/approvals/{request_id}/resolve",
        headers=_bearer(_user_token(keycloak_url, boss["username"])),
        json={"approved": True, "reason": "ok"},
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "approved"

    redeemed = await api.post(
        "/v1/actions/redeem",
        headers=assistant,
        json={"token": token, "request_id": request_id, "tool": "tool:hr.export", "args": args},
    )
    assert redeemed.json()["outcome"] == "ALLOW"
    replay = await api.post(
        "/v1/actions/redeem",
        headers=assistant,
        json={"token": token, "request_id": request_id, "tool": "tool:hr.export", "args": args},
    )
    assert replay.json()["outcome"] == "DENY" and replay.json()["reason"] == "approval_consumed"


async def test_memory_roundtrip(api: httpx.AsyncClient, keycloak_url: str) -> None:
    rh, store = _user("rh"), _user("magasin-lille")
    assistant = _bearer(_agent_token(keycloak_url, "assistant"))
    rh_token, store_token = (
        await _issue(api, keycloak_url, rh["username"]),
        await _issue(api, keycloak_url, store["username"]),
    )
    hr_doc = _doc("internal", "rh")
    written = await api.post(
        "/v1/memory/remember",
        headers=assistant,
        json={
            "token": rh_token,
            "content": "Grille salaires 2026: +3 % vendeurs",
            "derived_from": [hr_doc],
        },
    )
    assert written.status_code == 200, written.text
    memory_id = written.json()["memory_id"]
    try:
        mine = await api.post(
            "/v1/memory/recall",
            headers=assistant,
            json={"token": rh_token, "query": "salaires", "limit": 5},
        )
        assert [m["memory_id"] for m in mine.json()["memories"]] == [memory_id]
        theirs = await api.post(
            "/v1/memory/recall",
            headers=assistant,
            json={"token": store_token, "query": "salaires", "limit": 5},
        )
        assert theirs.json()["memories"] == []
    finally:
        forgotten = await api.post(
            "/v1/memory/forget", headers=assistant, json={"token": rh_token, "memory_id": memory_id}
        )
        assert forgotten.status_code == 200


async def test_scope_shape_is_validated(api: httpx.AsyncClient, keycloak_url: str) -> None:
    response = await api.post(
        "/v1/tokens/issue",
        headers=_bearer(_agent_token(keycloak_url, "assistant")),
        json={
            "user_token": _user_token(keycloak_url, _user("rh")["username"]),
            "scope": {"capabilities": [{"kind": "root", "resource": "*"}]},
        },
    )
    assert response.status_code == 422


def test_settings_scope_helper() -> None:
    scope = Scope.of(Capability(Kind.READ, "document:*"))
    assert scope.covers(Capability(Kind.READ, "document:x"))


async def test_mandate_roundtrip(api: httpx.AsyncClient, keycloak_url: str) -> None:
    """ADR 0016: the human mandates the agent once, the agent works alone, one call revokes."""
    founder = _user("direction")
    human = _bearer(_user_token(keycloak_url, founder["username"]))
    assistant = _bearer(_agent_token(keycloak_url, "assistant"))
    mailer = _bearer(_agent_token(keycloak_url, "mailer"))

    created = await api.post(
        "/v1/mandates",
        headers=human,
        json={
            "agent": "agent:assistant",
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.*"}]},
            "expires_in_hours": 48,
            "max_token_ttl_minutes": 15,
        },
    )
    assert created.status_code == 200, created.text
    mandate = created.json()
    assert mandate["status"] == "active" and mandate["subject"] == f"user:{founder['username']}"

    # An agent cannot create mandates, and another human does not see this one.
    assert (await api.post("/v1/mandates", headers=assistant, json={})).status_code == 403
    other = _bearer(_user_token(keycloak_url, _user("rh")["username"]))
    assert (await api.get("/v1/mandates", headers=other)).json()["mandates"] == []

    # The agent issues on its own, later, with its credentials only.
    issued = await api.post(
        "/v1/tokens/issue",
        headers=assistant,
        json={
            "mandate_id": mandate["mandate_id"],
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.send"}]},
            "ttl_minutes": 600,
        },
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    assert any(r.startswith("mandate:") for r in issued.json()["revocation_ids"])

    wider = await api.post(
        "/v1/tokens/issue",
        headers=assistant,
        json={"mandate_id": mandate["mandate_id"], "scope": FULL_SCOPE},
    )
    assert wider.status_code == 403 and wider.json()["reason"] == "scope_escalation"
    stolen = await api.post(
        "/v1/tokens/issue",
        headers=mailer,
        json={
            "mandate_id": mandate["mandate_id"],
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.send"}]},
        },
    )
    assert stolen.status_code == 403 and stolen.json()["reason"] == "mandate_wrong_agent"
    both = await api.post(
        "/v1/tokens/issue",
        headers=assistant,
        json={
            "mandate_id": mandate["mandate_id"],
            "user_token": "x" * 32,
            "scope": FULL_SCOPE,
        },
    )
    assert both.status_code == 422

    gate = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:mail.send", "args": {"to": "x"}},
    )
    assert gate.json()["decision"]["outcome"] == "ALLOW", gate.text

    listed = await api.get("/v1/mandates", headers=human)
    mine = {m["mandate_id"]: m for m in listed.json()["mandates"]}  # earlier runs persist
    assert mine[mandate["mandate_id"]]["status"] == "active"

    # ADR 0016 amendment (T17): a delegate revoking its own copy does not touch the mandate.
    child = await api.post(
        "/v1/tokens/attenuate",
        headers=assistant,
        json={
            "token": token,
            "to_actor": "agent:mailer",
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.send"}]},
        },
    )
    assert child.status_code == 200, child.text
    sabotage = await api.post(
        "/v1/tokens/revoke",
        headers=mailer,
        json={"token": child.json()["token"], "reason": "sabotage"},
    )
    assert sabotage.status_code == 200
    parent_alive = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:mail.send", "args": {"to": "x"}},
    )
    assert parent_alive.json()["decision"]["outcome"] == "ALLOW", parent_alive.text
    still_active = await api.get("/v1/mandates", headers=human)
    assert {m["mandate_id"]: m["status"] for m in still_active.json()["mandates"]}[
        mandate["mandate_id"]
    ] == "active"

    revoked = await api.delete(f"/v1/mandates/{mandate['mandate_id']}", headers=human)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    dead = await api.post(
        "/v1/actions/gate",
        headers=assistant,
        json={"token": token, "tool": "tool:mail.send", "args": {"to": "x"}},
    )
    assert dead.status_code == 403
    again = await api.post(
        "/v1/tokens/issue",
        headers=assistant,
        json={
            "mandate_id": mandate["mandate_id"],
            "scope": {"capabilities": [{"kind": "act", "resource": "tool:mail.send"}]},
        },
    )
    assert again.status_code == 403 and again.json()["reason"] == "mandate_revoked"
