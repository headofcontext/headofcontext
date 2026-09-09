"""Root key rotation (ADR 0015): old tokens verify while the old public key stays in the ring."""

from __future__ import annotations

from datetime import timedelta

import biscuit_auth
import pytest

from headofcontext.audit import InMemoryAuditSink
from headofcontext.core import Capability, Kind, PrincipalChain, Scope
from headofcontext.core.errors import ConfigurationError, TokenInvalid
from headofcontext.services import build_keyring
from headofcontext.settings import Settings
from headofcontext.tokens.biscuit import InMemoryRevocationStore, KeyRing, TokenService

CHAIN = PrincipalChain.root("user:alice", "agent:a", Scope.of(Capability(Kind.ACT, "tool:*")))

BASE = {
    "HOC_POSTGRES_DSN": "postgresql://x",
    "HOC_OPENFGA_URL": "http://x",
    "HOC_OPENFGA_STORE_ID": "s",
    "HOC_OIDC_ISSUER": "http://x",
    "HOC_OIDC_CLIENT_ID": "c",
    "HOC_OIDC_CLIENT_SECRET": "z",
}


def _service(ring: KeyRing, key_id: int) -> TokenService:
    return TokenService(
        ring, InMemoryRevocationStore(), InMemoryAuditSink(), key_id=key_id, ttl=timedelta(hours=1)
    )


def test_settings_parse_public_keys() -> None:
    s = Settings.from_env({**BASE, "HOC_ROOT_PUBLIC_KEYS": " 1=aa, 2=bb ,"})
    assert s.root_public_keys == ((1, "aa"), (2, "bb"))
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE, "HOC_ROOT_PUBLIC_KEYS": "one=aa"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE, "HOC_ROOT_PUBLIC_KEYS": "1"})


def test_rotation_keeps_old_tokens_valid_then_retires_them() -> None:
    old_raw = biscuit_auth.KeyPair().private_key.to_bytes()
    new_raw = biscuit_auth.KeyPair().private_key.to_bytes()
    old_ring = build_keyring(Settings.from_env({**BASE, "HOC_ROOT_KEY_HEX": old_raw.hex()}))
    old_public = old_ring.public_key_bytes(1).hex()
    token = _service(old_ring, 1).issue(CHAIN).token

    # Step 2: sign with key 2, still verify key 1.
    rotated = build_keyring(
        Settings.from_env(
            {
                **BASE,
                "HOC_ROOT_KEY_HEX": new_raw.hex(),
                "HOC_ROOT_KEY_ID": "2",
                "HOC_ROOT_PUBLIC_KEYS": f"1={old_public}",
            }
        )
    )
    assert rotated.key_ids() == (1, 2)
    service = _service(rotated, 2)
    assert service.inspect(token, caller="agent:a").chain.subject == "user:alice"
    fresh = service.issue(CHAIN).token
    assert service.inspect(fresh, caller="agent:a").chain.subject == "user:alice"

    # Step 4: drop key 1; its tokens die, key 2 tokens live on.
    retired = build_keyring(
        Settings.from_env({**BASE, "HOC_ROOT_KEY_HEX": new_raw.hex(), "HOC_ROOT_KEY_ID": "2"})
    )
    retired_service = _service(retired, 2)
    with pytest.raises(TokenInvalid):
        retired_service.inspect(token, caller="agent:a")
    assert retired_service.inspect(fresh, caller="agent:a").chain.subject == "user:alice"


def test_public_keys_cannot_sign() -> None:
    other = biscuit_auth.KeyPair().private_key.to_bytes()
    public = KeyRing.from_private_key_bytes(5, other).public_key_bytes(5).hex()
    ring = build_keyring(
        Settings.from_env(
            {**BASE, "HOC_ROOT_PUBLIC_KEYS": f"5={public}", "HOC_ALLOW_EPHEMERAL_ROOT_KEY": "true"}
        )
    )
    with pytest.raises(TokenInvalid):
        _service(ring, 5).issue(CHAIN)


def test_keys_public_command(capsys: pytest.CaptureFixture[str]) -> None:
    from headofcontext.cli import main

    raw = biscuit_auth.KeyPair().private_key.to_bytes()
    assert main(["--env-file", "/dev/null", "keys", "public", "--key-hex", raw.hex()]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == KeyRing.from_private_key_bytes(1, raw).public_key_bytes(1).hex()


def test_self_approval_setting_defaults_to_four_eyes() -> None:
    assert Settings.from_env(BASE).allow_self_approval is False
    assert Settings.from_env({**BASE, "HOC_ALLOW_SELF_APPROVAL": "true"}).allow_self_approval


def test_missing_root_key_refuses_to_start_unless_explicitly_allowed() -> None:
    from headofcontext.services import build_keyring

    base = dict(
        postgres_dsn="postgresql://x",
        openfga_url="http://fga",
        openfga_store_id="s",
        oidc_issuer="http://idp",
        oidc_audience="hoc",
        oidc_client_id="hoc",
        oidc_client_secret="secret",
    )
    with pytest.raises(ConfigurationError, match="HOC_ROOT_KEY_HEX"):
        build_keyring(Settings(**base))
    ring = build_keyring(Settings(**base, allow_ephemeral_root_key=True))
    assert ring.key_ids() == (1,)
