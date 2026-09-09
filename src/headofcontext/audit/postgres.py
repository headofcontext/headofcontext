"""Append-only, hash-chained audit journal in PostgreSQL (ADR 0005).

The table refuses UPDATE and DELETE through a trigger. The application role should additionally be
granted INSERT and SELECT only. Each row's ``hash`` covers the previous row's hash and the
canonical JSON of the event, so any edit or gap is detectable by ``verify_chain``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from headofcontext.audit.chain import ChainReport, verify_rows
from headofcontext.core.errors import AuditUnavailable
from headofcontext.core.events import GENESIS_HASH, AuditEvent, compute_hash
from headofcontext.db.schema import AUDIT_SCHEMA
from headofcontext.db.store import PostgresStore

_INSERT = """
INSERT INTO audit_events (
    event_id, kind, ts, subject, actor, delegation_depth, action, resource, outcome, reason,
    engine_latency_ms, token_fingerprint, args_hash, decision_id, payload, prev_hash, hash
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# Serializes appends so the hash chain has a single, well-defined predecessor per row.
_LOCK = "SELECT pg_advisory_xact_lock(hashtext('audit_events'))"
_LAST_HASH = "SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1"


class PostgresAuditSink(PostgresStore):
    unavailable = AuditUnavailable
    schema = AUDIT_SCHEMA

    def record(self, event: AuditEvent) -> None:
        payload = event.canonical_json()
        with self._transaction(failure="audit journal write failed") as conn:
            conn.execute(_LOCK)
            row = conn.execute(_LAST_HASH).fetchone()
            prev_hash = str(row[0]) if row else GENESIS_HASH
            conn.execute(
                _INSERT,
                (
                    event.event_id,
                    str(event.kind),
                    event.timestamp,
                    event.subject,
                    event.actor,
                    event.delegation_depth,
                    event.action,
                    event.resource,
                    event.outcome,
                    event.reason,
                    event.engine_latency_ms,
                    event.token_fingerprint,
                    event.args_hash,
                    event.decision_id,
                    payload,
                    prev_hash,
                    compute_hash(prev_hash, event),
                ),
            )

    def verify_chain(self) -> bool:
        """True when the chain is intact from GENESIS (see ``verify_chain_report``)."""
        return self.verify_chain_report().ok

    def verify_chain_report(self, *, anchor: str = GENESIS_HASH, batch: int = 1000) -> ChainReport:
        """Stream the table in ``seq`` order and verify hashes, links and reporting columns."""
        with (
            self._transaction(failure="audit journal read failed") as conn,
            conn.cursor(name="hoc_audit_verify") as cursor,
        ):
            cursor.itersize = batch
            cursor.execute(
                "SELECT seq, payload, prev_hash, hash, kind, subject, actor, action, resource, "
                "outcome, reason FROM audit_events ORDER BY seq"
            )
            return verify_rows((self._chain_row(row) for row in cursor), anchor=anchor)

    @staticmethod
    def _chain_row(row: tuple[object, ...]) -> tuple[int, str, str, str, dict[str, object]]:
        seq, payload, prev, stored, *columns = row
        names = ("kind", "subject", "actor", "action", "resource", "outcome", "reason")
        return (
            int(str(seq)),
            str(payload),
            str(prev),
            str(stored),
            dict(zip(names, columns, strict=True)),
        )

    def export(
        self,
        *,
        since_seq: int = 0,
        until_seq: int | None = None,
        batch: int = 1000,
    ) -> Iterator[dict[str, object]]:
        """Rows as JSON-ready dicts (seq, prev_hash, hash, event), by ``seq`` ranges.

        Bounds are ``seq``, the same axis a prune uses, so an exported range and a pruned range
        are the same rows. Each batch is its own query: no connection is held between yields.
        """
        last = since_seq
        while True:
            rows = self._query(
                "SELECT seq, payload, prev_hash, hash FROM audit_events "
                "WHERE seq > %s AND (%s::bigint IS NULL OR seq <= %s) ORDER BY seq LIMIT %s",
                (last, until_seq, until_seq, batch),
                failure="audit journal read failed",
            )
            for seq, payload, prev, stored in rows:
                last = int(str(seq))
                yield {
                    "seq": last,
                    "prev_hash": str(prev),
                    "hash": str(stored),
                    "event": json.loads(str(payload)),
                }
            if len(rows) < batch:
                return

    def count(self) -> int:
        row = self._one("SELECT count(*) FROM audit_events", failure="audit journal read failed")
        return int(str(row[0])) if row else 0
