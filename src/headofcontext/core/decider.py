"""The single decision path shared by READ, ACT and REMEMBER (ADR 0002).

Order matters and is deliberate:
1. the actor's scope must cover the request (an agent may only *ask* what it was delegated);
2. OpenFGA must relate the *subject* to the resource (the human must have the right);
3. an optional approval policy may downgrade ALLOW to REQUIRE_APPROVAL, never upgrade DENY;
4. the audit sink must accept the event, otherwise no decision is returned at all.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from headofcontext.core.blocking import offload
from headofcontext.core.chain import PrincipalChain
from headofcontext.core.clock import Clock, SystemClock
from headofcontext.core.decision import Action, Decision, Outcome
from headofcontext.core.engine import AuthzEngine
from headofcontext.core.errors import HocError
from headofcontext.core.events import AuditEvent, AuditSink, hash_args
from headofcontext.core.policy import DecisionContext, DecisionPolicy, Verdict
from headofcontext.core.refs import validate_ref, validate_resource_pattern
from headofcontext.core.scope import Capability

_NO_CONTEXT = DecisionContext()
if TYPE_CHECKING:
    from headofcontext.core.metrics import MetricsSink

BATCH_LIMIT = 50  # OpenFGA BatchCheck accepts at most 50 checks per call.


class Decider:
    def __init__(
        self,
        engine: AuthzEngine,
        audit: AuditSink,
        clock: Clock | None = None,
        *,
        policy: DecisionPolicy | None = None,
        metrics: MetricsSink | None = None,
    ) -> None:
        self._engine = engine
        self._audit = audit
        self._clock = clock or SystemClock()
        self._policy = policy
        self._metrics = metrics

    @property
    def engine(self) -> AuthzEngine:
        """The engine every other authorization question (approver, listing) must ask too."""
        return self._engine

    def _observe(self, decision: Decision, started: float) -> None:
        if self._metrics is not None:
            engine_ms = decision.engine_latency_ms if decision.engine_latency_ms > 0 else None
            self._metrics.record(decision, total_ms=_elapsed_ms(started), engine_ms=engine_ms)

    async def decide(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any] | None = None,
        context: DecisionContext | None = None,
    ) -> Decision:
        validate_resource_pattern(resource)
        # I3, re-checked here so that no engine is ever asked about a non-user subject even if a
        # caller managed to build a chain-like object.
        validate_ref(chain.subject, "user")

        started = time.perf_counter()
        outcome, reason, latency_ms = await self._evaluate(
            chain, action, resource, args or {}, context or _NO_CONTEXT
        )
        decision = Decision(
            outcome=outcome,
            reason=reason,
            chain=chain,
            resource=resource,
            action=action,
            timestamp=self._clock.now(),
            engine_latency_ms=latency_ms,
        )
        # Raises AuditUnavailable: an unlogged decision must never reach the caller.
        await offload(
            self._audit.record, AuditEvent.from_decision(decision, args_hash=hash_args(args))
        )
        self._observe(decision, started)
        return decision

    async def decide_all(
        self,
        chain: PrincipalChain,
        action: Action,
        resources: Sequence[str],
        *,
        scope_resources: Sequence[str] | None = None,
        resource: str | None = None,
        args: Mapping[str, Any] | None = None,
        context: DecisionContext | None = None,
    ) -> Decision:
        """ALLOW only if every resource is allowed (BatchCheck). Used by memory provenance.

        ``scope_resources`` are what the actor's scope must cover (defaults to ``resources``);
        ``resource`` names the decision in the audit (defaults to the first resource).
        """
        validate_ref(chain.subject, "user")
        engine_resources = tuple(resources)
        scope_targets = tuple(scope_resources) if scope_resources is not None else engine_resources
        for item in (*engine_resources, *scope_targets):
            validate_resource_pattern(item)
        named = (
            resource if resource is not None else (engine_resources[0] if engine_resources else "")
        )

        started = time.perf_counter()
        outcome, reason, latency_ms = await self._evaluate_all(
            chain,
            action,
            engine_resources,
            scope_targets,
            explicit_scope=scope_resources is not None,
            args=args or {},
            context=context or _NO_CONTEXT,
        )
        decision = Decision(
            outcome=outcome,
            reason=reason,
            chain=chain,
            resource=named,
            action=action,
            timestamp=self._clock.now(),
            engine_latency_ms=latency_ms,
            resources=engine_resources,
        )
        await offload(
            self._audit.record, AuditEvent.from_decision(decision, args_hash=hash_args(args))
        )
        self._observe(decision, started)
        return decision

    async def decide_each(
        self,
        chain: PrincipalChain,
        action: Action,
        resources: Sequence[str],
        *,
        audit_each: bool = True,
        args: Mapping[str, Any] | None = None,
        context: DecisionContext | None = None,
    ) -> list[Decision]:
        """One Decision per resource from batched engine calls. Used by the read filter.

        With ``audit_each=False`` the caller is responsible for journaling the set decision.
        """
        validate_ref(chain.subject, "user")
        targets = tuple(resources)
        for item in targets:
            validate_resource_pattern(item)
        if not targets:
            return []
        started = time.perf_counter()
        call_args = args or {}
        call_context = context or _NO_CONTEXT
        outcomes: dict[int, tuple[Outcome, str, float]] = {}
        to_check: list[int] = []
        for index, item in enumerate(targets):
            if chain.scope.covers(Capability(action.kind, item)):
                to_check.append(index)
            else:
                outcomes[index] = (Outcome.DENY, "scope_excluded", 0.0)
        for start in range(0, len(to_check), BATCH_LIMIT):
            chunk = to_check[start : start + BATCH_LIMIT]
            started = time.perf_counter()
            related, failure = await self._batch(
                chain.subject, action.relation, tuple(targets[i] for i in chunk)
            )
            latency_ms = _elapsed_ms(started)
            for position, index in enumerate(chunk):
                if failure is not None:
                    outcomes[index] = (Outcome.DENY, failure, latency_ms)
                elif not related[position]:
                    outcomes[index] = (Outcome.DENY, "not_related", latency_ms)
                else:
                    verdict = self._policy_verdict(
                        chain, action, targets[index], call_args, call_context
                    )
                    outcomes[index] = (verdict.outcome, verdict.reason, latency_ms)
        decisions: list[Decision] = []
        now = self._clock.now()
        for index, item in enumerate(targets):
            outcome, reason, latency_ms = outcomes[index]
            decision = Decision(
                outcome=outcome,
                reason=reason,
                chain=chain,
                resource=item,
                action=action,
                timestamp=now,
                engine_latency_ms=latency_ms,
            )
            if audit_each:
                await offload(
                    self._audit.record,
                    AuditEvent.from_decision(decision, args_hash=hash_args(args)),
                )
            self._observe(decision, started)
            decisions.append(decision)
        return decisions

    async def _evaluate_all(
        self,
        chain: PrincipalChain,
        action: Action,
        resources: tuple[str, ...],
        scope_targets: tuple[str, ...],
        *,
        explicit_scope: bool,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> tuple[Outcome, str, float]:
        if not resources and not explicit_scope:
            return Outcome.DENY, "no_resource", 0.0
        if any(not chain.scope.covers(Capability(action.kind, t)) for t in scope_targets):
            return Outcome.DENY, "scope_excluded", 0.0
        if not resources:
            # Nothing to relate (a memory with no source): the scope alone was the question.
            return Outcome.ALLOW, "scope_only", 0.0
        started = time.perf_counter()
        related, failure = await self._batch(chain.subject, action.relation, resources)
        latency_ms = _elapsed_ms(started)
        if failure is not None or not all(related):
            return Outcome.DENY, failure or "not_related", latency_ms
        for item in resources:
            verdict = self._policy_verdict(chain, action, item, args, context)
            if verdict.outcome is not Outcome.ALLOW:
                return verdict.outcome, verdict.reason, latency_ms
        return Outcome.ALLOW, "allowed", latency_ms

    async def _batch(
        self, subject: str, relation: str, resources: tuple[str, ...]
    ) -> tuple[list[bool], str | None]:
        """BatchCheck with every failure mapped to a denial reason (I5)."""
        try:
            related = await self._engine.batch_check(subject, [(relation, r) for r in resources])
        except HocError as exc:
            return [], exc.reason
        except Exception:
            return [], "engine_error"
        if len(related) != len(resources):
            return [], "engine_error"
        return list(related), None

    def _policy_verdict(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> Verdict:
        """Policies run after scope and engine; a failing policy is a denial, never a pass (I5)."""
        if self._policy is None:
            return Verdict(Outcome.ALLOW, "allowed")
        try:
            verdict = self._policy.evaluate(chain, action, resource, args, context)
        except Exception:
            return Verdict(Outcome.DENY, "policy_error")
        if verdict.outcome is Outcome.ALLOW:
            return Verdict(Outcome.ALLOW, "allowed")
        return verdict

    async def _evaluate(
        self,
        chain: PrincipalChain,
        action: Action,
        resource: str,
        args: Mapping[str, Any],
        context: DecisionContext,
    ) -> tuple[Outcome, str, float]:
        if not chain.scope.covers(Capability(action.kind, resource)):
            return Outcome.DENY, "scope_excluded", 0.0

        started = time.perf_counter()
        try:
            related = await self._engine.check(chain.subject, action.relation, resource)
        except HocError as exc:  # EngineUnavailable, ConnectorStale: typed reason, DENY.
            return Outcome.DENY, exc.reason, _elapsed_ms(started)
        except Exception:
            return Outcome.DENY, "engine_error", _elapsed_ms(started)
        latency_ms = _elapsed_ms(started)

        if not related:
            return Outcome.DENY, "not_related", latency_ms
        verdict = self._policy_verdict(chain, action, resource, args, context)
        return verdict.outcome, verdict.reason, latency_ms


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
