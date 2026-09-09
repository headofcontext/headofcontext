"""Migration ledger invariants (ADR 0020): ordered, unique, baseline first, nothing edited."""

from headofcontext.db import LATEST, MIGRATIONS, pending


def test_versions_are_strictly_increasing_from_one() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
    assert versions[-1] == LATEST
    assert MIGRATIONS[0].name == "baseline"
    assert MIGRATIONS[1].name == "memory_content"
    assert MIGRATIONS[2].name == "audit_truncate_guard"


def test_pending_is_everything_after_the_current_version() -> None:
    assert [m.version for m in pending(0)] == [m.version for m in MIGRATIONS]
    assert pending(LATEST) == ()
    assert [m.version for m in pending(LATEST - 1)] == [LATEST]


def test_baseline_covers_every_store_table() -> None:
    sql = MIGRATIONS[0].sql
    for table in (
        "audit_events",
        "token_revocations",
        "approval_requests",
        "mandates",
        "memory_provenance",
        "connector_state_v2",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "audit_events_no_update" in sql  # the append-only trigger travels with the baseline
