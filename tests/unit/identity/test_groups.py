import httpx

from headofcontext.identity import KeycloakGroupsConnector

BASE = "http://kc.test"


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/realms/master/protocol/openid-connect/token"):
        return httpx.Response(200, json={"access_token": "admin-token"})
    assert request.headers.get("Authorization") == "Bearer admin-token"
    first = int(request.url.params.get("first", 0))
    if path == "/admin/realms/acme/groups":
        if first:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {"id": "g1", "name": "rh", "path": "/rh", "subGroupCount": 1, "subGroups": []},
                {"id": "g2", "name": "it", "path": "/it", "subGroupCount": 0, "subGroups": []},
            ],
        )
    if path == "/admin/realms/acme/groups/g1/children":
        return httpx.Response(
            200, json=[{"id": "g3", "name": "paie", "path": "/rh/paie", "subGroupCount": 0}]
        )
    if path.endswith("/members"):
        if first:
            return httpx.Response(200, json=[])
        gid = path.split("/")[-2]
        members = {
            "g1": [
                {"username": "alice.martin", "enabled": True},
                {"username": "bad name", "enabled": True},
            ],
            "g2": [{"username": "bob", "enabled": False}, {"username": "carol", "enabled": True}],
            "g3": [{"username": "alice.martin", "enabled": True}],
        }
        return httpx.Response(200, json=members[gid])
    return httpx.Response(404)


async def test_membership_snapshot() -> None:
    connector = KeycloakGroupsConnector(
        BASE,
        "acme",
        admin_user="admin",
        admin_password="admin",
        transport=httpx.MockTransport(_handler),
    )
    snapshot = await connector.snapshot()
    assert snapshot.connector == "keycloak-acme"
    assert snapshot.tuples == frozenset(
        {
            ("user:alice.martin", "member", "group:rh"),
            ("user:carol", "member", "group:it"),
            ("user:alice.martin", "member", "group:rh/paie"),
        }
    )
