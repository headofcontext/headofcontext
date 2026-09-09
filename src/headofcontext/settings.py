"""Service configuration from the environment (ADR 0011). Secrets never live in the repo."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from headofcontext.core.errors import ConfigurationError

if TYPE_CHECKING:
    from headofcontext.identity import OidcConfig

REQUIRE_DB = frozenset({"HOC_POSTGRES_DSN"})
REQUIRE_ENGINE = REQUIRE_DB | {"HOC_OPENFGA_URL", "HOC_OPENFGA_STORE_ID"}
REQUIRE_SERVICE = REQUIRE_ENGINE | {
    "HOC_OIDC_ISSUER",
    "HOC_OIDC_CLIENT_ID",
    "HOC_OIDC_CLIENT_SECRET",
}


@dataclass(frozen=True, slots=True)
class Settings:
    postgres_dsn: str = ""
    openfga_url: str = ""
    openfga_store_id: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = "headofcontext"
    oidc_client_id: str = ""
    oidc_client_secret: str = field(default="", repr=False)
    openfga_model_id: str | None = None
    oidc_subject_claim: str = "preferred_username"
    oidc_internal_url: str | None = None
    # Identity (ADR 0019): keycloak, oidc, or a plugin; how an agent token is recognised.
    identity_provider: str = "keycloak"
    oidc_groups_claim: str = "groups"
    oidc_agent_claim: str = "clientId"
    oidc_agent_id_claim: str = "clientId"
    root_key_hex: str | None = field(default=None, repr=False)
    root_key_id: int = 1
    # Verify-only root public keys for rotation (ADR 0015): ((key_id, hex), ...).
    root_public_keys: tuple[tuple[int, str], ...] = ()
    rate_limit_per_minute: int = 600
    token_ttl: timedelta = timedelta(hours=1)
    approval_ttl: timedelta = timedelta(hours=1)
    approval_tools: tuple[str, ...] = ()
    memory_backend: str = "inmemory"
    memory_namespace: str = "hoc"
    # Memory backends (ADR 0018): Mem0 configuration as a JSON object; Zep key and base URL.
    mem0_config: str | None = None
    # PostgreSQL backend (ADR 0021): optional embedder as `package.module:callable`.
    memory_embedder: str | None = None
    zep_api_key: str | None = field(default=None, repr=False)
    zep_base_url: str | None = None
    connectors_required: bool = True
    connector_max_staleness: timedelta = timedelta(hours=1)
    otel_enabled: bool = False
    # OTLP/HTTP collector base URL; OTEL_EXPORTER_OTLP_ENDPOINT is the standard name.
    otel_endpoint: str | None = None
    service_name: str = "headofcontext"
    # Without HOC_ROOT_KEY_HEX the service refuses to start unless this is set (dev only).
    allow_ephemeral_root_key: bool = False
    approval_channels: tuple[str, ...] = ()
    # Solo operators approve their own agents' actions; enterprises keep four eyes (default).
    allow_self_approval: bool = False
    # Standing mandates (ADR 0016): longest a human may mandate an agent for, in days.
    mandate_max_days: int = 90
    # Apply schema migrations at startup (ADR 0020); false = refuse to start when behind.
    db_auto_migrate: bool = True
    # Connections per process shared by every store (ADR 0022).
    db_pool_size: int = 8
    # Application logging (ADR 0025): level name and `text` or `json`.
    log_level: str = "INFO"
    log_format: str = "text"
    # What plugins may read (ADR 0014, 0024): the HOC_* and OTEL_* variables, never the whole
    # process environment with unrelated secrets in it.
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    def oidc_config(self) -> OidcConfig:
        """The identity provider's view of these settings (ADR 0027)."""
        from headofcontext.identity import OidcConfig  # noqa: PLC0415 — identity imports settings

        return OidcConfig(
            issuer=self.oidc_issuer,
            audience=self.oidc_audience,
            client_id=self.oidc_client_id,
            client_secret=self.oidc_client_secret,
            subject_claim=self.oidc_subject_claim,
            groups_claim=self.oidc_groups_claim,
            internal_base_url=self.oidc_internal_url,
        )

    @classmethod
    def from_env(
        cls, env: dict[str, str] | None = None, *, require: frozenset[str] = REQUIRE_SERVICE
    ) -> Settings:
        """Build from the environment; only the keys in ``require`` must be present.

        The service needs everything (`REQUIRE_SERVICE`); a CLI command that only touches the
        database (`hoc db migrate`, `hoc journal verify`) asks for `REQUIRE_DB` and works from a
        migration Job or a workstation that knows nothing about the IdP.
        """
        e = env if env is not None else dict(os.environ)

        def need(key: str) -> str:
            value = e.get(key, "")
            if not value and key in require:
                raise ConfigurationError(f"missing required setting {key}")
            return value

        def minutes(key: str, default: float) -> timedelta:
            return timedelta(minutes=float(e.get(key, default)))

        def flag(key: str, default: str = "false") -> bool:
            return e.get(key, default).lower() in ("1", "true", "yes")

        return cls(
            postgres_dsn=need("HOC_POSTGRES_DSN"),
            openfga_url=need("HOC_OPENFGA_URL"),
            openfga_store_id=need("HOC_OPENFGA_STORE_ID"),
            openfga_model_id=e.get("HOC_OPENFGA_MODEL_ID") or None,
            oidc_issuer=need("HOC_OIDC_ISSUER"),
            oidc_audience=e.get("HOC_OIDC_AUDIENCE", "headofcontext"),
            oidc_client_id=need("HOC_OIDC_CLIENT_ID"),
            oidc_client_secret=need("HOC_OIDC_CLIENT_SECRET"),
            oidc_subject_claim=e.get("HOC_OIDC_SUBJECT_CLAIM", "preferred_username"),
            oidc_internal_url=e.get("HOC_OIDC_INTERNAL_URL") or None,
            identity_provider=e.get("HOC_IDENTITY_PROVIDER", "keycloak"),
            oidc_groups_claim=e.get("HOC_OIDC_GROUPS_CLAIM", "groups"),
            oidc_agent_claim=e.get("HOC_OIDC_AGENT_CLAIM", "clientId"),
            oidc_agent_id_claim=e.get("HOC_OIDC_AGENT_ID_CLAIM", "clientId"),
            root_key_hex=e.get("HOC_ROOT_KEY_HEX") or None,
            root_key_id=int(e.get("HOC_ROOT_KEY_ID", "1")),
            root_public_keys=_public_keys(e.get("HOC_ROOT_PUBLIC_KEYS", "")),
            rate_limit_per_minute=int(e.get("HOC_RATE_LIMIT_PER_MINUTE", "600")),
            token_ttl=minutes("HOC_TOKEN_TTL_MINUTES", 60),
            approval_ttl=minutes("HOC_APPROVAL_TTL_MINUTES", 60),
            approval_tools=tuple(
                p for p in e.get("HOC_APPROVAL_TOOLS", "").split(",") if p.strip()
            ),
            memory_backend=e.get("HOC_MEMORY_BACKEND", "inmemory"),
            memory_namespace=e.get("HOC_MEMORY_NAMESPACE", "hoc"),
            mem0_config=e.get("HOC_MEM0_CONFIG") or None,
            memory_embedder=e.get("HOC_MEMORY_EMBEDDER") or None,
            zep_api_key=e.get("HOC_ZEP_API_KEY") or None,
            zep_base_url=e.get("HOC_ZEP_BASE_URL") or None,
            connectors_required=flag("HOC_CONNECTORS_REQUIRED", "true"),
            connector_max_staleness=minutes("HOC_CONNECTOR_MAX_STALENESS_MINUTES", 60),
            otel_enabled=flag("HOC_OTEL_ENABLED", "false"),
            otel_endpoint=e.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            or e.get("HOC_OTEL_ENDPOINT")
            or None,
            allow_ephemeral_root_key=flag("HOC_ALLOW_EPHEMERAL_ROOT_KEY", "false"),
            service_name=e.get("HOC_SERVICE_NAME", "headofcontext"),
            approval_channels=tuple(
                p.strip() for p in e.get("HOC_APPROVAL_CHANNELS", "").split(",") if p.strip()
            ),
            allow_self_approval=flag("HOC_ALLOW_SELF_APPROVAL", "false"),
            mandate_max_days=int(e.get("HOC_MANDATE_MAX_DAYS", "90")),
            db_auto_migrate=flag("HOC_DB_AUTO_MIGRATE", "true"),
            db_pool_size=int(e.get("HOC_DB_POOL_SIZE", "8")),
            log_level=e.get("HOC_LOG_LEVEL", "INFO"),
            log_format=e.get("HOC_LOG_FORMAT", "text"),
            env=plugin_env(e),
        )


PLUGIN_ENV_PREFIXES = ("HOC_", "OTEL_")
CORE_SECRETS = frozenset(
    {"HOC_ROOT_KEY_HEX", "HOC_OIDC_CLIENT_SECRET", "HOC_POSTGRES_DSN", "HOC_ZEP_API_KEY"}
)


def plugin_env(env: Mapping[str, str]) -> dict[str, str]:
    """The slice of the environment handed to plugins (ADR 0024): our namespaces, minus the
    core's own secrets. A plugin's secrets live under its own `HOC_<PLUGIN>_*` names."""
    return {
        k: v for k, v in env.items() if k.startswith(PLUGIN_ENV_PREFIXES) and k not in CORE_SECRETS
    }


def _public_keys(raw: str) -> tuple[tuple[int, str], ...]:
    """``"2=<hex>,3=<hex>"`` → ((2, hex), (3, hex)). Malformed entries fail loudly."""
    keys: list[tuple[int, str]] = []
    for raw_part in raw.split(","):
        part = raw_part.strip()
        if not part:
            continue
        key_id, sep, hex_key = part.partition("=")
        if not sep or not key_id.strip().isdigit() or not hex_key.strip():
            raise ConfigurationError(f"HOC_ROOT_PUBLIC_KEYS: expected <id>=<hex>, got {part!r}")
        keys.append((int(key_id), hex_key.strip()))
    return tuple(keys)
