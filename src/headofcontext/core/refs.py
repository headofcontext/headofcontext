"""Object references in the OpenFGA style: ``type:id``."""

from __future__ import annotations

import re

from headofcontext.core.errors import InvalidResource, InvariantViolation

# type is a lowercase identifier; id is a conservative charset (no quotes, spaces, backslashes)
# so that references can be embedded in datalog, SQL and logs without surprises. A single
# trailing '*' turns the id into a prefix pattern.
_ID_CHARS = r"[A-Za-z0-9._~/@+=-]"
_RESOURCE_RE = re.compile(rf"^(?P<type>[a-z][a-z0-9_-]*):(?P<id>{_ID_CHARS}*\*?)$")
_PLAIN_RE = re.compile(rf"^(?P<type>[a-z][a-z0-9_-]*):(?P<id>{_ID_CHARS}+)$")


def validate_resource_pattern(value: str) -> str:
    """Accept ``type:id`` or ``type:prefix*`` (``type:*`` included). Reject anything else."""
    m = _RESOURCE_RE.match(value)
    if m is None or m.group("id") == "":
        raise InvalidResource(f"invalid resource pattern {value!r}")
    return value


def validate_ref(value: str, expected_type: str) -> str:
    """Accept a concrete ``expected_type:id`` reference (no wildcard, non-empty id)."""
    m = _PLAIN_RE.match(value)
    if m is None or m.group("type") != expected_type:
        raise InvariantViolation(f"expected a {expected_type!r} reference, got {value!r}")
    return value


def ref_type(value: str) -> str:
    return value.split(":", 1)[0]
