"""``hoc``: operate a HeadOfContext deployment from the shell (ADR 0012)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import biscuit_auth
import httpx

from headofcontext.cli.env import load_dotenv, write_env
from headofcontext.core.errors import ConfigurationError, HocError
from headofcontext.logging import configure_logging
from headofcontext.settings import REQUIRE_DB, REQUIRE_ENGINE

if TYPE_CHECKING:
    from mcp import ClientSession

    from headofcontext.integrations import AgentSession
    from headofcontext.services import Services
    from headofcontext.settings import Settings

log = logging.getLogger("hoc")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(Path(args.env_file))
    configure_logging(
        os.environ.get("HOC_LOG_LEVEL", "INFO"), os.environ.get("HOC_LOG_FORMAT", "text")
    )
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:
        return 130
    except HocError as exc:
        # Operator mistakes and dependency outages are reported, never dumped as tracebacks.
        print(f"{exc.reason}: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hoc", description="HeadOfContext operations")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load (default: .env)")
    sub = parser.add_subparsers(dest="command")

    model = sub.add_parser("model", help="OpenFGA store and model").add_subparsers(dest="action")
    p = model.add_parser("load", help="create/reuse the store, write the model, record ids in .env")
    p.add_argument("--url", default=None, help="OpenFGA URL (default: HOC_OPENFGA_URL)")
    p.add_argument("--store-name", default="headofcontext")
    p.add_argument("--no-write-env", action="store_true")
    p.set_defaults(handler=cmd_model_load)

    fixtures = sub.add_parser("fixtures", help="ACME fixtures (dev)").add_subparsers(dest="action")
    p = fixtures.add_parser("load", help="write ACME tuples and policy into the store")
    p.add_argument("--dir", default="fixtures/acme/generated")
    p.set_defaults(handler=cmd_fixtures_load)

    p = sub.add_parser("sync", help="run the connectors, then sweep expired approvals")
    p.add_argument("--config", default="hoc.connectors.json")
    p.add_argument("--once", action="store_true", help="run once and exit")
    p.add_argument("--interval", type=float, default=300.0, help="seconds between runs")
    p.set_defaults(handler=cmd_sync)

    _add_journal_parser(sub)

    _add_approvals_parser(sub)
    _add_mandates_parser(sub)

    db = sub.add_parser("db", help="schema migrations (ADR 0020)").add_subparsers(dest="action")
    p = db.add_parser("migrate", help="apply pending migrations")
    p.set_defaults(handler=cmd_db_migrate)
    p = db.add_parser("status", help="print the applied and expected schema versions")
    p.set_defaults(handler=cmd_db_status)

    keys = sub.add_parser("keys", help="root key material").add_subparsers(dest="action")
    p = keys.add_parser("generate", help="print a new Ed25519 private key (hex)")
    p.set_defaults(handler=cmd_keys_generate)
    p = keys.add_parser("public", help="print the public key (hex) of a private key")
    p.add_argument("--key-hex", default=None, help="private key hex (default: HOC_ROOT_KEY_HEX)")
    p.set_defaults(handler=cmd_keys_public)

    token = sub.add_parser("token", help="dev helpers around biscuits").add_subparsers(
        dest="action"
    )
    p = token.add_parser("issue", help="password grant + /v1/tokens/issue; prints the biscuit")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--issuer", default=None, help="OIDC issuer (default: HOC_OIDC_ISSUER)")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--agent", default="assistant", help="agent client id")
    p.add_argument("--agent-secret", default=None, help="default: <agent>-dev-secret")
    p.add_argument(
        "--scope", default="read=document:*,memory:*;act=tool:*;remember=document:*,memory:*"
    )
    p.set_defaults(handler=cmd_token_issue)

    _add_mcp_parser(sub)
    return parser


def _add_journal_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    journal = sub.add_parser("journal", help="audit journal").add_subparsers(dest="action")
    p = journal.add_parser("tail", help="print the latest events")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--follow", "-f", action="store_true")
    p.set_defaults(handler=cmd_journal_tail)
    p = journal.add_parser("verify", help="recompute the hash chain; exit 1 on a break")
    p.add_argument("--anchor", default=None, help="expected prev_hash of the first row (GENESIS)")
    p.add_argument("--archive", default=None, help="verify an exported JSON-lines file instead")
    p.set_defaults(handler=cmd_journal_verify)
    p = journal.add_parser("export", help="write events as JSON lines (verifiable archive)")
    p.add_argument("--since-seq", type=int, default=0, help="first seq excluded (default 0)")
    p.add_argument("--until-seq", type=int, default=None, help="last seq included")
    p.add_argument("--out", default="-", help="file path, or - for stdout")
    p.set_defaults(handler=cmd_journal_export)


def _add_approvals_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    approvals = sub.add_parser("approvals", help="human approvals").add_subparsers(dest="action")
    p = approvals.add_parser("list", help="pending requests")
    p.set_defaults(handler=cmd_approvals_list)
    for name, approved in (("approve", True), ("reject", False)):
        p = approvals.add_parser(name)
        p.add_argument("request_id")
        p.add_argument("--approver", required=True, help="user:<id> who decides")
        p.add_argument("--reason", default="")
        p.set_defaults(handler=cmd_approvals_resolve, approved=approved)
    p = approvals.add_parser("sweep", help="mark overdue pending requests expired")
    p.set_defaults(handler=cmd_approvals_sweep)


def _add_mandates_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mandates = sub.add_parser("mandates", help="standing mandates (ADR 0016)").add_subparsers(
        dest="action"
    )
    p = mandates.add_parser("list", help="mandates of a subject")
    p.add_argument("--subject", required=True, help="user:<id>")
    p.set_defaults(handler=cmd_mandates_list)
    p = mandates.add_parser("revoke", help="revoke a mandate and every token under it")
    p.add_argument("mandate_id")
    p.add_argument("--by", required=True, help="user:<id>, must be the subject")
    p.add_argument("--reason", default="revoked from the CLI")
    p.set_defaults(handler=cmd_mandates_revoke)
    p = mandates.add_parser("sweep", help="mark overdue mandates expired")
    p.set_defaults(handler=cmd_mandates_sweep)


def _add_mcp_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mcp = sub.add_parser("mcp", help="Model Context Protocol (ADR 0013)").add_subparsers(
        dest="action"
    )
    p = mcp.add_parser(
        "proxy", help="stdio MCP server gating the tools of one or several upstream servers"
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--upstream-stdio", help="command line of the upstream stdio server")
    group.add_argument("--upstream-http", help="URL of the upstream streamable HTTP server")
    group.add_argument(
        "--upstream",
        action="append",
        metavar="NAME=TARGET",
        help="a named upstream (repeatable): TARGET is a streamable HTTP URL or a stdio command "
        "line; tools are exposed as NAME__tool and gated as tool:NAME/tool",
    )
    p.add_argument(
        "--tool-map",
        action="append",
        default=[],
        metavar="NAME=TOOL",
        help="map an MCP tool name (NAME__tool with --upstream) to a tool resource",
    )
    p.set_defaults(handler=cmd_mcp_proxy)
    p = mcp.add_parser("serve", help="expose HeadOfContext itself as an MCP server")
    p.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.set_defaults(handler=cmd_mcp_serve)


# -- helpers --------------------------------------------------------------------------------------


def _settings(need: frozenset[str] | None = None) -> Settings:
    """Settings for a command; ``need`` narrows what must be present (default: everything)."""
    from headofcontext.settings import REQUIRE_SERVICE, Settings  # noqa: PLC0415

    return Settings.from_env(require=need if need is not None else REQUIRE_SERVICE)


def _prepare_schema(settings: Settings) -> None:
    """Same policy as the service (ADR 0020): migrate when HOC_DB_AUTO_MIGRATE, else require
    the schema to be current. Commands never run DDL on their own."""
    from headofcontext.db import assert_current, migrate  # noqa: PLC0415

    if settings.db_auto_migrate:
        migrate(settings.postgres_dsn)
    else:
        assert_current(settings.postgres_dsn)


def _missing_from_example(env_path: Path, example: Path) -> dict[str, str]:
    present = {
        line.split("=", 1)[0].strip()
        for line in (env_path.read_text().splitlines() if env_path.is_file() else [])
        if "=" in line and not line.startswith("#")
    }
    missing: dict[str, str] = {}
    for raw in example.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() not in present and key.strip() not in os.environ:
            missing[key.strip()] = value.strip()
    return missing


def _openfga_url(args: argparse.Namespace) -> str:

    url = getattr(args, "url", None) or os.environ.get("HOC_OPENFGA_URL")
    if not url:
        raise SystemExit("set HOC_OPENFGA_URL or pass --url")
    return str(url)


def _write_tuples(client: httpx.Client, store_id: str, tuples: list[dict[str, str]]) -> int:
    written = 0
    for start in range(0, len(tuples), 100):
        chunk = tuples[start : start + 100]
        response = client.post(f"/stores/{store_id}/write", json={"writes": {"tuple_keys": chunk}})
        if response.status_code == 400 and "already exists" in response.text:
            for t in chunk:
                single = client.post(
                    f"/stores/{store_id}/write", json={"writes": {"tuple_keys": [t]}}
                )
                written += single.status_code == 200
            continue
        response.raise_for_status()
        written += len(chunk)
    return written


# -- commands -------------------------------------------------------------------------------------


def cmd_model_load(args: argparse.Namespace) -> int:
    model = json.loads(
        resources.files("headofcontext.engines.openfga").joinpath("authz-model.json").read_text()
    )
    with httpx.Client(base_url=_openfga_url(args), timeout=30) as client:
        stores = client.get("/stores").json().get("stores", [])
        store_id = next((str(s["id"]) for s in stores if s["name"] == args.store_name), None)
        if store_id is None:
            store_id = str(client.post("/stores", json={"name": args.store_name}).json()["id"])
        response = client.post(f"/stores/{store_id}/authorization-models", json=model)
        response.raise_for_status()
        model_id = str(response.json()["authorization_model_id"])
    print(f"store={store_id} model={model_id}")
    if not args.no_write_env:
        example = Path(args.env_file).with_name(".env.example")
        if example.is_file():
            # First run on a workstation: the dev values the compose stack expects, once.
            write_env(Path(args.env_file), _missing_from_example(Path(args.env_file), example))
        write_env(
            Path(args.env_file),
            {"HOC_OPENFGA_STORE_ID": store_id, "HOC_OPENFGA_MODEL_ID": model_id},
        )
        print(f"recorded in {args.env_file}")
    return 0


def cmd_fixtures_load(args: argparse.Namespace) -> int:
    settings = _settings(REQUIRE_ENGINE)
    base = Path(args.dir)
    tuples = json.loads((base / "tuples.json").read_text())
    with httpx.Client(base_url=settings.openfga_url, timeout=30) as client:
        written = _write_tuples(client, settings.openfga_store_id, tuples)
    print(f"tuples written: {written}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from headofcontext.actions import PostgresApprovalStore  # noqa: PLC0415
    from headofcontext.audit import PostgresAuditSink  # noqa: PLC0415
    from headofcontext.cli.connectors import build_connectors, load_config  # noqa: PLC0415
    from headofcontext.engines.openfga import OpenFgaEngine  # noqa: PLC0415
    from headofcontext.read.connectors import (  # noqa: PLC0415
        PostgresConnectorStateStore,
        reconcile,
    )
    from headofcontext.tokens.mandates import PostgresMandateStore  # noqa: PLC0415

    settings = _settings()
    connectors = build_connectors(load_config(Path(args.config)))
    _prepare_schema(settings)
    audit = PostgresAuditSink(settings.postgres_dsn)
    state = PostgresConnectorStateStore(
        settings.postgres_dsn,
        settings.connector_max_staleness,
        namespace=settings.openfga_store_id,
    )
    approvals = PostgresApprovalStore(settings.postgres_dsn)
    mandate_store = PostgresMandateStore(settings.postgres_dsn)

    async def run_once(engine: OpenFgaEngine) -> int:
        failures = 0
        for connector in connectors:
            try:
                report = await reconcile(connector, tuples=engine, state=state, audit=audit)
                log.info(
                    "%s: +%d -%d, %d documents",
                    report.connector,
                    report.added,
                    report.removed,
                    report.documents,
                )
            except Exception as exc:
                failures += 1
                log.error("%s failed: %s", connector.name, exc)
        expired = approvals.expire_pending(datetime.now(UTC))
        if expired:
            log.info("approvals expired: %d", expired)
        mandates_expired = len(mandate_store.expire_active(datetime.now(UTC)))
        if mandates_expired:
            log.info("mandates expired: %d", mandates_expired)
        return failures

    async def run_loop() -> int:
        # The OpenFGA client owns an aiohttp session: it must be created inside the event loop.
        engine = OpenFgaEngine.connect(
            settings.openfga_url,
            settings.openfga_store_id,
            state,
            authorization_model_id=settings.openfga_model_id,
        )
        try:
            while True:
                failures = await run_once(engine)
                if args.once:
                    return 1 if failures else 0
                await asyncio.sleep(args.interval)
        finally:
            await engine.close()

    return asyncio.run(run_loop())


def cmd_journal_tail(args: argparse.Namespace) -> int:
    import psycopg  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    query = (
        "SELECT seq, ts, kind, subject, actor, action, resource, outcome, reason FROM audit_events "
        "WHERE seq > %s ORDER BY seq {order} LIMIT %s"
    )
    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        rows = conn.execute(query.format(order="DESC"), (0, args.n)).fetchall()
        last = 0
        for row in reversed(rows):
            _print_event(row)
            last = max(last, int(str(row[0])))
        while args.follow:
            time.sleep(1.0)
            for row in conn.execute(query.format(order="ASC"), (last, 200)).fetchall():
                _print_event(row)
                last = max(last, int(str(row[0])))
    return 0


def cmd_journal_verify(args: argparse.Namespace) -> int:

    from headofcontext.audit import PostgresAuditSink, verify_rows  # noqa: PLC0415
    from headofcontext.core.events import GENESIS_HASH  # noqa: PLC0415

    anchor = args.anchor or GENESIS_HASH
    if args.archive:
        lines = (json.loads(line) for line in Path(args.archive).read_text().splitlines() if line)
        rows = (
            (
                int(r["seq"]),
                json.dumps(r["event"], sort_keys=True, separators=(",", ":")),
                str(r["prev_hash"]),
                str(r["hash"]),
            )
            for r in lines
        )
        report = verify_rows(rows, anchor=anchor)
    else:
        settings = _settings(REQUIRE_DB)
        _prepare_schema(settings)
        sink = PostgresAuditSink(settings.postgres_dsn)
        report = sink.verify_chain_report(anchor=anchor)
    span = f"seq {report.first_seq}..{report.last_seq}" if report.checked else "empty journal"
    if report.ok:
        print(
            f"chain ok: {report.checked} events, {span}, anchor {report.anchor}, head {report.head}"
        )
        return 0
    print(f"chain BROKEN at seq {report.break_at}: {report.detail} ({span})", file=sys.stderr)
    return 1


def cmd_journal_export(args: argparse.Namespace) -> int:

    from headofcontext.audit import PostgresAuditSink  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    sink = PostgresAuditSink(settings.postgres_dsn)
    count, head = 0, None
    with sys.stdout if args.out == "-" else Path(args.out).open("w", encoding="utf-8") as out:
        for row in sink.export(since_seq=args.since_seq, until_seq=args.until_seq):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
            head = row["hash"]
    print(f"exported: {count}, head {head}", file=sys.stderr)
    return 0


def _print_event(row: tuple[object, ...]) -> None:
    seq, ts, kind, subject, actor, action, resource, outcome, reason = row
    stamp = ts.isoformat(timespec="seconds") if isinstance(ts, datetime) else str(ts)
    head = f"{seq:>8} {stamp} {kind!s:16} {outcome!s:17}"
    print(f"{head} {subject} via {actor} {action} {resource} · {reason}")


def cmd_db_migrate(args: argparse.Namespace) -> int:
    from headofcontext.db import LATEST, migrate  # noqa: PLC0415

    applied = migrate(_settings(REQUIRE_DB).postgres_dsn)
    print(f"applied: {applied or 'nothing'} (schema at {LATEST})")
    return 0


def cmd_db_status(args: argparse.Namespace) -> int:
    from headofcontext.db import LATEST, current_version  # noqa: PLC0415

    version = current_version(_settings(REQUIRE_DB).postgres_dsn)
    print(f"schema: {version} applied, {LATEST} expected")
    return 0 if version >= LATEST else 1


def cmd_approvals_list(args: argparse.Namespace) -> int:
    from headofcontext.actions import PostgresApprovalStore  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    store = PostgresApprovalStore(settings.postgres_dsn)
    for r in store.list_pending():
        expires = r.expires_at.isoformat(timespec="minutes")
        print(f"{r.request_id} {r.tool} for {r.subject} via {r.actor} · expires {expires}")
    return 0


def cmd_approvals_resolve(args: argparse.Namespace) -> int:
    from headofcontext.actions import (  # noqa: PLC0415
        PostgresApprovalStore,
        authorize_approver,
        resolve_request,
    )
    from headofcontext.audit import PostgresAuditSink  # noqa: PLC0415
    from headofcontext.core.errors import ApprovalError  # noqa: PLC0415
    from headofcontext.engines.freshness import AlwaysFresh  # noqa: PLC0415
    from headofcontext.engines.openfga import OpenFgaEngine  # noqa: PLC0415

    settings = _settings(REQUIRE_ENGINE)
    _prepare_schema(settings)
    store = PostgresApprovalStore(settings.postgres_dsn)
    audit = PostgresAuditSink(settings.postgres_dsn)
    now = datetime.now(UTC)

    async def run() -> int:
        # The approver is checked in OpenFGA here exactly as on the API: no operator bypass
        # (ADR 0017). Connector freshness is irrelevant to an `approver` tuple, hence AlwaysFresh.
        engine = OpenFgaEngine.connect(
            settings.openfga_url,
            settings.openfga_store_id,
            AlwaysFresh(),
            authorization_model_id=settings.openfga_model_id,
        )
        try:
            request = store.get(args.request_id)
            if request is None:
                print(f"unknown request {args.request_id}", file=sys.stderr)
                return 1
            await authorize_approver(
                engine,
                audit,
                now,
                request,
                args.approver,
                allow_self_approval=settings.allow_self_approval,
            )
        finally:
            await engine.close()
        resolved = resolve_request(
            store,
            audit,
            now,
            args.request_id,
            approver=args.approver,
            approved=args.approved,
            reason=args.reason,
            allow_self_approval=settings.allow_self_approval,
        )
        print(f"{resolved.request_id} {resolved.status} by {resolved.resolved_by}")
        return 0

    try:
        return asyncio.run(run())
    except ApprovalError as exc:
        print(f"refused: {exc.reason}", file=sys.stderr)
        return 1


def cmd_approvals_sweep(args: argparse.Namespace) -> int:
    from headofcontext.actions import PostgresApprovalStore  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    store = PostgresApprovalStore(settings.postgres_dsn)
    print(f"expired: {store.expire_pending(datetime.now(UTC))}")
    return 0


def cmd_mandates_list(args: argparse.Namespace) -> int:
    from headofcontext.tokens.mandates import PostgresMandateStore  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    store = PostgresMandateStore(settings.postgres_dsn)
    for m in store.list_for(args.subject):
        caps = ",".join(f"{c.kind}={c.resource}" for c in sorted(m.scope))
        print(
            f"{m.mandate_id} {m.status} {m.agent} for {m.subject} "
            f"until {m.expires_at.isoformat(timespec='minutes')} [{caps}]"
        )
    return 0


def cmd_mandates_revoke(args: argparse.Namespace) -> int:
    from headofcontext.audit import PostgresAuditSink  # noqa: PLC0415
    from headofcontext.services import build_keyring  # noqa: PLC0415
    from headofcontext.tokens.biscuit import (  # noqa: PLC0415
        KeyRing,
        PostgresRevocationStore,
        TokenService,
    )
    from headofcontext.tokens.mandates import MandateService, PostgresMandateStore  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    audit = PostgresAuditSink(settings.postgres_dsn)
    store = PostgresMandateStore(settings.postgres_dsn)
    revocations = PostgresRevocationStore(settings.postgres_dsn)
    # Revoking needs no signing key: an operator without HOC_ROOT_KEY_HEX may still revoke.
    keyring = build_keyring(settings) if settings.root_key_hex else KeyRing()
    tokens = TokenService(keyring, revocations, audit, key_id=settings.root_key_id)
    # Revocation never consults the engine; None keeps the CLI free of an event loop.
    service = MandateService(store, tokens, None, audit)
    revoked = asyncio.run(service.revoke(args.mandate_id, by=args.by, reason=args.reason))
    print(f"revoked: {revoked.mandate_id} ({revoked.agent} for {revoked.subject})")
    return 0


def cmd_mandates_sweep(args: argparse.Namespace) -> int:
    from headofcontext.tokens.mandates import PostgresMandateStore  # noqa: PLC0415

    settings = _settings(REQUIRE_DB)
    _prepare_schema(settings)
    store = PostgresMandateStore(settings.postgres_dsn)
    print(f"expired: {len(store.expire_active(datetime.now(UTC)))}")
    return 0


def cmd_keys_generate(args: argparse.Namespace) -> int:
    print(biscuit_auth.KeyPair().private_key.to_bytes().hex())
    return 0


def cmd_keys_public(args: argparse.Namespace) -> int:

    from headofcontext.tokens.biscuit import KeyRing  # noqa: PLC0415

    key_hex = args.key_hex or os.environ.get("HOC_ROOT_KEY_HEX")
    if not key_hex:
        raise SystemExit("pass --key-hex or set HOC_ROOT_KEY_HEX")
    print(KeyRing.from_private_key_bytes(0, bytes.fromhex(key_hex)).public_key_bytes(0).hex())
    return 0


def cmd_token_issue(args: argparse.Namespace) -> int:

    issuer = args.issuer or os.environ.get("HOC_OIDC_ISSUER")
    if not issuer:
        raise SystemExit("set HOC_OIDC_ISSUER or pass --issuer")
    with httpx.Client(timeout=15) as http:
        user = http.post(
            f"{issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": os.environ.get("HOC_OIDC_CLIENT_ID", "headofcontext"),
                "client_secret": os.environ.get("HOC_OIDC_CLIENT_SECRET", "hoc-dev-secret"),
                "username": args.username,
                "password": args.password,
                "scope": "openid",
            },
        )
        user.raise_for_status()
        agent = http.post(
            f"{issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": args.agent,
                "client_secret": args.agent_secret or f"{args.agent}-dev-secret",
            },
        )
        agent.raise_for_status()
        capabilities = []
        for part in args.scope.split(";"):
            kind, _, resources_ = part.partition("=")
            capabilities += [
                {"kind": kind.strip(), "resource": r.strip()}
                for r in resources_.split(",")
                if r.strip()
            ]
        issued = http.post(
            f"{args.api}/v1/tokens/issue",
            headers={"Authorization": f"Bearer {agent.json()['access_token']}"},
            json={
                "user_token": user.json()["access_token"],
                "scope": {"capabilities": capabilities},
            },
        )
        if issued.status_code != 200:
            print(issued.text, file=sys.stderr)
            return 1
    print(issued.json()["token"])
    return 0


# -- mcp ------------------------------------------------------------------------------------------


def _mcp_session(services: Services) -> AgentSession:
    """The proxy and the server act for one user: the biscuit and the caller come from the env."""

    from headofcontext.integrations import AgentSession  # noqa: PLC0415

    token = os.environ.get("HOC_MCP_TOKEN")
    caller = os.environ.get("HOC_MCP_AGENT")
    if not token or not caller:
        raise SystemExit("set HOC_MCP_TOKEN (biscuit) and HOC_MCP_AGENT (agent:<id>)")
    return AgentSession(
        token=token, caller=caller, token_service=services.tokens, gate=services.gate
    )


def _tool_map(pairs: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        name, sep, tool = pair.partition("=")
        if not sep or not name or not tool:
            raise SystemExit(f"--tool-map expects NAME=TOOL, got {pair!r}")
        mapping[name] = tool
    return mapping


_UPSTREAM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _upstreams(pairs: Sequence[str]) -> dict[str, str]:
    """``NAME=TARGET`` pairs of ``--upstream``; the target is a URL or a stdio command line."""
    upstreams: dict[str, str] = {}
    for pair in pairs:
        name, sep, target = pair.partition("=")
        if not sep or not name or not target:
            raise ConfigurationError(f"--upstream expects NAME=TARGET, got {pair!r}")
        if not _UPSTREAM_NAME_RE.match(name) or "__" in name:
            raise ConfigurationError(
                f"invalid upstream name {name!r}: [a-z0-9][a-z0-9_-]*, no '__'"
            )
        if name in upstreams:
            raise ConfigurationError(f"duplicate upstream name {name!r}")
        upstreams[name] = target
    return upstreams


def _is_url(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def cmd_mcp_proxy(args: argparse.Namespace) -> int:
    import shlex  # noqa: PLC0415
    from contextlib import AsyncExitStack  # noqa: PLC0415

    from mcp import ClientSession  # noqa: PLC0415

    from headofcontext.integrations.mcp import GuardedMcpProxy  # noqa: PLC0415
    from headofcontext.services import build_services  # noqa: PLC0415

    # stdout is the MCP channel: everything else goes to stderr.
    tool_map = _tool_map(args.tool_map)
    # One anonymous upstream (tool names verbatim) or several named ones (prefixed names).
    targets: dict[str | None, tuple[bool, str]]
    if args.upstream:
        targets = {name: (_is_url(t), t) for name, t in _upstreams(args.upstream).items()}
    elif args.upstream_stdio:
        targets = {None: (False, args.upstream_stdio)}
    else:
        targets = {None: (True, args.upstream_http)}

    async def open_upstream(stack: AsyncExitStack, is_url: bool, target: str) -> ClientSession:
        if is_url:
            from mcp.client.streamable_http import streamable_http_client  # noqa: PLC0415

            read, write, *_ = await stack.enter_async_context(streamable_http_client(target))
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415

            command, *argv = shlex.split(target)
            read, write = await stack.enter_async_context(
                stdio_client(StdioServerParameters(command=command, args=argv))
            )
        client = await stack.enter_async_context(ClientSession(read, write))
        await client.initialize()
        return client

    async def run() -> int:
        services = build_services(_settings())
        session = _mcp_session(services)
        async with AsyncExitStack() as stack:
            clients = {
                name: await open_upstream(stack, is_url, target)
                for name, (is_url, target) in targets.items()
            }
            upstream: ClientSession | dict[str, ClientSession] = (
                clients[None]
                if None in clients
                else {name: client for name, client in clients.items() if name is not None}
            )
            try:
                await GuardedMcpProxy(session, upstream, tool_map=tool_map).run_stdio()
            finally:
                await services.aclose()
        return 0

    return asyncio.run(run())


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from headofcontext.integrations.mcp import HocMcpBinding, build_hoc_server  # noqa: PLC0415
    from headofcontext.services import build_services  # noqa: PLC0415

    async def run() -> int:
        # The OpenFGA client binds to the running loop: build everything inside it.
        services = build_services(_settings())
        try:
            server = build_hoc_server(
                HocMcpBinding(
                    session=_mcp_session(services),
                    engine=services.engine,
                    audit=services.audit,
                    memory=services.memory,
                )
            )
            if args.http:
                await server.run_streamable_http_async(host=args.host, port=args.port)
            else:
                await server.run_stdio_async()
        finally:
            await services.aclose()
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
