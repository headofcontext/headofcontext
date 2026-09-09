"""`hoc mcp serve` as a real stdio subprocess wired to OpenFGA and PostgreSQL (ADR 0013)."""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from typing import Any

import biscuit_auth
import httpx
import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Kind, PrincipalChain, Scope
from headofcontext.tokens.biscuit import (
    InMemoryRevocationStore,
    KeyRing,
    TokenService,
)
from tests.services import FgaStore

pytestmark = pytest.mark.integration

SUBJECT = "user:alice.martin"


def _payload(result: Any) -> dict[str, Any]:
    assert isinstance(result, types.CallToolResult)
    text = "".join(c.text for c in result.content if isinstance(c, types.TextContent))
    assert not result.is_error, text
    if result.structured_content is not None:
        return dict(result.structured_content)
    return dict(json.loads(text))


def _visible_documents(store: FgaStore) -> list[str]:
    with httpx.Client(base_url=store.url, timeout=30) as client:
        body = client.post(
            f"/stores/{store.store_id}/list-objects",
            json={
                "authorization_model_id": store.model_id,
                "type": "document",
                "relation": "viewer",
                "user": SUBJECT,
            },
        ).json()
    return sorted(body["objects"])


async def test_mcp_serve_over_stdio(
    openfga_store: FgaStore, pg_dsn: str, keycloak_url: str
) -> None:
    raw = biscuit_auth.KeyPair().private_key.to_bytes()
    tokens = TokenService(
        KeyRing.from_private_key_bytes(1, raw),
        InMemoryRevocationStore(),
        InMemoryAuditSink(),
        ttl=timedelta(minutes=10),
    )
    scope = Scope.of(
        Capability(Kind.ACT, "tool:*"),
        Capability(Kind.READ, "document:*"),
        Capability(Kind.READ, "memory:*"),
        Capability(Kind.REMEMBER, "document:*"),
        Capability(Kind.REMEMBER, "memory:*"),
    )
    token = tokens.issue(PrincipalChain.root(SUBJECT, "agent:assistant", scope)).token
    visible = _visible_documents(openfga_store)
    assert visible, "fixture subject should see at least one document"
    hidden = next(
        f"document:acme-{n:04d}" for n in range(1, 501) if f"document:acme-{n:04d}" not in visible
    )

    env = {
        **os.environ,
        "HOC_POSTGRES_DSN": pg_dsn,
        "HOC_OPENFGA_URL": openfga_store.url,
        "HOC_OPENFGA_STORE_ID": openfga_store.store_id,
        "HOC_OPENFGA_MODEL_ID": openfga_store.model_id,
        "HOC_ROOT_KEY_HEX": raw.hex(),
        "HOC_OIDC_ISSUER": f"{keycloak_url}/realms/acme",
        "HOC_OIDC_CLIENT_ID": "headofcontext",
        "HOC_OIDC_CLIENT_SECRET": "hoc-dev-secret",
        "HOC_MCP_TOKEN": token,
        "HOC_MCP_AGENT": "agent:assistant",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "headofcontext.cli.main", "--env-file", "/dev/null", "mcp", "serve"],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"hoc_whoami", "hoc_filter", "hoc_gate", "hoc_recall", "hoc_remember"} <= names

        who = _payload(await client.call_tool("hoc_whoami", {}))
        assert who["subject"] == SUBJECT and who["actor"] == "agent:assistant"

        out = _payload(await client.call_tool("hoc_filter", {"items": [visible[0], hidden]}))
        assert out["kept"] == [visible[0]] and out["dropped"] == [hidden]

        deny = _payload(await client.call_tool("hoc_gate", {"tool": "no.such.tool", "args": {}}))
        assert deny["decision"]["outcome"] == "DENY"

        written = _payload(
            await client.call_tool(
                "hoc_remember", {"content": "note from mcp", "derived_from": [visible[0]]}
            )
        )
        assert written["derived_from"] == [visible[0]]
        leak = await client.call_tool(
            "hoc_remember", {"content": "should fail", "derived_from": [hidden]}
        )
        assert leak.is_error
