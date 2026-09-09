"""Export the OpenAPI contract to docs/openapi.json (the SDK is written against it)."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

from headofcontext.api import Settings, create_app

OUT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def main() -> int:
    settings = Settings(
        postgres_dsn="postgresql://unused",
        openfga_url="http://unused",
        openfga_store_id="unused",
        oidc_issuer="http://unused/realms/unused",
        oidc_audience="headofcontext",
        oidc_client_id="unused",
        oidc_client_secret="unused",  # noqa: S106
        token_ttl=timedelta(hours=1),
    )
    spec = create_app(settings).openapi()
    OUT.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
