"""Error families (ADR 0023): dependencies down are 503, configuration mistakes never 403."""

from __future__ import annotations

import pytest

from headofcontext.actions.approvals import ApprovalStoreUnavailable
from headofcontext.api.app import status_for
from headofcontext.core.errors import (
    ApprovalError,
    AuditUnavailable,
    ConfigurationError,
    EngineUnavailable,
    HocError,
    TokenInvalid,
    Unavailable,
)
from headofcontext.db import SchemaOutOfDate
from headofcontext.memory.ledger import LedgerUnavailable
from headofcontext.memory.postgres import MemoryContentUnavailable
from headofcontext.plugins import PluginError
from headofcontext.read.connectors.base import ConnectorStateUnavailable
from headofcontext.tokens.biscuit.postgres import RevocationStoreUnavailable
from headofcontext.tokens.mandates import MandateStoreUnavailable


@pytest.mark.parametrize(
    "error",
    [
        EngineUnavailable("down"),
        AuditUnavailable("down"),
        ApprovalStoreUnavailable("down"),
        MandateStoreUnavailable("down"),
        RevocationStoreUnavailable("down"),
        LedgerUnavailable("down"),
        ConnectorStateUnavailable("down"),
        MemoryContentUnavailable("down"),
        SchemaOutOfDate("behind"),
    ],
)
def test_every_dependency_outage_is_unavailable_and_503(error: HocError) -> None:
    assert isinstance(error, Unavailable)
    assert status_for(error) == 503


def test_denials_stay_403_and_unknown_hoc_errors_default_to_403() -> None:
    assert status_for(TokenInvalid("bad")) == 403
    assert status_for(ApprovalError("no")) == 403
    assert status_for(HocError("generic")) == 403


def test_configuration_errors_are_typed() -> None:
    assert isinstance(PluginError("broken"), ConfigurationError)
    assert ConfigurationError("x").reason == "configuration_error"
