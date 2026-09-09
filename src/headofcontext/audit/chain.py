"""Pure verification of the hash chain (ADR 0005, ADR 0025 amended).

Works on any ordered sequence of stored rows: the live table, an exported archive, a slice.
The chain must start at ``anchor``: ``GENESIS`` unless the caller says otherwise, because
accepting whatever ``prev_hash`` comes first would let a rewritten history pass. After a prune,
the anchor is the archived head, which the operator recorded from the archive's report.

Only the payload is hashed; when a row carries its reporting columns as a fifth element, they
must agree with the payload, so what `hoc journal tail` shows is what was hashed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from headofcontext.core.events import GENESIS_HASH, AuditEvent, EventKind, compute_hash

ChainRow = tuple[int, str, str, str] | tuple[int, str, str, str, Mapping[str, object]]
"""(seq, canonical payload, prev_hash, hash[, columns]) in ``seq`` order."""


@dataclass(frozen=True, slots=True)
class ChainReport:
    ok: bool
    checked: int
    first_seq: int | None
    last_seq: int | None
    anchor: str | None
    head: str | None = None
    break_at: int | None = None
    detail: str = ""


def verify_rows(rows: Iterable[ChainRow], *, anchor: str = GENESIS_HASH) -> ChainReport:
    checked = 0
    first_seq: int | None = None
    last_seq: int | None = None
    prev = anchor
    for row in rows:
        seq, payload, prev_hash, stored_hash = row[0], row[1], row[2], row[3]
        columns = row[4] if len(row) > 4 else None
        if first_seq is None:
            first_seq = seq
            if prev_hash != anchor:
                return ChainReport(False, 0, seq, seq, anchor, None, seq, "anchor mismatch")
        if prev_hash != prev:
            return ChainReport(
                False, checked, first_seq, seq, anchor, prev, seq, "prev_hash mismatch"
            )
        event = event_from_payload(payload)
        if compute_hash(prev_hash, event) != stored_hash:
            return ChainReport(False, checked, first_seq, seq, anchor, prev, seq, "hash mismatch")
        if columns is not None and _columns_differ(columns, json.loads(payload)):
            return ChainReport(False, checked, first_seq, seq, anchor, prev, seq, "column mismatch")
        prev = stored_hash
        checked += 1
        last_seq = seq
    return ChainReport(True, checked, first_seq, last_seq, anchor, prev if checked else None)


def _columns_differ(columns: Mapping[str, object], payload: Mapping[str, object]) -> bool:
    return any(str(payload.get(name)) != str(value) for name, value in columns.items())


def event_from_payload(payload: str) -> AuditEvent:
    data = json.loads(payload)
    return AuditEvent(
        kind=EventKind(data["kind"]),
        timestamp=datetime.fromisoformat(data["timestamp"]),
        subject=data["subject"],
        actor=data["actor"],
        delegation_depth=int(data["delegation_depth"]),
        action=data["action"],
        resource=data["resource"],
        outcome=data["outcome"],
        reason=data["reason"],
        engine_latency_ms=float(data["engine_latency_ms"]),
        token_fingerprint=data.get("token_fingerprint"),
        args_hash=data.get("args_hash"),
        decision_id=data.get("decision_id"),
        event_id=data["event_id"],
    )


__all__ = ["ChainReport", "ChainRow", "event_from_payload", "verify_rows"]
