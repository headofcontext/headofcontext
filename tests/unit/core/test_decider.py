"""I5 (fail closed) and "never an unlogged decision" on the Decider."""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from headofcontext.actions import RequireApproval
from headofcontext.audit import AuditEvent, InMemoryAuditSink
from headofcontext.core import Action, Capability, Decider, Kind, Outcome, PrincipalChain, Scope
from headofcontext.core.errors import AuditUnavailable, ConnectorStale, EngineUnavailable
from tests.conftest import FrozenClock

READ_ALL = Scope.of(Capability(Kind.READ, "document:*"))
CHAIN = PrincipalChain.root("user:alice", "agent:a", READ_ALL)


class FakeEngine:
    def __init__(self, answer: bool | Exception = True) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, str]] = []

    async def check(self, subject: str, relation: str, obj: str) -> bool:
        self.calls.append((subject, relation, obj))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def batch_check(self, subject: str, items: Sequence[tuple[str, str]]) -> list[bool]:
        return [await self.check(subject, r, o) for r, o in items]

    async def list_objects(self, subject: str, relation: str, type_: str) -> list[str]:
        return []


class BrokenSink:
    def record(self, event: AuditEvent) -> None:
        raise AuditUnavailable("disk full")


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


async def test_allow_when_scope_and_engine_agree(
    audit: InMemoryAuditSink, clock: FrozenClock
) -> None:
    engine = FakeEngine(True)
    d = Decider(engine, audit, clock)
    decision = await d.decide(CHAIN, Action.read(), "document:hr/salaries")
    assert decision.outcome is Outcome.ALLOW
    assert engine.calls == [("user:alice", "viewer", "document:hr/salaries")]
    assert decision.timestamp == clock.now()
    assert decision.engine_latency_ms >= 0
    assert len(audit.events) == 1
    assert audit.events[0].outcome == "ALLOW"
    assert audit.events[0].subject == "user:alice"


async def test_deny_when_engine_says_no(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    d = Decider(FakeEngine(False), audit, clock)
    decision = await d.decide(CHAIN, Action.read(), "document:hr/salaries")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "not_related"
    assert audit.events[0].outcome == "DENY"


async def test_deny_outside_scope_without_calling_engine(
    audit: InMemoryAuditSink, clock: FrozenClock
) -> None:
    engine = FakeEngine(True)
    d = Decider(engine, audit, clock)
    decision = await d.decide(CHAIN, Action.invoke(), "tool:mail.send")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "scope_excluded"
    assert engine.calls == []
    assert len(audit.events) == 1


async def test_i5_engine_unavailable_denies(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    d = Decider(FakeEngine(EngineUnavailable("openfga down")), audit, clock)
    decision = await d.decide(CHAIN, Action.read(), "document:x")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "engine_unavailable"
    assert audit.events[0].reason == "engine_unavailable"


async def test_i5_stale_connector_denies(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    d = Decider(FakeEngine(ConnectorStale("nextcloud", 3600)), audit, clock)
    decision = await d.decide(CHAIN, Action.read(), "document:x")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "connector_stale"


async def test_i5_unexpected_engine_error_denies(
    audit: InMemoryAuditSink, clock: FrozenClock
) -> None:
    d = Decider(FakeEngine(RuntimeError("boom")), audit, clock)
    decision = await d.decide(CHAIN, Action.read(), "document:x")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "engine_error"


async def test_no_decision_without_audit(clock: FrozenClock) -> None:
    d = Decider(FakeEngine(True), BrokenSink(), clock)
    with pytest.raises(AuditUnavailable):
        await d.decide(CHAIN, Action.read(), "document:x")


async def test_require_approval_hook(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    chain = PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*")))

    d = Decider(FakeEngine(True), audit, clock, policy=RequireApproval(["tool:payment.send"]))
    assert (
        await d.decide(chain, Action.invoke(), "tool:payment.send")
    ).outcome is Outcome.REQUIRE_APPROVAL
    assert (await d.decide(chain, Action.invoke(), "tool:mail.send")).outcome is Outcome.ALLOW


async def test_approval_never_overrides_deny(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    chain = PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*")))
    d = Decider(FakeEngine(False), audit, clock, policy=RequireApproval(["tool:*"]))
    assert (await d.decide(chain, Action.invoke(), "tool:payment.send")).outcome is Outcome.DENY


async def test_audit_event_carries_no_document_content(
    audit: InMemoryAuditSink, clock: FrozenClock
) -> None:
    d = Decider(FakeEngine(True), audit, clock)
    await d.decide(CHAIN, Action.read(), "document:x", args={"body": "SECRET CONTENT"})
    event = audit.events[0]
    assert "SECRET" not in repr(event)
    assert event.args_hash is not None


class TestDecideAll:
    async def test_all_allowed(self, audit: InMemoryAuditSink, clock: FrozenClock) -> None:
        engine = FakeEngine(True)
        d = Decider(engine, audit, clock)
        decision = await d.decide_all(CHAIN, Action.read(), ["document:a", "document:b"])
        assert decision.outcome is Outcome.ALLOW
        assert decision.resources == ("document:a", "document:b")
        assert decision.resource == "document:a"
        assert len(audit.events) == 1

    async def test_one_denied_denies_all(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        class Partial(FakeEngine):
            async def batch_check(
                self, subject: str, items: Sequence[tuple[str, str]]
            ) -> list[bool]:
                return [obj != "document:secret" for _, obj in items]

        d = Decider(Partial(), audit, clock)
        decision = await d.decide_all(CHAIN, Action.read(), ["document:a", "document:secret"])
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "not_related"

    async def test_scope_checked_on_every_resource(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        engine = FakeEngine(True)
        chain = PrincipalChain.root(
            "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:hr/*"))
        )
        d = Decider(engine, audit, clock)
        decision = await d.decide_all(chain, Action.read(), ["document:hr/a", "document:finance/b"])
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "scope_excluded"
        assert engine.calls == []

    async def test_scope_resources_can_differ_from_engine_resources(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        chain = PrincipalChain.root(
            "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "memory:*"))
        )
        d = Decider(FakeEngine(True), audit, clock)
        decision = await d.decide_all(
            chain,
            Action.read_memory(),
            ["document:a"],
            scope_resources=["memory:m1"],
            resource="memory:m1",
        )
        assert decision.outcome is Outcome.ALLOW
        assert decision.resource == "memory:m1"
        assert audit.events[0].resource == "memory:m1"

    async def test_empty_resources_is_allowed_only_if_scope_resources_given(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        d = Decider(FakeEngine(True), audit, clock)
        decision = await d.decide_all(CHAIN, Action.read(), [])
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "no_resource"

    async def test_i5_engine_failure_denies(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        d = Decider(FakeEngine(EngineUnavailable("down")), audit, clock)
        decision = await d.decide_all(CHAIN, Action.read(), ["document:a"])
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "engine_unavailable"

    async def test_wrong_batch_length_denies(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        class Short(FakeEngine):
            async def batch_check(
                self, subject: str, items: Sequence[tuple[str, str]]
            ) -> list[bool]:
                return [True]

        d = Decider(Short(), audit, clock)
        decision = await d.decide_all(CHAIN, Action.read(), ["document:a", "document:b"])
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "engine_error"


class TestDecisionPolicy:
    async def test_policy_can_only_lower(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        from headofcontext.core import DecisionContext, DecisionPolicy, Verdict

        class Upgrader:
            def evaluate(
                self,
                chain: PrincipalChain,
                action: Action,
                resource: str,
                args: Mapping[str, Any],
                context: DecisionContext,
            ) -> Verdict:
                return Verdict(Outcome.ALLOW, "policy says yes")

        policy: DecisionPolicy = Upgrader()
        d = Decider(FakeEngine(False), audit, clock, policy=policy)
        assert (await d.decide(CHAIN, Action.read(), "document:x")).outcome is Outcome.DENY

    async def test_policy_deny_and_approval(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        from headofcontext.core import DecisionContext, Verdict

        class ArgsPolicy:
            def evaluate(
                self,
                chain: PrincipalChain,
                action: Action,
                resource: str,
                args: Mapping[str, Any],
                context: DecisionContext,
            ) -> Verdict:
                if args.get("amount", 0) > 1000:
                    return Verdict(Outcome.DENY, "amount_too_high")
                if args.get("amount", 0) > 100 and context.approval_id is None:
                    return Verdict(Outcome.REQUIRE_APPROVAL, "amount_needs_approval")
                return Verdict(Outcome.ALLOW, "ok")

        chain = PrincipalChain.root(
            "user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*"))
        )
        d = Decider(FakeEngine(True), audit, clock, policy=ArgsPolicy())
        assert (
            await d.decide(chain, Action.invoke(), "tool:pay", args={"amount": 5000})
        ).reason == "amount_too_high"
        pending = await d.decide(chain, Action.invoke(), "tool:pay", args={"amount": 500})
        assert pending.outcome is Outcome.REQUIRE_APPROVAL
        approved = await d.decide(
            chain,
            Action.invoke(),
            "tool:pay",
            args={"amount": 500},
            context=DecisionContext(approval_id="req-1"),
        )
        assert approved.outcome is Outcome.ALLOW
        assert (
            await d.decide(chain, Action.invoke(), "tool:pay", args={"amount": 5})
        ).outcome is Outcome.ALLOW

    async def test_policy_exception_denies(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        from headofcontext.core import DecisionContext, Verdict

        class Broken:
            def evaluate(
                self,
                chain: PrincipalChain,
                action: Action,
                resource: str,
                args: Mapping[str, Any],
                context: DecisionContext,
            ) -> Verdict:
                raise RuntimeError("boom")

        d = Decider(FakeEngine(True), audit, clock, policy=Broken())
        decision = await d.decide(CHAIN, Action.read(), "document:x")
        assert decision.outcome is Outcome.DENY
        assert decision.reason == "policy_error"


class TestDecideEach:
    async def test_one_decision_per_resource_one_engine_call(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        class Partial(FakeEngine):
            async def batch_check(
                self, subject: str, items: Sequence[tuple[str, str]]
            ) -> list[bool]:
                self.calls.append((subject, "batch", str(len(items))))
                return [obj.endswith("ok") for _, obj in items]

        engine = Partial()
        d = Decider(engine, audit, clock)
        decisions = await d.decide_each(
            CHAIN, Action.read(), ["document:a-ok", "document:b", "document:c-ok"]
        )
        assert [x.outcome for x in decisions] == [Outcome.ALLOW, Outcome.DENY, Outcome.ALLOW]
        assert [x.resource for x in decisions] == ["document:a-ok", "document:b", "document:c-ok"]
        assert engine.calls == [("user:alice", "batch", "3")]
        assert len(audit.events) == 3

    async def test_scope_excluded_items_never_reach_engine(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        engine = FakeEngine(True)
        chain = PrincipalChain.root(
            "user:alice", "agent:a", Scope.of(Capability(Kind.READ, "document:hr/*"))
        )
        d = Decider(engine, audit, clock)
        decisions = await d.decide_each(
            chain, Action.read(), ["document:hr/a", "document:finance/b"]
        )
        assert [x.reason for x in decisions] == ["allowed", "scope_excluded"]

    async def test_engine_failure_denies_all(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        d = Decider(FakeEngine(EngineUnavailable("down")), audit, clock)
        decisions = await d.decide_each(CHAIN, Action.read(), ["document:a", "document:b"])
        assert all(
            x.outcome is Outcome.DENY and x.reason == "engine_unavailable" for x in decisions
        )

    async def test_chunking_respects_batch_limit(
        self, audit: InMemoryAuditSink, clock: FrozenClock
    ) -> None:
        class Counting(FakeEngine):
            async def batch_check(
                self, subject: str, items: Sequence[tuple[str, str]]
            ) -> list[bool]:
                assert len(items) <= 50
                self.calls.append((subject, "batch", str(len(items))))
                return [True] * len(items)

        engine = Counting()
        d = Decider(engine, audit, clock)
        decisions = await d.decide_each(
            CHAIN, Action.read(), [f"document:{i}" for i in range(120)], audit_each=False
        )
        assert len(decisions) == 120 and len(engine.calls) == 3
        assert audit.events == []

    async def test_empty(self, audit: InMemoryAuditSink, clock: FrozenClock) -> None:
        d = Decider(FakeEngine(True), audit, clock)
        assert await d.decide_each(CHAIN, Action.read(), []) == []
