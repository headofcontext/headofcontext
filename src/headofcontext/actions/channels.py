"""Approval channels (ADR 0014): tell a human that a request is waiting.

A channel is told *after* the request is persisted and audited and it can never change the
decision: a failing channel is recorded and skipped. Channels receive request metadata only,
never the tool arguments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

import httpx

from headofcontext.actions.approvals import ApprovalRequest
from headofcontext.core.errors import HocError
from headofcontext.plugins import discover

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "headofcontext.approval_channels"
ChannelFactory = Callable[[Mapping[str, str]], "ApprovalChannel"]


class ApprovalChannelError(HocError):
    reason = "approval_channel_failed"


@runtime_checkable
class ApprovalChannel(Protocol):
    name: str

    async def notify(self, request: ApprovalRequest) -> None: ...


def request_payload(request: ApprovalRequest) -> dict[str, Any]:
    """The public shape of a request: identifiers, hashes and timestamps, no arguments."""
    return {
        "request_id": request.request_id,
        "subject": request.subject,
        "actor": request.actor,
        "delegation_depth": request.delegation_depth,
        "tool": request.tool,
        "args_hash": request.args_hash,
        "decision_id": request.decision_id,
        "status": str(request.status),
        "approval_reason": request.approval_reason,
        "created_at": request.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
    }


class LogChannel:
    name = "log"

    async def notify(self, request: ApprovalRequest) -> None:
        log.info(
            "approval pending request_id=%s tool=%s subject=%s actor=%s depth=%d expires_at=%s",
            request.request_id,
            request.tool,
            request.subject,
            request.actor,
            request.delegation_depth,
            request.expires_at.isoformat(),
        )


class WebhookChannel:
    """POST the request as JSON; HMAC-SHA256 signature over the body when a secret is set."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        secret: str | None = None,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._secret = secret.encode() if secret else None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def notify(self, request: ApprovalRequest) -> None:
        body = json.dumps(
            {"event": "approval.pending", "request": request_payload(request)},
            separators=(",", ":"),
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-HeadOfContext-Event": "approval.pending",
        }
        if self._secret is not None:
            digest = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
            headers["X-HeadOfContext-Signature"] = f"sha256={digest}"
        try:
            response = await self._client.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ApprovalChannelError(f"webhook unreachable: {type(exc).__name__}") from exc
        if not 200 <= response.status_code < 300:
            raise ApprovalChannelError(f"webhook answered {response.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()


def build_channels(
    env: Mapping[str, str], *, plugins: Mapping[str, ChannelFactory] | None = None
) -> list[ApprovalChannel]:
    """``HOC_APPROVAL_CHANNELS=log,webhook,<plugin>`` → channels, in that order.

    Plugins come from the ``headofcontext.approval_channels`` entry point group unless given.
    """
    names = [n.strip() for n in env.get("HOC_APPROVAL_CHANNELS", "").split(",") if n.strip()]
    if not names:
        return []
    factories: dict[str, ChannelFactory] = {
        "log": lambda _env: LogChannel(),
        "webhook": _webhook_from_env,
    }
    for name, plugin in (discover(ENTRY_POINT_GROUP) if plugins is None else plugins).items():
        factories.setdefault(name, plugin)  # built-ins cannot be shadowed (ADR 0019)
    channels: list[ApprovalChannel] = []
    for name in names:
        factory = factories.get(name)
        if factory is None:
            raise ApprovalChannelError(f"unknown approval channel {name!r}")
        channels.append(factory(env))
    return channels


def _webhook_from_env(env: Mapping[str, str]) -> ApprovalChannel:
    url = env.get("HOC_APPROVAL_WEBHOOK_URL")
    if not url:
        raise ApprovalChannelError("HOC_APPROVAL_WEBHOOK_URL is required for the webhook channel")
    return WebhookChannel(url, secret=env.get("HOC_APPROVAL_WEBHOOK_SECRET") or None)
