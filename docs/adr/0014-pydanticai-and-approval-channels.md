# ADR 0014 — PydanticAI integration and approval channels

Status: Accepted — 2026-09-07

## Context

Two gaps after ADR 0008/0009/0013. First, PydanticAI is the third framework the brief names and
had no integration. Second, a `REQUIRE_APPROVAL` decision lands in the approval store and nothing
tells a human: the core "provides the state, not the UI", but an operator still needs a hook to
route the request to wherever approvals happen. The paid tier owns Teams/Slack/ServiceNow
(docs/boundary.md); the free tier needs the seam and a generic transport.

## Decision

### PydanticAI: `HocToolset`

`headofcontext.integrations.pydanticai.HocToolset` wraps any PydanticAI toolset
(`WrapperToolset`) and authorizes every `call_tool` through `ToolGuard` before delegating to the
wrapped toolset. Tool definitions are passed through unchanged. A denied or pending call returns
the same refusal message as the LangGraph and CrewAI integrations (`on_deny="message"`, default)
or raises `ActionDenied` / `ApprovalPending` (`on_deny="raise"`). `guard_toolset(toolset,
session, tool_map=...)` is the one-liner. Optional extra `pydanticai` (`pydantic-ai-slim`, MIT).

PydanticAI's own `approval_required` / deferred-tools mechanism is not used: HeadOfContext's
approvals are argument-bound, persisted and resolved by a human outside the run, and the core
must not depend on a framework's run loop to remember them.

### Approval channels

`headofcontext.actions.ApprovalChannel` is a Protocol with one method:
`async notify(request: ApprovalRequest) -> None`, called by `ActionGate` right after a pending
request is persisted and audited. Channels see the request metadata (id, subject, actor, tool,
args hash, expiry), never the arguments themselves.

Rules:

- **Notification never changes the decision.** The request is already stored; a channel that
  fails raises `ApprovalChannelError`, which the gate records (`APPROVAL_NOTIFY_FAILED` audit
  event, warning log) and moves on. Fail-closed applies to authorization, not to messaging: a
  broken webhook must not turn every approval-gated call into a silent DENY, nor into an ALLOW.
- Channels are called sequentially, in configuration order, each with its own error boundary.
- The core ships two channels: `LogChannel` (a structured log line) and `WebhookChannel`
  (`POST` JSON to a URL, `X-HeadOfContext-Event: approval.pending`, body signed with
  `X-HeadOfContext-Signature: sha256=<hmac>` when a secret is configured, 2xx required).
- Other channels plug in through the entry point group `headofcontext.approval_channels`: the
  entry point names a factory `Callable[[Mapping[str, str]], ApprovalChannel]` receiving the
  process environment. `HOC_APPROVAL_CHANNELS=log,webhook,<plugin>` selects and orders them;
  `HOC_APPROVAL_WEBHOOK_URL` / `HOC_APPROVAL_WEBHOOK_SECRET` configure the webhook.

Resolution is not broadcast: the resolver (CLI or API) already knows, and consumers that need
the outcome poll `GET /v1/approvals` or the store. That is a possible follow-up, not a gap in
authorization.

## Consequences

- Teams/Slack/ServiceNow channels (paid) are ordinary entry points; the free webhook is enough
  to reach any of them through a relay, which keeps the boundary honest without being hostile.
- `ActionGate` gains a `channels` parameter; existing callers are unchanged.

## Amendment — 2026-09-08

`allow_self_approval` is now configurable through `HOC_ALLOW_SELF_APPROVAL` (Helm
`config.allowSelfApproval`). Default stays `false` (four eyes). A solo operator, the only human
their agents act for, sets it to `true` to approve their own agents' actions; the approval is
still single-use, argument-bound and journaled with the approver's identity.
