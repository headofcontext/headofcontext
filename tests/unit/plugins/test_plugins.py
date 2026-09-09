"""Plugin contract (ADR 0019): a distribution declaring the four entry point groups composes
into the channels, the connector runner, the identity provider and the memory adapter."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from headofcontext.actions import build_channels
from headofcontext.cli.connectors import build_connectors
from headofcontext.core.errors import ConfigurationError
from headofcontext.plugins import GROUPS, PluginError, discover
from headofcontext.services import build_identity, build_memory_adapter
from headofcontext.settings import Settings
from tests.unit.plugins.fake_plugin import FakeChannel, FakeConnector, FakeIdentity, FakeMemory

MODULE = "tests.unit.plugins.fake_plugin"
DECLARED = [
    EntryPoint("fake", f"{MODULE}:make_channel", GROUPS["approval_channels"]),
    EntryPoint("fake", f"{MODULE}:make_connector", GROUPS["source_connectors"]),
    EntryPoint("fake", f"{MODULE}:make_identity", GROUPS["identity_providers"]),
    EntryPoint("fake", f"{MODULE}:make_memory", GROUPS["memory_adapters"]),
    EntryPoint("broken", f"{MODULE}:does_not_exist", GROUPS["memory_adapters"]),
]


def _settings(**env: str) -> Settings:
    base = {
        "HOC_POSTGRES_DSN": "postgresql://x",
        "HOC_OPENFGA_URL": "http://fga",
        "HOC_OPENFGA_STORE_ID": "s",
        "HOC_OIDC_ISSUER": "http://idp/realms/acme",
        "HOC_OIDC_CLIENT_ID": "hoc",
        "HOC_OIDC_CLIENT_SECRET": "secret",
    }
    return Settings.from_env({**base, **env})


def entry_points_for(group: str) -> list[EntryPoint]:
    return [ep for ep in DECLARED if ep.group == group]


def test_discover_loads_real_entry_points() -> None:
    found = discover(GROUPS["source_connectors"], entry_points=entry_points_for)
    assert list(found) == ["fake"]
    assert isinstance(found["fake"]({"name": "x"}), FakeConnector)


def test_broken_plugin_fails_loudly() -> None:
    with pytest.raises(PluginError, match="broken"):
        discover(GROUPS["memory_adapters"], entry_points=entry_points_for)


def test_groups_are_the_documented_ones() -> None:
    assert set(GROUPS.values()) == {
        "headofcontext.approval_channels",
        "headofcontext.source_connectors",
        "headofcontext.identity_providers",
        "headofcontext.memory_adapters",
    }


async def test_channel_plugin_composes() -> None:
    channels = build_channels(
        {"HOC_APPROVAL_CHANNELS": "log,fake", "HOC_FAKE_URL": "u"},
        plugins=discover(GROUPS["approval_channels"], entry_points=entry_points_for),
    )
    assert [type(c).__name__ for c in channels] == ["LogChannel", "FakeChannel"]
    assert isinstance(channels[1], FakeChannel) and channels[1].env["HOC_FAKE_URL"] == "u"


def test_plugins_never_see_unrelated_environment() -> None:
    settings = _settings(HOC_MEMORY_BACKEND="fake", AWS_SECRET_ACCESS_KEY="nope", OTEL_X="1")
    assert "AWS_SECRET_ACCESS_KEY" not in settings.env
    assert settings.env["HOC_MEMORY_BACKEND"] == "fake" and settings.env["OTEL_X"] == "1"


def test_plugins_never_see_the_core_secrets() -> None:
    settings = _settings(
        HOC_ROOT_KEY_HEX="ab" * 32,
        HOC_ZEP_API_KEY="zep-secret",
        HOC_APPROVAL_WEBHOOK_SECRET="hook",
        HOC_TEAMS_TOKEN="plugin-own",
    )
    for core_secret in (
        "HOC_ROOT_KEY_HEX",
        "HOC_OIDC_CLIENT_SECRET",
        "HOC_POSTGRES_DSN",
        "HOC_ZEP_API_KEY",
    ):
        assert core_secret not in settings.env
    # A channel's own secret and a plugin's own variables still arrive.
    assert settings.env["HOC_APPROVAL_WEBHOOK_SECRET"] == "hook"
    assert settings.env["HOC_TEAMS_TOKEN"] == "plugin-own"


async def test_connector_plugin_composes_and_cannot_shadow_builtins(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plugins = {"fake": FakeConnector, "files": FakeConnector}
    built = build_connectors(
        [
            {"type": "fake", "name": "sharepoint-like", "site": "x"},
            {"type": "files", "name": "f", "root": str(tmp_path)},
        ],
        plugins=plugins,
    )
    assert isinstance(built[0], FakeConnector) and built[0].args == {
        "name": "sharepoint-like",
        "site": "x",
    }
    assert type(built[1]).__name__ == "FilesConnector"
    snapshot = await built[0].snapshot()
    assert ("user:alice", "viewer", "document:fake-1") in snapshot.tuples


async def test_identity_plugin_composes() -> None:
    settings = _settings(HOC_IDENTITY_PROVIDER="fake")
    provider = build_identity(settings, plugins={"fake": FakeIdentity})
    assert isinstance(provider, FakeIdentity)
    identity = await provider.verify_user_token("alice")
    assert identity.subject == "user:alice"
    await provider.aclose()
    assert provider.closed
    with pytest.raises(ConfigurationError, match="unknown identity provider"):
        build_identity(_settings(HOC_IDENTITY_PROVIDER="nope"), plugins={})


def test_builtin_identity_providers() -> None:
    from headofcontext.identity import KeycloakProvider, OidcProvider

    assert type(build_identity(_settings())) is KeycloakProvider
    generic = build_identity(_settings(HOC_IDENTITY_PROVIDER="oidc", HOC_OIDC_GROUPS_CLAIM="roles"))
    assert type(generic) is OidcProvider


async def test_memory_plugin_composes() -> None:
    settings = _settings(HOC_MEMORY_BACKEND="fake", HOC_MEMORY_NAMESPACE="ns")
    adapter = build_memory_adapter(settings, plugins={"fake": FakeMemory})
    assert isinstance(adapter, FakeMemory) and adapter.namespace_seen == "ns"
    ref = await adapter.store("memory:1", "hello world", "ns")
    assert [c.backend_refs for c in await adapter.search("hello", "ns", 5)] == [(ref,)]
    with pytest.raises(ConfigurationError, match="unknown memory backend"):
        build_memory_adapter(_settings(HOC_MEMORY_BACKEND="nope"), plugins={})


def test_postgres_backend_and_embedder_from_settings() -> None:
    from headofcontext.memory.postgres import PostgresMemoryAdapter

    adapter = build_memory_adapter(_settings(HOC_MEMORY_BACKEND="postgres"), plugins={})
    assert isinstance(adapter, PostgresMemoryAdapter) and adapter.embedder is None
    with pytest.raises(ConfigurationError, match="HOC_MEMORY_EMBEDDER"):
        build_memory_adapter(
            _settings(HOC_MEMORY_BACKEND="postgres", HOC_MEMORY_EMBEDDER="nope.module:fn"),
            plugins={},
        )
