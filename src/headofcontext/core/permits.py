"""What the token holder's checks say, per resource (ADR 0018, 0027).

Built from ``TokenService.verify_many``; asked by the services where the resources become
known, off the event loop. A resource missing from the answer counts as refused.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from headofcontext.core.scope import Kind

ResourcePermits = Callable[[Kind, Sequence[str]], Mapping[str, bool]]

__all__ = ["ResourcePermits"]
