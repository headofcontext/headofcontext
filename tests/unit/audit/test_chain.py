"""Chain verification on rows (ADR 0025): intact, tampered, and pruned with an anchor."""

from __future__ import annotations

from datetime import UTC, datetime

from headofcontext.audit.chain import verify_rows
from headofcontext.core.events import GENESIS_HASH, AuditEvent, EventKind, StoredAuditEvent


def _event(i: int) -> AuditEvent:
    return AuditEvent(
        kind=EventKind.DECISION,
        timestamp=datetime(2026, 9, 9, 12, 0, i, tzinfo=UTC),
        subject="user:alice",
        actor="agent:a",
        delegation_depth=0,
        action="act:can_invoke",
        resource="tool:mail.send",
        outcome="ALLOW",
        reason="allowed",
    )


def _rows(
    n: int, start_seq: int = 1, anchor: str = GENESIS_HASH
) -> list[tuple[int, str, str, str]]:
    rows = []
    prev = anchor
    for i in range(n):
        stored = StoredAuditEvent.chain(_event(i), prev)
        rows.append((start_seq + i, stored.event.canonical_json(), stored.prev_hash, stored.hash))
        prev = stored.hash
    return rows


def test_intact_chain_verifies() -> None:
    report = verify_rows(_rows(5))
    assert report.ok and report.checked == 5 and (report.first_seq, report.last_seq) == (1, 5)
    assert report.anchor == GENESIS_HASH and report.break_at is None


def test_tampered_payload_breaks_at_that_row() -> None:
    rows = _rows(5)
    seq, payload, prev_hash, stored_hash = rows[2]
    rows[2] = (seq, payload.replace('"ALLOW"', '"DENY"'), prev_hash, stored_hash)
    report = verify_rows(rows)
    assert not report.ok and report.break_at == 3 and report.detail == "hash mismatch"


def test_removed_row_in_the_middle_breaks_the_link() -> None:
    rows = _rows(5)
    del rows[2]
    report = verify_rows(rows)
    assert not report.ok and report.break_at == 4 and report.detail == "prev_hash mismatch"


def test_pruned_head_verifies_from_its_anchor() -> None:
    rows = _rows(6)
    archived, kept = rows[:3], rows[3:]
    assert verify_rows(archived).ok
    report = verify_rows(kept, anchor=archived[-1][3])
    assert report.ok and report.anchor == archived[-1][3] and report.first_seq == 4


def test_empty_journal_is_ok() -> None:
    report = verify_rows([])
    assert report.ok and report.checked == 0 and report.anchor == GENESIS_HASH
    assert report.head is None


def test_pruned_head_is_refused_without_its_anchor() -> None:
    """T10: a journal whose head was removed does not verify unless the archive's last hash is
    given explicitly; accepting the first prev_hash would let a rewritten history pass."""
    rows = _rows(6)
    archived, kept = rows[:3], rows[3:]
    assert not verify_rows(kept).ok
    assert verify_rows(kept).detail == "anchor mismatch"
    report = verify_rows(kept, anchor=archived[-1][3])
    assert report.ok and report.first_seq == 4 and report.head == kept[-1][3]


def test_fabricated_history_from_a_foreign_anchor_is_refused() -> None:
    fabricated = _rows(3, anchor="deadbeef" * 8)
    assert not verify_rows(fabricated).ok


def test_columns_diverging_from_the_payload_are_detected() -> None:
    """T10: only the payload is hashed; columns read by reports must agree with it."""
    rows = _rows(3)
    seq = rows[1][0]
    columns = {"outcome": "DENY"}  # the payload says ALLOW
    report = verify_rows(
        [(s, p, ph, h, ({"outcome": "ALLOW"} if s != seq else columns)) for s, p, ph, h in rows]
    )
    assert not report.ok and report.break_at == seq and report.detail == "column mismatch"
    assert verify_rows([(s, p, ph, h, {"outcome": "ALLOW"}) for s, p, ph, h in rows]).ok


def test_report_carries_the_head_hash_for_out_of_band_recording() -> None:
    rows = _rows(4)
    assert verify_rows(rows).head == rows[-1][3]
