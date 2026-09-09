"""The CLI turns any HocError into `reason: message` on stderr and exit code 1 (ADR 0023)."""

from __future__ import annotations

import pytest

from headofcontext.cli import main
from headofcontext.core.errors import ConfigurationError


def test_configuration_error_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib

    cli = importlib.import_module("headofcontext.cli.main")

    def broken_settings(need: object = None) -> object:
        raise ConfigurationError("missing required setting HOC_POSTGRES_DSN")

    monkeypatch.setattr(cli, "_settings", broken_settings)
    assert main(["--env-file", "/dev/null", "db", "status"]) == 1
    err = capsys.readouterr().err
    assert "configuration_error" in err and "HOC_POSTGRES_DSN" in err
    assert "Traceback" not in err


def test_missing_settings_from_env_are_a_configuration_error() -> None:
    from headofcontext.settings import Settings

    with pytest.raises(ConfigurationError, match="HOC_POSTGRES_DSN"):
        Settings.from_env({})


def test_database_only_commands_need_only_the_dsn() -> None:
    """ADR 0026: `hoc db migrate` on a migration Job knows nothing about the IdP."""
    from headofcontext.settings import REQUIRE_DB, REQUIRE_ENGINE, Settings

    settings = Settings.from_env({"HOC_POSTGRES_DSN": "postgresql://x"}, require=REQUIRE_DB)
    assert settings.postgres_dsn == "postgresql://x" and settings.oidc_issuer == ""
    with pytest.raises(ConfigurationError, match="HOC_OPENFGA_URL"):
        Settings.from_env({"HOC_POSTGRES_DSN": "postgresql://x"}, require=REQUIRE_ENGINE)
