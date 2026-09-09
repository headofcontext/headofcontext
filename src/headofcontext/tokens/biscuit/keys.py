"""Root key material. The private key never leaves the issuer; verifiers hold public keys only."""

from __future__ import annotations

import biscuit_auth

from headofcontext.core.errors import TokenInvalid


class KeyRing:
    """Maps ``root_key_id`` to public keys; optionally holds one private key for issuing."""

    def __init__(self) -> None:
        self._public: dict[int, biscuit_auth.PublicKey] = {}
        self._private: dict[int, biscuit_auth.PrivateKey] = {}

    @classmethod
    def generate(cls, key_id: int) -> KeyRing:
        ring = cls()
        ring.add_private_key(key_id, biscuit_auth.KeyPair().private_key)
        return ring

    @classmethod
    def from_private_key_bytes(cls, key_id: int, raw: bytes) -> KeyRing:
        ring = cls()
        # biscuit-python's stubs lag the runtime: from_bytes needs the algorithm since 0.4.
        algorithm = biscuit_auth.Algorithm.Ed25519  # type: ignore[attr-defined]
        ring.add_private_key(
            key_id,
            biscuit_auth.PrivateKey.from_bytes(raw, algorithm),  # type: ignore[call-arg]
        )
        return ring

    def add_private_key(self, key_id: int, private_key: biscuit_auth.PrivateKey) -> None:
        pair = biscuit_auth.KeyPair.from_private_key(private_key)
        self._private[key_id] = private_key
        self._public[key_id] = pair.public_key

    def add_public_key(self, key_id: int, public_key: biscuit_auth.PublicKey) -> None:
        self._public[key_id] = public_key

    def public_key(self, key_id: int | None) -> biscuit_auth.PublicKey:
        if key_id is None or key_id not in self._public:
            raise TokenInvalid(f"unknown root key id {key_id!r}")
        return self._public[key_id]

    def private_key(self, key_id: int) -> biscuit_auth.PrivateKey:
        try:
            return self._private[key_id]
        except KeyError:
            raise TokenInvalid(f"no private key for key id {key_id}") from None

    @staticmethod
    def public_key_from_bytes(raw: bytes) -> biscuit_auth.PublicKey:
        algorithm = biscuit_auth.Algorithm.Ed25519  # type: ignore[attr-defined]
        return biscuit_auth.PublicKey.from_bytes(raw, algorithm)  # type: ignore[call-arg]

    def key_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._public))

    def public_key_bytes(self, key_id: int) -> bytes:
        return self.public_key(key_id).to_bytes()
