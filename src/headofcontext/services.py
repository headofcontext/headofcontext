"""Wiring of the core services behind the HTTP surface (ADR 0011)."""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass

import biscuit_auth

from headofcontext.actions import (
    ActionGate,
    PostgresApprovalStore,
    RequireApproval,
    build_channels,
)
from headofcontext.audit import (
    AuditSink,
    CompositeAuditSink,
    DecisionMetrics,
    OtelAuditExporter,
    PostgresAuditSink,
)
from headofcontext.audit.telemetry import Telemetry, build_telemetry
from headofcontext.core import Decider, DecisionPolicy
from headofcontext.core.errors import ConfigurationError
from headofcontext.db import LATEST, assert_current, migrate, ping
from headofcontext.db.pool import close_pools, configure
from headofcontext.engines.freshness import AlwaysFresh, FreshnessGuard
from headofcontext.engines.openfga import OpenFgaEngine
from headofcontext.identity import IdentityProvider, KeycloakProvider, OidcProvider
from headofcontext.identity.callers import AgentDetection
from headofcontext.memory import (
    InMemoryMemoryAdapter,
    MemoryAdapter,
    MemoryService,
    PostgresLedger,
    PostgresMemoryAdapter,
)
from headofcontext.memory.postgres import Embedder
from headofcontext.plugins import Factory, resolve
from headofcontext.read.connectors import PostgresConnectorStateStore
from headofcontext.readiness import ReadinessProbe
from headofcontext.settings import Settings
from headofcontext.tokens.biscuit import KeyRing, PostgresRevocationStore, TokenService
from headofcontext.tokens.mandates import MandateService, PostgresMandateStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Services:
    settings: Settings
    keyring: KeyRing
    audit: AuditSink
    engine: OpenFgaEngine
    freshness: FreshnessGuard
    identity: IdentityProvider
    agent_detection: AgentDetection
    tokens: TokenService
    decider: Decider
    gate: ActionGate
    memory: MemoryService
    mandates: MandateService
    readiness: ReadinessProbe
    telemetry: Telemetry | None = None

    async def aclose(self) -> None:
        if self.telemetry is not None:
            self.telemetry.shutdown()
        await self.engine.close()
        await self.identity.aclose()
        close_pools()


def build_services(settings: Settings) -> Services:
    # The schema is owned by the migration ledger (ADR 0020): applied here, or already applied
    # by `hoc db migrate`; a database behind the code stops the service before any decision.
    configure(max_size=settings.db_pool_size)
    if settings.db_auto_migrate:
        migrate(settings.postgres_dsn)
    else:
        assert_current(settings.postgres_dsn)
    keyring = build_keyring(settings)
    journal = PostgresAuditSink(settings.postgres_dsn)
    audit: AuditSink = journal
    telemetry = build_telemetry(
        enabled=settings.otel_enabled,
        service_name=settings.service_name,
        endpoint=settings.otel_endpoint,
    )
    if telemetry is not None:
        audit = CompositeAuditSink([journal, OtelAuditExporter(telemetry.tracer_provider)])

    freshness: FreshnessGuard
    if settings.connectors_required:
        state = PostgresConnectorStateStore(
            settings.postgres_dsn,
            settings.connector_max_staleness,
            namespace=settings.openfga_store_id,
        )
        freshness = state
    else:
        freshness = AlwaysFresh()
    engine = OpenFgaEngine.connect(
        settings.openfga_url,
        settings.openfga_store_id,
        freshness,
        authorization_model_id=settings.openfga_model_id,
    )

    identity = build_identity(settings)

    revocations = PostgresRevocationStore(settings.postgres_dsn)
    tokens = TokenService(
        keyring, revocations, audit, key_id=settings.root_key_id, ttl=settings.token_ttl
    )

    policy: DecisionPolicy | None = (
        RequireApproval(list(settings.approval_tools)) if settings.approval_tools else None
    )
    metrics = DecisionMetrics(telemetry.meter_provider if telemetry else None)
    decider = Decider(engine, audit, policy=policy, metrics=metrics)
    approvals = PostgresApprovalStore(settings.postgres_dsn)
    gate = ActionGate(
        decider,
        approvals,
        audit,
        approval_ttl=settings.approval_ttl,
        allow_self_approval=settings.allow_self_approval,
        channels=build_channels(settings.env),
        metrics=metrics,
    )

    ledger = PostgresLedger(settings.postgres_dsn)
    mandate_store = PostgresMandateStore(settings.postgres_dsn)
    mandates = MandateService(
        mandate_store,
        tokens,
        engine,
        audit,
        max_days=settings.mandate_max_days,
        default_max_token_ttl=settings.token_ttl,
    )
    memory = MemoryService(
        engine=engine,
        tuples=engine,
        ledger=ledger,
        adapter=build_memory_adapter(settings),
        decider=decider,
        audit=audit,
        namespace=settings.memory_namespace,
    )
    return Services(
        settings=settings,
        keyring=keyring,
        audit=audit,
        engine=engine,
        freshness=freshness,
        identity=identity,
        agent_detection=AgentDetection.from_settings(
            settings.oidc_agent_claim,
            settings.oidc_agent_id_claim,
            keycloak_fallback=settings.identity_provider == "keycloak",
        ),
        tokens=tokens,
        decider=decider,
        gate=gate,
        memory=memory,
        mandates=mandates,
        readiness=ReadinessProbe(
            postgres=lambda: ping(settings.postgres_dsn),
            engine=engine.ping,
            freshness=freshness.assert_fresh,
            latest=LATEST,
        ),
        telemetry=telemetry,
    )


def build_keyring(settings: Settings) -> KeyRing:
    """Signing key from HOC_ROOT_KEY_HEX, verify-only keys from HOC_ROOT_PUBLIC_KEYS."""
    if settings.root_key_hex:
        ring = KeyRing.from_private_key_bytes(
            settings.root_key_id, bytes.fromhex(settings.root_key_hex)
        )
    else:
        if not settings.allow_ephemeral_root_key:
            raise ConfigurationError(
                "HOC_ROOT_KEY_HEX is not set: every restart would invalidate every token. "
                "Generate one with `hoc keys generate`, or set HOC_ALLOW_EPHEMERAL_ROOT_KEY=true "
                "for a throwaway development instance."
            )
        log.warning("HOC_ROOT_KEY_HEX not set: generating an ephemeral root key (dev only)")
        ring = KeyRing()
        ring.add_private_key(settings.root_key_id, biscuit_auth.KeyPair().private_key)
    for key_id, hex_key in settings.root_public_keys:
        if key_id == settings.root_key_id:
            continue  # the signer's own public key is already there
        ring.add_public_key(key_id, KeyRing.public_key_from_bytes(bytes.fromhex(hex_key)))
    return ring


def build_identity(
    settings: Settings, *, plugins: dict[str, Factory] | None = None
) -> IdentityProvider:
    """``HOC_IDENTITY_PROVIDER``: keycloak (default), oidc, or a plugin (ADR 0019)."""
    config = settings.oidc_config()
    builtins: dict[str, Factory] = {
        "keycloak": lambda _settings: KeycloakProvider(config),
        "oidc": lambda _settings: OidcProvider(config),
    }
    factory = resolve(settings.identity_provider, builtins, plugins, "identity_providers")
    if factory is None:
        raise ConfigurationError(f"unknown identity provider {settings.identity_provider!r}")
    provider: IdentityProvider = factory(settings)
    return provider


def build_memory_adapter(
    settings: Settings, *, plugins: dict[str, Factory] | None = None
) -> MemoryAdapter:
    """``HOC_MEMORY_BACKEND``: inmemory, mem0, zep, or a plugin fed the environment (ADR 0019)."""
    builtins: dict[str, Factory] = {
        "inmemory": lambda _env: _inmemory_adapter(),
        "postgres": lambda _env: PostgresMemoryAdapter(
            settings.postgres_dsn, embedder=_embedder(settings)
        ),
        "mem0": lambda _env: _mem0_adapter(settings),
        "zep": lambda _env: _zep_adapter(settings),
    }
    factory = resolve(settings.memory_backend, builtins, plugins, "memory_adapters")
    if factory is None:
        raise ConfigurationError(f"unknown memory backend {settings.memory_backend!r}")
    adapter: MemoryAdapter = factory(settings.env)
    return adapter


def _embedder(settings: Settings) -> Embedder | None:
    """``HOC_MEMORY_EMBEDDER=package.module:callable``: the operator's model, never ours."""
    if not settings.memory_embedder:
        return None
    module_name, sep, attr = settings.memory_embedder.partition(":")
    try:
        target = getattr(importlib.import_module(module_name), attr) if sep else None
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(
            f"HOC_MEMORY_EMBEDDER: cannot import {settings.memory_embedder!r}"
        ) from exc
    if not callable(target):
        raise ConfigurationError("HOC_MEMORY_EMBEDDER must be `package.module:callable`")
    embedder: Embedder = target
    return embedder


def _inmemory_adapter() -> MemoryAdapter:
    log.warning(
        "HOC_MEMORY_BACKEND=inmemory: memory content lives in this process only and is lost "
        "on restart; configure postgres, mem0 or zep for anything beyond a demo (ADR 0018)"
    )
    return InMemoryMemoryAdapter()


def _mem0_adapter(settings: Settings) -> MemoryAdapter:
    from headofcontext.memory.mem0 import Mem0Adapter  # noqa: PLC0415

    config = json.loads(settings.mem0_config) if settings.mem0_config else None
    if config is not None and not isinstance(config, dict):
        raise ConfigurationError("HOC_MEM0_CONFIG must be a JSON object")
    return Mem0Adapter.from_config(config)


def _zep_adapter(settings: Settings) -> MemoryAdapter:
    from headofcontext.memory.zep import ZepAdapter  # noqa: PLC0415

    if not settings.zep_api_key:
        raise ConfigurationError("HOC_ZEP_API_KEY is required for the zep memory backend")
    extra = {"base_url": settings.zep_base_url} if settings.zep_base_url else {}
    return ZepAdapter.from_api_key(settings.zep_api_key, **extra)
