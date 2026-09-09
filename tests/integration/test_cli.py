"""`hoc` against the real stack: model load into .env, sync --once with the files connector,
journal tail, approvals sweep."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from headofcontext.cli import main
from tests.services import OPENFGA_URL

pytestmark = pytest.mark.integration


@pytest.fixture
def workdir(
    tmp_path: Path, pg_dsn: str, keycloak_url: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    from fixtures.acme.materialize import write_files_tree

    tree = write_files_tree(tmp_path / "files")
    (tmp_path / "hoc.connectors.json").write_text(
        json.dumps(
            [
                {"type": "files", "name": "files-cli", "root": str(tree)},
                {
                    "type": "keycloak_groups",
                    "base_url": keycloak_url,
                    "realm": "acme",
                    "admin_user": "admin",
                    "admin_password_env": "KC_TEST_PASSWORD",
                },
            ]
        )
    )
    monkeypatch.setenv("KC_TEST_PASSWORD", "admin")
    monkeypatch.setenv("HOC_POSTGRES_DSN", pg_dsn)
    monkeypatch.setenv("HOC_OPENFGA_URL", OPENFGA_URL)
    monkeypatch.setenv("HOC_OIDC_ISSUER", f"{keycloak_url}/realms/acme")
    monkeypatch.setenv("HOC_OIDC_CLIENT_ID", "headofcontext")
    monkeypatch.setenv("HOC_OIDC_CLIENT_SECRET", "hoc-dev-secret")
    monkeypatch.delenv("HOC_OPENFGA_STORE_ID", raising=False)
    monkeypatch.delenv("HOC_OPENFGA_MODEL_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_database_commands_work_with_the_dsn_alone(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR 0026: `hoc db status` and `hoc journal verify` need no OpenFGA or IdP settings."""
    for key in list(dict(__import__("os").environ)):
        if key.startswith("HOC_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOC_POSTGRES_DSN", pg_dsn)
    monkeypatch.chdir(tmp_path)
    assert main(["--env-file", "/dev/null", "db", "status"]) == 0
    assert "applied" in capsys.readouterr().out
    assert main(["--env-file", "/dev/null", "journal", "verify"]) == 0


def test_model_load_merges_the_example_env(
    workdir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0026: after `hoc model load`, .env carries what the compose stack needs."""
    monkeypatch.delenv("HOC_POSTGRES_DSN", raising=False)
    (workdir / ".env.example").write_text(
        "# dev values\nHOC_POSTGRES_DSN=postgresql://hoc:hoc@localhost:5433/hoc\n"
        "HOC_ALLOW_EPHEMERAL_ROOT_KEY=true\n"
    )
    store_name = f"hoc-cli-example-{workdir.name}"
    assert main(["model", "load", "--store-name", store_name]) == 0
    env = (workdir / ".env").read_text()
    assert "HOC_POSTGRES_DSN=postgresql://hoc:hoc@localhost:5433/hoc" in env
    assert "HOC_ALLOW_EPHEMERAL_ROOT_KEY=true" in env and "HOC_OPENFGA_STORE_ID=" in env
    store_id = next(
        line.split("=", 1)[1]
        for line in env.splitlines()
        if line.startswith("HOC_OPENFGA_STORE_ID=")
    )
    with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
        client.delete(f"/stores/{store_id}")


def test_model_load_sync_and_journal(
    workdir: Path, pg_dsn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    store_name = f"hoc-cli-{workdir.name}"
    assert main(["model", "load", "--store-name", store_name]) == 0
    env = (workdir / ".env").read_text()
    assert "HOC_OPENFGA_STORE_ID=" in env and "HOC_OPENFGA_MODEL_ID=" in env
    store_id = next(
        line.split("=", 1)[1]
        for line in env.splitlines()
        if line.startswith("HOC_OPENFGA_STORE_ID=")
    )
    try:
        # second run reuses the store
        assert main(["model", "load", "--store-name", store_name, "--no-write-env"]) == 0
        assert f"store={store_id}" in capsys.readouterr().out

        assert main(["sync", "--once", "--config", "hoc.connectors.json"]) == 0
        with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
            body = client.post(
                f"/stores/{store_id}/check",
                json={
                    "tuple_key": {
                        "user": "user:alice.martin",
                        "relation": "member",
                        "object": "group:direction",
                    }
                },
            ).json()
            assert body["allowed"] is True
            docs = client.post(
                f"/stores/{store_id}/read",
                json={"tuple_key": {"object": "document:acme-0001", "relation": "parent"}},
            ).json()
            assert docs["tuples"]

        assert main(["journal", "tail", "-n", "5"]) == 0
        out = capsys.readouterr().out
        assert "connector_synced" in out

        assert main(["approvals", "sweep"]) == 0
        assert "expired:" in capsys.readouterr().out

        # The operator revocation path: only the subject may revoke, and it kills the mandate.
        from datetime import UTC, datetime, timedelta

        from headofcontext.core import Capability, Kind, Scope
        from headofcontext.tokens.mandates import Mandate, MandateStatus, PostgresMandateStore

        mandate_store = PostgresMandateStore(pg_dsn)
        now = datetime.now(UTC)
        mandate = Mandate(
            mandate_id=f"cli-{now.timestamp()}",
            subject="user:alice.martin",
            agent="agent:assistant",
            scope=Scope.of(Capability(Kind.ACT, "tool:mail.*")),
            created_at=now,
            expires_at=now + timedelta(days=1),
            max_token_ttl=timedelta(minutes=15),
            created_by="user:alice.martin",
        )
        mandate_store.create(mandate)
        assert main(["mandates", "revoke", mandate.mandate_id, "--by", "user:bruno.bernard"]) == 1
        assert "not_subject" in capsys.readouterr().err
        assert main(["mandates", "revoke", mandate.mandate_id, "--by", "user:alice.martin"]) == 0
        assert "revoked:" in capsys.readouterr().out
        assert mandate_store.get(mandate.mandate_id).status is MandateStatus.REVOKED  # type: ignore[union-attr]
        assert main(["mandates", "list", "--subject", "user:alice.martin"]) == 0
        assert mandate.mandate_id in capsys.readouterr().out

        # ADR 0025: the journal can be verified and exported as a verifiable archive.
        assert main(["journal", "verify"]) == 0
        assert "chain ok" in capsys.readouterr().out
        archive = workdir / "journal.jsonl"
        assert main(["journal", "export", "--out", str(archive)]) == 0
        lines = [json.loads(line) for line in archive.read_text().splitlines()]
        assert lines and {"seq", "prev_hash", "hash", "event"} <= set(lines[0])
        # An archive verifies from GENESIS on its own, and its head anchors the live journal.
        assert main(["journal", "verify", "--archive", str(archive)]) == 0
        head = capsys.readouterr().out.strip().rsplit(" ", 1)[-1]
        assert head == lines[-1]["hash"]
        assert main(["journal", "verify", "--anchor", "deadbeef" * 8]) == 1
        assert "BROKEN" in capsys.readouterr().err

        # ADR 0017: the operator command checks the approver in OpenFGA, like the API does.
        from datetime import UTC, datetime, timedelta

        from headofcontext.actions import ApprovalRequest, ApprovalStatus, PostgresApprovalStore

        approvals = PostgresApprovalStore(pg_dsn)
        approvals.ensure_schema()
        now = datetime.now(UTC)
        request_id = f"cli-{now.timestamp()}"
        approvals.create(
            ApprovalRequest(
                request_id=request_id,
                subject="user:bruno.bernard",
                actor="agent:assistant",
                delegation_depth=0,
                tool="tool:hr.export",
                args_hash="h",
                decision_id="d",
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        approve = ["approvals", "approve", request_id, "--approver", "user:alice.martin"]
        assert main(approve) == 1
        assert "approver_not_authorized" in capsys.readouterr().err
        assert approvals.get(request_id).status is ApprovalStatus.PENDING  # type: ignore[union-attr]
        with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
            client.post(
                f"/stores/{store_id}/write",
                json={
                    "writes": {
                        "tuple_keys": [
                            {
                                "user": "user:alice.martin",
                                "relation": "approver",
                                "object": "tool:hr.export",
                            }
                        ]
                    }
                },
            ).raise_for_status()
        assert main(approve) == 0
        assert "approved by user:alice.martin" in capsys.readouterr().out
        assert approvals.get(request_id).status is ApprovalStatus.APPROVED  # type: ignore[union-attr]
        approvals.close()
        assert main(["keys", "generate"]) == 0
        assert len(capsys.readouterr().out.strip()) == 64
    finally:
        with httpx.Client(base_url=OPENFGA_URL, timeout=30) as client:
            client.delete(f"/stores/{store_id}")
