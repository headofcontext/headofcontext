"""Run a blocking call in a thread so the event loop keeps serving (ADR 0022).

Every store keeps a synchronous API; async code calls it through ``offload``. The pool behind
the stores is thread-safe, so concurrent offloads really run concurrently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def offload[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    return await asyncio.to_thread(fn, *args, **kwargs)


__all__ = ["offload"]
