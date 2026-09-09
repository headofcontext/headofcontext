-- PostgreSQL roles for HeadOfContext (ADR 0020, third amendment). Pure SQL, idempotent.
--
-- Run AFTER `hoc db migrate` created the tables, as a superuser, on the target database:
--     psql "$ADMIN_DSN" -f scripts/db-roles.sql
--     psql "$ADMIN_DSN" -c "ALTER ROLE hoc_runtime PASSWORD '...'" -c "ALTER ROLE hoc_migrator PASSWORD '...'"
--
-- hoc_migrator owns future tables and runs `hoc db migrate` (Helm `migrations.secretName`).
-- hoc_runtime is what every replica and the sync CronJob use: exactly the data rights the code
-- needs. It cannot delete a revocation or a mandate (I4), cannot touch the journal beyond
-- INSERT/SELECT (the trigger refuses the rest and the role cannot disable it), and cannot run DDL.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hoc_migrator') THEN
        CREATE ROLE hoc_migrator LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hoc_runtime') THEN
        CREATE ROLE hoc_runtime LOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO hoc_migrator, hoc_runtime', current_database());
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO hoc_migrator;
GRANT USAGE ON SCHEMA public TO hoc_runtime;

-- Existing tables: what the code does, nothing more.
GRANT SELECT, INSERT ON token_revocations TO hoc_runtime;
GRANT SELECT, INSERT, UPDATE ON approval_requests, mandates, connector_state_v2 TO hoc_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON memory_provenance, memory_content TO hoc_runtime;
GRANT SELECT, INSERT ON audit_events TO hoc_runtime;
GRANT SELECT ON schema_migrations TO hoc_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hoc_runtime;
DO $$
BEGIN
    IF to_regclass('memory_embedding') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON memory_embedding TO hoc_runtime';
    END IF;
END $$;

-- The migrator must own what it will migrate, and future tables it creates must be readable and
-- writable (no DELETE by default: a migration that needs it grants it explicitly).
DO $$
DECLARE t text;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE %I OWNER TO hoc_migrator', t);
    END LOOP;
END $$;
ALTER DEFAULT PRIVILEGES FOR ROLE hoc_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO hoc_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE hoc_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO hoc_runtime;
