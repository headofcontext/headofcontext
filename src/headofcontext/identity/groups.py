"""Keycloak group membership → ``group:X#member@user:Y`` tuples (ADR 0004, ADR 0010).

Reads the admin API with a dedicated client and returns a connector snapshot; it never writes to
Keycloak. Group paths (``/direction``) become ``group:direction``; nested paths keep their
slashes (``group:direction/rh``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from headofcontext.core import Tuple
from headofcontext.core.errors import IdentityError, InvariantViolation
from headofcontext.core.refs import validate_ref
from headofcontext.read.connectors.base import Snapshot


class KeycloakGroupsConnector:
    def __init__(
        self,
        base_url: str,
        realm: str,
        *,
        admin_user: str,
        admin_password: str,
        admin_realm: str = "master",
        client_id: str = "admin-cli",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        name: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._admin_user = admin_user
        self._admin_password = admin_password
        self._admin_realm = admin_realm
        self._client_id = client_id
        self._transport = transport
        self._timeout = timeout_seconds
        self._name = name or f"keycloak-{realm}"

    @property
    def name(self) -> str:
        return self._name

    async def snapshot(self) -> Snapshot:
        tuples: set[Tuple] = set()
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            token = await self._admin_token(http)
            headers = {"Authorization": f"Bearer {token}"}
            groups = await self._all_groups(http, headers)
            for group in groups:
                group_ref = _group_ref(str(group["path"]))
                for member in await self._members(http, headers, str(group["id"])):
                    username = member.get("username")
                    if not isinstance(username, str) or not member.get("enabled", True):
                        continue
                    try:
                        user_ref = validate_ref(f"user:{username}", "user")
                    except InvariantViolation:
                        continue
                    tuples.add((user_ref, "member", group_ref))
        return Snapshot(
            connector=self._name, tuples=frozenset(tuples), documents=(), taken_at=datetime.now(UTC)
        )

    async def _admin_token(self, http: httpx.AsyncClient) -> str:
        url = f"{self._base}/realms/{self._admin_realm}/protocol/openid-connect/token"
        try:
            response = await http.post(
                url,
                data={
                    "grant_type": "password",
                    "client_id": self._client_id,
                    "username": self._admin_user,
                    "password": self._admin_password,
                },
            )
            response.raise_for_status()
            token = response.json().get("access_token")
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityError("keycloak admin login failed") from exc
        if not isinstance(token, str):
            raise IdentityError("keycloak admin login returned no token")
        return token

    async def _all_groups(
        self, http: httpx.AsyncClient, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        first = 0
        while True:
            page = await self._get(
                http, f"/admin/realms/{self._realm}/groups", headers, {"first": first, "max": 100}
            )
            if not page:
                break
            for group in page:
                flat.extend(_flatten(group))
            first += 100
        # Subgroups are lazily loaded in recent Keycloak versions: fetch children explicitly.
        for group in list(flat):
            if int(group.get("subGroupCount", 0) or 0) > 0 and not group.get("subGroups"):
                children = await self._get(
                    http, f"/admin/realms/{self._realm}/groups/{group['id']}/children", headers, {}
                )
                for child in children:
                    flat.extend(_flatten(child))
        return flat

    async def _members(
        self, http: httpx.AsyncClient, headers: dict[str, str], group_id: str
    ) -> list[dict[str, Any]]:
        members: list[dict[str, Any]] = []
        first = 0
        while True:
            page = await self._get(
                http,
                f"/admin/realms/{self._realm}/groups/{group_id}/members",
                headers,
                {"first": first, "max": 100},
            )
            if not page:
                break
            members.extend(page)
            first += 100
        return members

    async def _get(
        self, http: httpx.AsyncClient, path: str, headers: dict[str, str], params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            response = await http.get(self._base + path, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityError(f"keycloak admin read failed: {path}") from exc
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _flatten(group: dict[str, Any]) -> list[dict[str, Any]]:
    out = [group]
    for child in group.get("subGroups") or []:
        if isinstance(child, dict):
            out.extend(_flatten(child))
    return out


def _group_ref(path: str) -> str:
    return validate_ref("group:" + path.strip("/"), "group")
