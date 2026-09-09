"""AuthzEngine adapter over the OpenFGA SDK. Never reimplements evaluation; never guesses (I5)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from openfga_sdk import ClientConfiguration, OpenFgaClient
from openfga_sdk.client.models import (
    ClientBatchCheckItem,
    ClientBatchCheckRequest,
    ClientCheckRequest,
    ClientListObjectsRequest,
    ClientTuple,
)
from openfga_sdk.models.read_request_tuple_key import ReadRequestTupleKey

from headofcontext.core.blocking import offload
from headofcontext.core.engine import Tuple
from headofcontext.core.errors import EngineUnavailable, InvariantViolation
from headofcontext.core.refs import validate_ref, validate_resource_pattern
from headofcontext.engines.freshness import FreshnessGuard

log = logging.getLogger(__name__)


class OpenFgaEngine:
    """Adapter over the OpenFGA client.

    Transient transport failures are retried once (``retries``) after a short delay; a failure
    that survives the retry is an ``EngineUnavailable`` and therefore a denial (I5). Retrying
    never changes an answer, it only avoids denying on a blip.
    """

    def __init__(
        self,
        client: OpenFgaClient,
        freshness: FreshnessGuard,
        *,
        retries: int = 1,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        self._client = client
        self._freshness = freshness
        self._retries = max(0, retries)
        self._retry_delay = retry_delay_seconds

    async def _call(self, what: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        attempt = 0
        while True:
            try:
                return await operation()
            except Exception as exc:
                if attempt >= self._retries:
                    raise EngineUnavailable(f"openfga {what} failed: {type(exc).__name__}") from exc
                attempt += 1
                log.warning("openfga %s failed (%s); retrying once", what, type(exc).__name__)
                await asyncio.sleep(self._retry_delay)

    @classmethod
    def connect(
        cls,
        api_url: str,
        store_id: str,
        freshness: FreshnessGuard,
        *,
        authorization_model_id: str | None = None,
    ) -> OpenFgaEngine:
        configuration = ClientConfiguration(
            api_url=api_url, store_id=store_id, authorization_model_id=authorization_model_id
        )
        return cls(OpenFgaClient(configuration), freshness)

    async def check(self, subject: str, relation: str, obj: str) -> bool:
        await self._guard(subject, obj)
        request = ClientCheckRequest(user=subject, relation=relation, object=obj)
        response = await self._call("check", lambda: self._client.check(request))
        return bool(getattr(response, "allowed", False))

    async def batch_check(self, subject: str, items: Sequence[tuple[str, str]]) -> list[bool]:
        if not items:
            return []
        for _, obj in items:
            await self._guard(subject, obj)
        checks = [
            ClientBatchCheckItem(user=subject, relation=relation, object=obj, correlation_id=str(i))
            for i, (relation, obj) in enumerate(items)
        ]
        request = ClientBatchCheckRequest(checks=checks)
        response = await self._call("batch_check", lambda: self._client.batch_check(request))
        results: list[bool] = [False] * len(items)
        for single in _results(response):
            index = int(getattr(single, "correlation_id", -1))
            if getattr(single, "error", None) is not None:
                # A per-item error is a denial for that item, never an allow.
                log.warning("openfga batch item %d errored", index)
                continue
            if 0 <= index < len(results):
                results[index] = bool(getattr(single, "allowed", False))
        return results

    async def list_objects(self, subject: str, relation: str, type_: str) -> list[str]:
        await self._guard(subject, f"{type_}:*")
        request = ClientListObjectsRequest(user=subject, relation=relation, type=type_)
        response = await self._call("list_objects", lambda: self._client.list_objects(request))
        return [str(o) for o in getattr(response, "objects", [])]

    # -- TupleStore --------------------------------------------------------------------------

    async def write_tuples(self, tuples: Sequence[Tuple]) -> None:
        body = [ClientTuple(user=u, relation=r, object=o) for u, r, o in self._validated(tuples)]
        if not body:
            return
        await self._call("write", lambda: self._client.write_tuples(body))

    async def delete_tuples(self, tuples: Sequence[Tuple]) -> None:
        body = [ClientTuple(user=u, relation=r, object=o) for u, r, o in self._validated(tuples)]
        if not body:
            return
        await self._call("delete", lambda: self._client.delete_tuples(body))

    async def read_tuples(self, obj: str, relation: str) -> list[Tuple]:
        validate_resource_pattern(obj)
        _validate_relation(relation)
        found: list[Tuple] = []
        continuation: str | None = None
        try:
            while True:
                options: dict[str, Any] = {}
                if continuation:
                    options["continuation_token"] = continuation
                key = ReadRequestTupleKey(object=obj, relation=relation)  # type: ignore[no-untyped-call]
                response = await self._client.read(key, options or None)
                for item in getattr(response, "tuples", None) or []:
                    key = item.key
                    found.append((str(key.user), str(key.relation), str(key.object)))
                continuation = getattr(response, "continuation_token", None) or None
                if not continuation:
                    break
        except Exception as exc:
            raise EngineUnavailable(f"openfga read failed: {type(exc).__name__}") from exc
        return found

    @staticmethod
    def _validated(tuples: Sequence[Tuple]) -> list[Tuple]:
        out: list[Tuple] = []
        for user, relation, obj in tuples:
            # user may be "type:id" or "type:id#relation"; both halves are validated.
            base, _, userset = user.partition("#")
            validate_resource_pattern(base)
            if userset:
                _validate_relation(userset)
            _validate_relation(relation)
            validate_resource_pattern(obj)
            out.append((user, relation, obj))
        return out

    async def ping(self) -> None:
        """Reach the store once, for the readiness probe.

        With a pinned model id the probe reads *that* model, so a store/model mismatch (a model
        id from another store) is "not ready" instead of a ValidationException on every check.
        """
        pinned = self._client.get_authorization_model_id()  # type: ignore[no-untyped-call]
        if pinned:
            await self._call("read_authorization_model", self._client.read_authorization_model)
        else:
            await self._call(
                "read_authorization_models",
                lambda: self._client.read_authorization_models(options={"page_size": 1}),
            )

    async def close(self) -> None:
        await self._client.close()  # type: ignore[no-untyped-call]

    async def _guard(self, subject: str, obj: str) -> None:
        # I3: an engine question is only ever asked about a human subject.
        try:
            validate_ref(subject, "user")
        except InvariantViolation:
            raise InvariantViolation(f"engine subject must be a user, got {subject!r}") from None
        validate_resource_pattern(obj)
        # The PostgreSQL-backed guard reads the state table: off the loop (ADR 0022).
        await offload(self._freshness.assert_fresh)


def _validate_relation(relation: str) -> None:
    if not relation.isidentifier():
        raise InvariantViolation(f"invalid relation {relation!r}")


def _results(response: Any) -> list[Any]:
    result = getattr(response, "result", None)
    return list(result) if result is not None else []
