"""`headofcontext.db` against the real PostgreSQL (ADR 0020)."""

from __future__ import annotations

import psycopg
import pytest

from headofcontext.db import LATEST, SchemaOutOfDate, assert_current, current_version, migrate

pytestmark = pytest.mark.integration


def test_migrate_is_idempotent_and_assert_current_fails_closed(pg_dsn: str) -> None:
    applied = migrate(pg_dsn)
    assert current_version(pg_dsn) == LATEST
    assert migrate(pg_dsn) == []
    assert_current(pg_dsn)
    # A database behind the code: the service must refuse to start (fail closed).
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (LATEST,))
    try:
        assert current_version(pg_dsn) == LATEST - 1
        with pytest.raises(SchemaOutOfDate):
            assert_current(pg_dsn)
    finally:
        assert migrate(pg_dsn) == [LATEST]
    assert applied == [] or applied[-1] == LATEST


async def test_service_starts_without_ddl_rights(pg_dsn: str, keycloak_url: str) -> None:
    """ADR 0020: with HOC_DB_AUTO_MIGRATE=false the service and the CLI never run DDL, so a
    role with DML only is enough for the replicas and the sync CronJob."""
    import uuid
    from dataclasses import replace

    from headofcontext.api import Settings, build_services
    from headofcontext.cli.main import _prepare_schema

    migrate(pg_dsn)
    role = f"hoc_dml_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'dml'")
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("test role needs CREATEROLE")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
        )
        conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    dml_dsn = pg_dsn.replace("hoc:hoc@", f"{role}:dml@")
    try:
        with (
            psycopg.connect(dml_dsn, autocommit=True) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute("CREATE TABLE IF NOT EXISTS audit_events (seq BIGSERIAL PRIMARY KEY)")
        settings = replace(
            Settings(
                postgres_dsn=dml_dsn,
                openfga_url="http://127.0.0.1:1",
                openfga_store_id="unused",
                oidc_issuer=f"{keycloak_url}/realms/acme",
                oidc_audience="headofcontext",
                oidc_client_id="headofcontext",
                oidc_client_secret="hoc-dev-secret",
                connectors_required=True,
                memory_backend="postgres",
                allow_ephemeral_root_key=True,
            ),
            db_auto_migrate=False,
        )
        services = build_services(settings)
        await services.aclose()
        _prepare_schema(settings)  # what every CLI command does first: no DDL, just the check
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(f"REASSIGN OWNED BY {role} TO hoc")
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")


def test_concurrent_migrations_on_an_empty_database_all_succeed(pg_dsn: str) -> None:
    """Two replicas booting with auto-migrate must not race on the ledger creation."""
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    name = f"hoc_mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(f"CREATE DATABASE {name}")
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("needs CREATEDB")
    dsn = pg_dsn.rsplit("/", 1)[0] + f"/{name}"
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(migrate, [dsn] * 4))
        assert sorted(len(r) for r in results) == [0, 0, 0, LATEST]
        assert current_version(dsn) == LATEST
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


def test_db_roles_script_gives_the_runtime_role_exactly_what_the_code_needs(pg_dsn: str) -> None:
    """ADR 0020 (third amendment): the script applies after `hoc db migrate`, is idempotent,
    grants no DELETE on revocations or mandates, and tables created later by the migrator role
    are visible to the runtime role."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "scripts" / "db-roles.sql").read_text()
    migrate(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(script)
            conn.execute(script)  # idempotent
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("needs CREATEROLE")
        conn.execute("ALTER ROLE hoc_runtime PASSWORD 'rt'")
        conn.execute("ALTER ROLE hoc_migrator PASSWORD 'mg'")
    runtime = pg_dsn.replace("hoc:hoc@", "hoc_runtime:rt@")
    migrator = pg_dsn.replace("hoc:hoc@", "hoc_migrator:mg@")
    try:
        with psycopg.connect(runtime, autocommit=True) as conn:
            conn.execute("INSERT INTO token_revocations VALUES ('rt-x', 'test', now())")
            for forbidden in (
                "DELETE FROM token_revocations WHERE revocation_id = 'rt-x'",
                "DELETE FROM mandates",
                "UPDATE token_revocations SET reason = 'x'",
                "TRUNCATE audit_events",
                "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update",
                "CREATE TABLE sneaky (x int)",
            ):
                refused = (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)
                with pytest.raises(refused):
                    conn.execute(forbidden)
            conn.execute("SELECT count(*) FROM audit_events")  # SELECT + INSERT on the journal
        with psycopg.connect(migrator, autocommit=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS future_table (id int PRIMARY KEY)")
        with psycopg.connect(runtime, autocommit=True) as conn:
            conn.execute("INSERT INTO future_table VALUES (1)")
            assert conn.execute("SELECT count(*) FROM future_table").fetchone() == (1,)
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS future_table")
            for role in ("hoc_runtime", "hoc_migrator"):
                conn.execute(f"REASSIGN OWNED BY {role} TO hoc")
                conn.execute(f"DROP OWNED BY {role}")
                conn.execute(f"DROP ROLE IF EXISTS {role}")


def test_truncate_is_refused_even_for_the_owner(pg_dsn: str) -> None:
    migrate(pg_dsn)
    with (
        psycopg.connect(pg_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        conn.execute("TRUNCATE audit_events")


async def test_schema_ahead_of_the_code_is_refused_and_not_ready(pg_dsn: str) -> None:
    """ADR 0026: a rollback under a newer schema must not run silently."""
    from dataclasses import replace

    import httpx

    from headofcontext.api import Settings, build_services, create_app

    migrate(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, now())",
            (LATEST + 1, "from-the-future"),
        )
    try:
        with pytest.raises(SchemaOutOfDate, match="newer"):
            assert_current(pg_dsn)
        with pytest.raises(SchemaOutOfDate, match="newer"):
            migrate(pg_dsn)
        settings = replace(
            Settings(
                postgres_dsn=pg_dsn,
                openfga_url="http://127.0.0.1:1",
                openfga_store_id="unused",
                oidc_issuer="http://idp/realms/acme",
                oidc_client_id="hoc",
                oidc_client_secret="s",
                connectors_required=False,
                allow_ephemeral_root_key=True,
            ),
            db_auto_migrate=True,
        )
        with pytest.raises(SchemaOutOfDate):
            build_services(settings)
        services = build_services(replace(settings, db_auto_migrate=False)) if False else None
        # Readiness reports it as `ahead` even for a running instance that was migrated before.
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version = %s", (LATEST + 1,))
        services = build_services(replace(settings, db_auto_migrate=False))
        app = create_app(settings, services)
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, now())",
                (LATEST + 1, "from-the-future"),
            )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://hoc"
            ) as client:
                response = await client.get("/v1/ready")
                assert response.status_code == 503
                assert response.json()["checks"]["schema"] == "ahead"
        finally:
            await services.aclose()
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version = %s", (LATEST + 1,))
