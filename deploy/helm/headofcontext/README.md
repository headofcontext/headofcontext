# HeadOfContext Helm chart

```bash
# 1. Database roles (once, as a superuser, after the first migration): scripts/db-roles.sql
# 2. OpenFGA store and model (once, from a workstation): hoc model load --url ... --no-write-env
# 3. Install: the pre-install hook migrates the schema, then the service starts read-only on DDL.
helm install hoc deploy/helm/headofcontext \
  --set openfga.storeId=01... --set oidc.issuer=https://idp/realms/acme \
  --set existingSecret=hoc-secrets            # HOC_POSTGRES_DSN, HOC_OIDC_CLIENT_SECRET, HOC_ROOT_KEY_HEX
```

Then point `sync.connectors` at your directory and sources, set `sync.enabled: true` and
`config.connectorsRequired: true`. Values are documented inline in `values.yaml`; the deployment
guide (published with the documentation) covers probes, migrations, roles, retention and network policy.
