"""One psycopg connection pool per process and DSN (ADR 0022).

Every store checks a connection out for the duration of one statement or one transaction and
hands it back; the pool is thread-safe, which is what lets the async code run store calls in
threads without serializing them on a single connection.
"""

from __future__ import annotations

import threading

from psycopg_pool import ConnectionPool

DEFAULT_MAX_SIZE = 8
CHECKOUT_TIMEOUT_SECONDS = 5.0

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()
_max_size = DEFAULT_MAX_SIZE
_timeout = CHECKOUT_TIMEOUT_SECONDS


def configure(*, max_size: int, timeout: float | None = None) -> None:
    """Pool size and checkout timeout for pools created from now on (`HOC_DB_POOL_SIZE`)."""
    global _max_size, _timeout  # noqa: PLW0603 — process-wide, applied before any pool exists
    _max_size = max(1, max_size)
    if timeout is not None:
        _timeout = timeout


def get_pool(conninfo: str) -> ConnectionPool:
    with _lock:
        pool = _pools.get(conninfo)
        if pool is None:
            pool = ConnectionPool(
                conninfo,
                min_size=1,
                max_size=_max_size,
                timeout=_timeout,
                kwargs={"autocommit": True},
                open=True,
            )
            _pools[conninfo] = pool
        return pool


def close_pools() -> None:
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


__all__ = ["DEFAULT_MAX_SIZE", "close_pools", "configure", "get_pool"]
