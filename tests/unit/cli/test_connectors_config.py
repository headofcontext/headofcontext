import json
from pathlib import Path

import pytest

from headofcontext.cli.connectors import build_connectors, load_config
from headofcontext.core.errors import ConfigurationError
from headofcontext.identity import KeycloakGroupsConnector
from headofcontext.read.connectors import FilesConnector, NextcloudConnector, PipesHubConnector


def test_builds_every_type_and_resolves_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NC_PASS", "s3cret")
    monkeypatch.setenv("KC_PASS", "admin")
    monkeypatch.setenv("PH_TOKEN", "pat")
    config = [
        {"type": "files", "name": "files-acme", "root": str(tmp_path)},
        {
            "type": "nextcloud",
            "base_url": "http://nc",
            "user": "svc",
            "password_env": "NC_PASS",
            "root": "/ACME",
        },
        {
            "type": "keycloak_groups",
            "base_url": "http://kc",
            "realm": "acme",
            "admin_user": "admin",
            "admin_password_env": "KC_PASS",
        },
        {"type": "pipeshub", "base_url": "http://ph", "token_env": "PH_TOKEN"},
    ]
    connectors = build_connectors(config)
    assert [type(c) for c in connectors] == [
        FilesConnector,
        NextcloudConnector,
        KeycloakGroupsConnector,
        PipesHubConnector,
    ]
    assert [c.name for c in connectors] == ["files-acme", "nextcloud", "keycloak-acme", "pipeshub"]


def test_missing_secret_env_is_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(ConfigurationError, match="NOPE"):
        build_connectors(
            [{"type": "nextcloud", "base_url": "http://nc", "user": "svc", "password_env": "NOPE"}]
        )


def test_unknown_type_is_an_error() -> None:
    with pytest.raises(ConfigurationError, match="unknown connector type"):
        build_connectors([{"type": "sharepoint"}])


def test_load_config(tmp_path: Path) -> None:
    path = tmp_path / "hoc.connectors.json"
    path.write_text(json.dumps([{"type": "files", "name": "f", "root": str(tmp_path)}]))
    assert load_config(path)[0]["type"] == "files"
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(ConfigurationError, match="list"):
        load_config(tmp_path / "bad.json")
