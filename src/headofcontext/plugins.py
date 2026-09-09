"""Entry point discovery for the four extension groups (ADR 0019).

A plugin is an installed distribution declaring, in its own ``pyproject.toml``::

    [project.entry-points."headofcontext.source_connectors"]
    sharepoint = "hoc_enterprise.sharepoint:make_connector"

Built-in names are registered by the core first and cannot be shadowed. A plugin that fails to
import raises ``PluginError``: an authorization plugin must fail at startup, never degrade into
"not configured".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from headofcontext.core.errors import ConfigurationError

log = logging.getLogger(__name__)

GROUPS: dict[str, str] = {
    "approval_channels": "headofcontext.approval_channels",
    "source_connectors": "headofcontext.source_connectors",
    "identity_providers": "headofcontext.identity_providers",
    "memory_adapters": "headofcontext.memory_adapters",
}

Factory = Callable[..., Any]
EntryPointsFor = Callable[[str], Iterable[EntryPoint]]


class PluginError(ConfigurationError):
    reason = "plugin_error"


def discover(group: str, *, entry_points: EntryPointsFor | None = None) -> dict[str, Factory]:
    """``{name: factory}`` for every entry point of ``group``; loud on any import failure."""
    source = entry_points or _installed
    found: dict[str, Factory] = {}
    for ep in source(group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:
            raise PluginError(f"plugin {ep.name!r} in {group} failed to load: {exc}") from exc
        log.info("plugin loaded: %s (%s)", ep.name, group)
    return found


def _installed(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


def resolve(
    name: str,
    builtins: dict[str, Factory],
    plugins: dict[str, Factory] | None,
    group_key: str,
) -> Factory | None:
    """The factory for ``name``: built-ins first, then the given or discovered plugins."""
    if name in builtins:
        return builtins[name]
    found = discover(GROUPS[group_key]) if plugins is None else plugins
    return found.get(name)


__all__ = ["GROUPS", "Factory", "PluginError", "discover", "resolve"]
