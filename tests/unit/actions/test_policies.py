from headofcontext.actions import CompositePolicy, DenyWhen, RequireApproval
from headofcontext.core import (
    Action,
    Capability,
    DecisionContext,
    Kind,
    Outcome,
    PrincipalChain,
    Scope,
)

CHAIN = PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*")))
CTX = DecisionContext()


def test_require_approval_matches_patterns() -> None:
    policy = RequireApproval(["tool:payment.*", "tool:hr.export"])
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:payment.send", {}, CTX).outcome
        is Outcome.REQUIRE_APPROVAL
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:hr.export", {}, CTX).outcome
        is Outcome.REQUIRE_APPROVAL
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:mail.send", {}, CTX).outcome is Outcome.ALLOW
    )


def test_require_approval_satisfied_by_context() -> None:
    policy = RequireApproval(["tool:payment.*"])
    verdict = policy.evaluate(
        CHAIN, Action.invoke(), "tool:payment.send", {}, DecisionContext(approval_id="r1")
    )
    assert verdict.outcome is Outcome.ALLOW


def test_require_approval_with_predicate() -> None:
    policy = RequireApproval(["tool:payment.*"], when=lambda args: args.get("amount", 0) > 100)
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:payment.send", {"amount": 50}, CTX).outcome
        is Outcome.ALLOW
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:payment.send", {"amount": 500}, CTX).outcome
        is Outcome.REQUIRE_APPROVAL
    )


def test_require_approval_only_for_act() -> None:
    policy = RequireApproval(["tool:*"])
    assert policy.evaluate(CHAIN, Action.read(), "tool:x", {}, CTX).outcome is Outcome.ALLOW


def test_deny_when() -> None:
    policy = DenyWhen(
        ["tool:mail.*"],
        lambda args: not str(args.get("to", "")).endswith("@acme.example"),
        reason="external_recipient",
    )
    verdict = policy.evaluate(CHAIN, Action.invoke(), "tool:mail.send", {"to": "x@evil.test"}, CTX)
    assert verdict.outcome is Outcome.DENY and verdict.reason == "external_recipient"
    assert (
        policy.evaluate(
            CHAIN, Action.invoke(), "tool:mail.send", {"to": "bob@acme.example"}, CTX
        ).outcome
        is Outcome.ALLOW
    )


def test_predicate_exception_is_deny() -> None:
    def boom(args: object) -> bool:
        raise ValueError("bad args")

    policy = RequireApproval(["tool:*"], when=boom)
    assert policy.evaluate(CHAIN, Action.invoke(), "tool:x", {}, CTX).outcome is Outcome.DENY


def test_composite_most_restrictive_wins() -> None:
    policy = CompositePolicy(
        [
            RequireApproval(["tool:payment.*"]),
            DenyWhen(["tool:payment.*"], lambda a: a.get("amount", 0) > 1000, reason="too_high"),
        ]
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:payment.send", {"amount": 5000}, CTX).outcome
        is Outcome.DENY
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:payment.send", {"amount": 500}, CTX).outcome
        is Outcome.REQUIRE_APPROVAL
    )
    assert (
        policy.evaluate(CHAIN, Action.invoke(), "tool:mail.send", {}, CTX).outcome is Outcome.ALLOW
    )
