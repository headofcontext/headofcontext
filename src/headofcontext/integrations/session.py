"""AgentSession: the unit every integration uses (ADR 0008).

Every tool call verifies the biscuit for (caller, act, tool), rebuilds the chain from the signed
trail and runs the action gate. Nothing is cached between calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from headofcontext.actions import ActionGate, GateResult
from headofcontext.core import Decision, Kind, PrincipalChain, Scope
from headofcontext.core.blocking import offload
from headofcontext.core.errors import ActionDenied, InvariantViolation, TokenInvalid
from headofcontext.core.events import AuditEvent, EventKind, fingerprint
from headofcontext.core.refs import validate_ref
from headofcontext.tokens.biscuit import TokenService

TOOL_PREFIX = "tool:"


class AgentSession:
    def __init__(
        self,
        *,
        token: str,
        caller: str,
        token_service: TokenService,
        gate: ActionGate,
        revocation_ids: tuple[str, ...] = (),
    ) -> None:
        validate_ref(caller, "agent")
        self.token = token
        self.caller = caller
        self._tokens = token_service
        self._gate = gate
        self.revocation_ids = revocation_ids

    @property
    def gate(self) -> ActionGate:
        return self._gate

    async def chain(self) -> PrincipalChain:
        """The chain proven by the token, for display or delegation. Raises TokenInvalid."""
        verified = await offload(self._tokens.inspect, self.token, caller=self.caller)
        return verified.chain

    def permits(self, operation: Kind, resources: Sequence[str]) -> dict[str, bool]:
        """Per-resource answer of the token's checks (ADR 0018), for the memory service."""
        return self._tokens.verify_many(
            self.token, caller=self.caller, operation=operation, resources=resources
        )[1]

    async def authorize(self, tool: str, args: Mapping[str, Any] | None = None) -> GateResult:
        """Verify the token for this tool, then run the gate. ActionDenied on token errors."""
        resource = tool_resource(tool)
        chain = await offload(self._verify, resource, args)
        return await self._gate.gate(chain, resource, args)

    async def redeem(
        self, request_id: str, tool: str, args: Mapping[str, Any] | None = None
    ) -> Decision:
        resource = tool_resource(tool)
        chain = await offload(self._verify, resource, args)
        return await self._gate.redeem(chain, request_id, args, tool=resource)

    def delegate(self, to_actor: str, scope: Scope) -> AgentSession:
        """Hand an attenuated token to a sub-agent. Raises ScopeEscalation on widening (I1)."""
        issued = self._tokens.attenuate(self.token, to_actor=to_actor, scope=scope)
        return AgentSession(
            token=issued.token,
            caller=to_actor,
            token_service=self._tokens,
            gate=self._gate,
            revocation_ids=issued.revocation_ids,
        )

    def _verify(self, resource: str, args: Mapping[str, Any] | None) -> PrincipalChain:
        try:
            verified = self._tokens.verify(
                self.token, caller=self.caller, operation=Kind.ACT, resource=resource
            )
        except TokenInvalid as exc:
            # No chain to attach: the token did not prove one. Still never an unlogged denial.
            self._gate.audit_raw(
                AuditEvent(
                    kind=EventKind.DECISION,
                    timestamp=self._gate.now(),
                    subject="-",
                    actor=self.caller,
                    delegation_depth=0,
                    action="act:can_invoke",
                    resource=resource,
                    outcome="DENY",
                    reason=exc.reason,
                    token_fingerprint=fingerprint(self.token),
                )
            )
            raise ActionDenied(f"tool call refused: {exc.reason}", reason=exc.reason) from exc
        return verified.chain


def tool_resource(tool: str) -> str:
    """Framework tool name → ``tool:<name>``. Invalid names are refused, never sanitized."""
    name = tool if tool.startswith(TOOL_PREFIX) else TOOL_PREFIX + tool
    try:
        return validate_ref(name, "tool")
    except InvariantViolation as exc:
        raise ActionDenied(f"invalid tool name {tool!r}", reason="invalid_tool") from exc
