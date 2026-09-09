"""filter_items: keep only the items whose document the subject may view (ADR 0010).

The filter is deaf to content: it looks at one field per item, the document reference, and asks
OpenFGA about the subject. Nothing in the text of a hit, and nothing the index says about who the
hit is for, can influence the outcome.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from headofcontext.core import (
    Action,
    AuthzEngine,
    Capability,
    Clock,
    Decider,
    Kind,
    Outcome,
    PrincipalChain,
    SystemClock,
)
from headofcontext.core.blocking import offload
from headofcontext.core.errors import HocError, InvariantViolation
from headofcontext.core.events import AuditEvent, AuditSink, EventKind
from headofcontext.core.permits import ResourcePermits
from headofcontext.core.refs import validate_ref

log = logging.getLogger(__name__)

Strategy = Literal["auto", "batch", "list_objects"]
LIST_OBJECTS_THRESHOLD = 100
_DOCUMENT_TYPE = "document"


@dataclass(frozen=True, slots=True)
class FilterResult[T]:
    kept: list[T]
    dropped: tuple[str, ...]
    strategy: str
    decisions_by_ref: dict[str, Outcome] = field(default_factory=dict, repr=False)

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def default_id_of(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("id")
    return getattr(item, "id", None)


def document_refs[T](items: Sequence[T], id_of: Callable[[T], Any] | None = None) -> list[str]:
    """The valid document references among ``items``, in order, for ``TokenService.verify_many``.

    Anything that is not a well-formed ``document:`` reference is left out here and dropped as
    ``invalid`` by ``filter_items``; it never reaches the token or the engine.
    """
    extract = id_of or default_id_of
    return [r for r in (_reference(extract, item) for item in items) if r is not None]


async def filter_items[T](
    chain: PrincipalChain,
    items: Sequence[T],
    *,
    engine: AuthzEngine,
    audit: AuditSink,
    id_of: Callable[[T], Any] | None = None,
    relation: str = "viewer",
    strategy: Strategy = "auto",
    per_item_audit: bool = False,
    clock: Clock | None = None,
    decider: Decider | None = None,
    permits: ResourcePermits | None = None,
) -> FilterResult[T]:
    """Keep the items whose document the subject may view.

    ``permits`` is the token holder's per-resource answer (``core.permits.ResourcePermits``,
    built from ``TokenService.verify_many``), asked off the loop for the candidate references: a
    reference it refuses or does not mention is dropped before the engine is asked
    (ADR 0010 amendment, ADR 0027).
    """
    if not items:
        return FilterResult(kept=[], dropped=(), strategy="none")
    clock = clock or SystemClock()
    decider = decider or Decider(engine, audit, clock)
    extract = id_of or default_id_of
    action = Action(Kind.READ, relation)

    refs: list[str | None] = [_reference(extract, item) for item in items]
    token_refused: set[str] = set()
    if permits is not None:
        wanted = sorted({r for r in refs if r is not None})
        answers = await offload(permits, Kind.READ, wanted) if wanted else {}
        token_refused = {r for r in wanted if not answers.get(r, False)}
    candidates = sorted({r for r in refs if r is not None and r not in token_refused})
    chosen = _choose(strategy, len(candidates))

    allowed: dict[str, Outcome] = dict.fromkeys(token_refused, Outcome.DENY)
    if not candidates:
        pass
    elif chosen == "list_objects":
        allowed.update(await _via_list_objects(decider, chain, action, candidates, engine))
    else:
        decisions = await decider.decide_each(chain, action, candidates, audit_each=per_item_audit)
        allowed.update({d.resource: d.outcome for d in decisions})

    kept: list[T] = []
    dropped: list[str] = []
    for item, ref in zip(items, refs, strict=True):
        if ref is not None and allowed.get(ref) is Outcome.ALLOW:
            kept.append(item)
        else:
            dropped.append(ref if ref is not None else "invalid")
    await offload(
        audit.record,
        _summary_event(
            chain,
            action,
            kept_refs=[r for r in refs if allowed.get(r or "") is Outcome.ALLOW],
            dropped=dropped,
            token_refused=len(token_refused),
            clock=clock,
        ),
    )
    return FilterResult(
        kept=kept, dropped=tuple(dropped), strategy=chosen, decisions_by_ref=allowed
    )


def _reference[T](extract: Callable[[T], Any], item: T) -> str | None:
    try:
        raw = extract(item)
    except Exception:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return validate_ref(raw, _DOCUMENT_TYPE)
    except InvariantViolation:
        return None


def _choose(strategy: Strategy, count: int) -> str:
    if strategy == "auto":
        return "list_objects" if count > LIST_OBJECTS_THRESHOLD else "batch"
    return strategy


async def _via_list_objects(
    decider: Decider,
    chain: PrincipalChain,
    action: Action,
    candidates: list[str],
    engine: AuthzEngine,
) -> dict[str, Outcome]:
    """ListObjects once, intersect, then run the scope and policy checks through the decider.

    The decider is given a fake-free path: resources the engine did not list are denied without
    asking again; listed ones go through ``decide_each`` in a batch that is guaranteed allowed by
    the engine, so scope and policies still apply uniformly.
    """
    try:
        visible = set(await engine.list_objects(chain.subject, action.relation, _DOCUMENT_TYPE))
    except HocError as exc:
        log.warning("list_objects failed: %s", exc.reason)
        return dict.fromkeys(candidates, Outcome.DENY)
    except Exception:
        log.warning("list_objects failed unexpectedly")
        return dict.fromkeys(candidates, Outcome.DENY)
    outcomes: dict[str, Outcome] = dict.fromkeys(candidates, Outcome.DENY)
    listed = [c for c in candidates if c in visible]
    for decision in await decider.decide_each(chain, action, listed, audit_each=False):
        outcomes[decision.resource] = decision.outcome
    return outcomes


def _summary_event(
    chain: PrincipalChain,
    action: Action,
    *,
    kept_refs: list[str | None],
    dropped: list[str],
    token_refused: int,
    clock: Clock,
) -> AuditEvent:
    kept_digest = _digest([r for r in kept_refs if r is not None])
    dropped_digest = _digest(dropped)
    return AuditEvent(
        kind=EventKind.READ_FILTERED,
        timestamp=clock.now(),
        subject=chain.subject,
        actor=chain.actor,
        delegation_depth=chain.depth,
        action=f"{action.kind}:{action.relation}",
        resource=f"{_DOCUMENT_TYPE}:*",
        outcome="ALLOW" if kept_refs else "DENY",
        reason=(
            f"kept={len(kept_refs)} dropped={len(dropped)} token_refused={token_refused} "
            f"kept_sha256={kept_digest[:16]}"
        ),
        args_hash=dropped_digest,
    )


def _digest(refs: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()


__all__ = [
    "LIST_OBJECTS_THRESHOLD",
    "Capability",
    "FilterResult",
    "document_refs",
    "filter_items",
]
