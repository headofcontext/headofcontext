# Rotating the root key

Biscuits carry the id of the root key that signed them, and the service verifies with any key
in its ring. Rotation is therefore a configuration sequence; no token is re-issued and no agent
is cut off, as long as the old public key stays in the ring for one token TTL
(`HOC_TOKEN_TTL_MINUTES`, default 60).

```
hoc keys generate                     # new private key, keep it in your secret store
hoc keys public --key-hex <new-hex>   # its public key, safe to distribute
```

1. **Publish the new public key** on every replica, without changing the signer:
   `HOC_ROOT_PUBLIC_KEYS="2=<new-public-hex>"` (keep `HOC_ROOT_KEY_HEX` / `HOC_ROOT_KEY_ID=1`).
   Roll out. Every replica can now verify tokens signed by key 2.
2. **Switch the signer**: `HOC_ROOT_KEY_HEX=<new-private-hex>`, `HOC_ROOT_KEY_ID=2`,
   `HOC_ROOT_PUBLIC_KEYS="1=<old-public-hex>"`. Roll out. New tokens use key 2; tokens signed by
   key 1 still verify.
3. **Wait one token TTL** (plus the approval TTL if pending approvals must survive).
4. **Retire key 1**: remove it from `HOC_ROOT_PUBLIC_KEYS`. Roll out. Any token still signed by
   key 1 is refused with `unknown root key id`, and that refusal is audited like any denial.

If the private key leaked, skip the waiting: go straight to step 4 with the new key and revoke
nothing else, since every token signed by the old key is now invalid. Revocation ids are
independent of the key and keep working across rotations.

Helm: `secrets.rootKeyHex`, `config.rootKeyId`, `config.rootPublicKeys` map to the variables
above.
