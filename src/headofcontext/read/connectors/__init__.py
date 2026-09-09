"""Source connectors: permissions → OpenFGA tuples (ADR 0010)."""

from headofcontext.read.connectors.base import (
    ConnectorStateStore,
    ConnectorStateUnavailable,
    DocumentMeta,
    InMemoryConnectorStateStore,
    PostgresConnectorStateStore,
    Snapshot,
    SourceConnector,
    SyncReport,
    principal,
    reconcile,
)
from headofcontext.read.connectors.files import FilesConnector
from headofcontext.read.connectors.nextcloud import NextcloudConnector, NextcloudUnavailable
from headofcontext.read.connectors.pipeshub import PipesHubConnector, PipesHubUnavailable

__all__ = [
    "ConnectorStateStore",
    "ConnectorStateUnavailable",
    "DocumentMeta",
    "FilesConnector",
    "InMemoryConnectorStateStore",
    "NextcloudConnector",
    "NextcloudUnavailable",
    "PipesHubConnector",
    "PipesHubUnavailable",
    "PostgresConnectorStateStore",
    "Snapshot",
    "SourceConnector",
    "SyncReport",
    "principal",
    "reconcile",
]
