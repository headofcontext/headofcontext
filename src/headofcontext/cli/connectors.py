"""Connector configuration: a JSON list, secrets resolved from the environment (ADR 0012)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from headofcontext.core.errors import ConfigurationError
from headofcontext.identity import KeycloakGroupsConnector
from headofcontext.plugins import Factory, resolve
from headofcontext.read.connectors import (
    FilesConnector,
    NextcloudConnector,
    PipesHubConnector,
    SourceConnector,
)

BUILTIN_CONNECTORS: dict[str, Factory] = {
    "files": lambda args: FilesConnector(Path(str(args.pop("root"))), **args),
    "nextcloud": lambda args: NextcloudConnector(str(args.pop("base_url")), **args),
    "pipeshub": lambda args: PipesHubConnector(str(args.pop("base_url")), **args),
    "keycloak_groups": lambda args: KeycloakGroupsConnector(
        str(args.pop("base_url")), str(args.pop("realm")), **args
    ),
}


def load_config(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ConfigurationError(f"{path}: expected a JSON list of connector objects")
    return data


def resolve_secrets(spec: dict[str, Any]) -> dict[str, Any]:
    """``password_env: NAME`` becomes ``password: $NAME``; a missing variable is an error."""
    resolved: dict[str, Any] = {}
    for key, value in spec.items():
        if key.endswith("_env"):
            name = str(value)
            if name not in os.environ:
                raise ConfigurationError(f"environment variable {name!r} is not set (for {key})")
            resolved[key[: -len("_env")]] = os.environ[name]
        else:
            resolved[key] = value
    return resolved


def build_connectors(
    config: list[dict[str, Any]], *, plugins: dict[str, Factory] | None = None
) -> list[SourceConnector]:
    """Connectors from the config list; ``type`` names a built-in or a plugin (ADR 0019)."""
    connectors: list[SourceConnector] = []
    for spec in config:
        kind = str(spec.get("type", ""))
        args = resolve_secrets({k: v for k, v in spec.items() if k != "type"})
        factory = resolve(kind, BUILTIN_CONNECTORS, plugins, "source_connectors")
        if factory is None:
            raise ConfigurationError(f"unknown connector type {kind!r}")
        connectors.append(factory(args))
    return connectors
