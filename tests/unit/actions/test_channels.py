"""Approval channels (ADR 0014): notified after a pending request, never deciding anything."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx
import pytest

from headofcontext.actions import (
    ActionGate,
    ApprovalChannel,
    ApprovalChannelError,
    ApprovalRequest,
    ApprovalStatus,
    InMemoryApprovalStore,
    LogChannel,
    RequireApproval,
    WebhookChannel,
    build_channels,
)
from headofcontext.audit import InMemoryAuditSink
from headofcontext.audit.events import EventKind
from headofcontext.core import Capability, Decider, Kind, PrincipalChain, Scope
from tests.conftest import FrozenClock
from tests.unit.memory.conftest import FakeGraph

CHAIN = PrincipalChain.root(
    "user:alice", "agent:assistant", Scope.of(Capability(Kind.ACT, "tool:*"))
)


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


class Recording:
    name = "recording"

    def __init__(self) -> None:
        self.seen: list[ApprovalRequest] = []

    async def notify(self, request: ApprovalRequest) -> None:
        self.seen.append(request)


class Broken:
    name = "broken"

    async def notify(self, request: ApprovalRequest) -> None:
        raise ApprovalChannelError("nope")


def make_gate(
    channels: list[ApprovalChannel], audit: InMemoryAuditSink, clock: FrozenClock
) -> ActionGate:
    graph = FakeGraph()
    graph.grant("user:alice", "can_invoke", "tool:payment.send")
    graph.grant("user:alice", "can_invoke", "tool:mail.send")
    decider = Decider(graph, audit, clock, policy=RequireApproval(["tool:payment.*"]))
    return ActionGate(decider, InMemoryApprovalStore(), audit, clock, channels=channels)


async def test_pending_request_is_notified_with_metadata_only(
    audit: InMemoryAuditSink, clock: FrozenClock
) -> None:
    channel = Recording()
    gate = make_gate([channel], audit, clock)
    result = await gate.gate(CHAIN, "tool:payment.send", {"amount": 10, "iban": "FR76"})
    assert result.pending and result.approval is not None
    assert [r.request_id for r in channel.seen] == [result.approval.request_id]
    dumped = json.dumps(dataclasses.asdict(channel.seen[0]), default=str)
    assert "FR76" not in dumped and "iban" not in dumped


async def test_allowed_call_notifies_nobody(audit: InMemoryAuditSink, clock: FrozenClock) -> None:
    channel = Recording()
    gate = make_gate([channel], audit, clock)
    result = await gate.gate(CHAIN, "tool:mail.send", {"to": "bob"})
    assert result.allowed and channel.seen == []


async def test_broken_channel_never_changes_the_outcome(
    audit: InMemoryAuditSink, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    after = Recording()
    gate = make_gate([Broken(), after], audit, clock)
    with caplog.at_level(logging.WARNING):
        result = await gate.gate(CHAIN, "tool:payment.send", {"amount": 10})
    assert result.pending and result.approval is not None
    assert gate.approvals.get(result.approval.request_id) is not None
    assert gate.approvals.get(result.approval.request_id).status is ApprovalStatus.PENDING  # type: ignore[union-attr]
    assert len(after.seen) == 1, "channels after the broken one still run"
    failed = [e for e in audit.events if e.kind is EventKind.APPROVAL_NOTIFY_FAILED]
    assert len(failed) == 1 and failed[0].reason == "approval_channel_failed"
    assert "broken" in caplog.text


async def test_log_channel_logs_ids_not_args(caplog: pytest.LogCaptureFixture) -> None:
    request = _request()
    with caplog.at_level(logging.INFO):
        await LogChannel().notify(request)
    assert request.request_id in caplog.text and "tool:payment.send" in caplog.text
    assert "user:alice" in caplog.text


async def test_webhook_posts_signed_json() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    channel = WebhookChannel(
        "https://hooks.example/approvals",
        secret="s3cret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    request = _request()
    await channel.notify(request)
    [sent] = calls
    body = json.loads(sent.content)
    assert body["event"] == "approval.pending"
    assert body["request"]["request_id"] == request.request_id
    assert body["request"]["tool"] == "tool:payment.send"
    assert "args" not in body["request"]
    assert sent.headers["X-HeadOfContext-Event"] == "approval.pending"
    expected = hmac.new(b"s3cret", sent.content, hashlib.sha256).hexdigest()
    assert sent.headers["X-HeadOfContext-Signature"] == f"sha256={expected}"


@pytest.mark.parametrize("status", [500, 302, 404])
async def test_webhook_non_2xx_is_a_channel_error(status: int) -> None:
    channel = WebhookChannel(
        "https://hooks.example/approvals",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))),
    )
    with pytest.raises(ApprovalChannelError):
        await channel.notify(_request())


async def test_webhook_transport_error_is_a_channel_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    channel = WebhookChannel(
        "https://hooks.example/approvals",
        client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
    )
    with pytest.raises(ApprovalChannelError):
        await channel.notify(_request())


def test_build_channels_from_env_and_plugins() -> None:
    env = {
        "HOC_APPROVAL_CHANNELS": "log, webhook ,custom",
        "HOC_APPROVAL_WEBHOOK_URL": "https://hooks.example/x",
    }
    channels = build_channels(env, plugins={"custom": lambda _env: Recording()})
    assert [type(c).__name__ for c in channels] == ["LogChannel", "WebhookChannel", "Recording"]


def test_build_channels_rejects_unknown_and_incomplete() -> None:
    with pytest.raises(ApprovalChannelError):
        build_channels({"HOC_APPROVAL_CHANNELS": "teams"}, plugins={})
    with pytest.raises(ApprovalChannelError):
        build_channels({"HOC_APPROVAL_CHANNELS": "webhook"}, plugins={})
    assert build_channels({}, plugins={}) == []


def _request() -> ApprovalRequest:
    now = FrozenClock().now()
    return ApprovalRequest(
        request_id="req-1",
        subject="user:alice",
        actor="agent:assistant",
        delegation_depth=0,
        tool="tool:payment.send",
        args_hash="abc",
        decision_id="dec-1",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
