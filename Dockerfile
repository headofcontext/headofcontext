# HeadOfContext service image (ADR 0011). Runs uvicorn as a non-root user.
# Base images are pinned by digest; Dependabot (docker ecosystem) proposes the bumps.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.30@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
# Optional extras baked into the image (ADR 0025): e.g. --build-arg HOC_EXTRAS="mcp mem0 zep".
ARG HOC_EXTRAS="mcp"
COPY pyproject.toml uv.lock README.md ./
RUN set -eu; flags=""; for x in $HOC_EXTRAS; do flags="$flags --extra $x"; done; \
    uv sync --frozen --no-dev --no-install-project $flags
COPY src ./src
RUN set -eu; flags=""; for x in $HOC_EXTRAS; do flags="$flags --extra $x"; done; \
    uv sync --frozen --no-dev $flags

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
RUN useradd --create-home --uid 10001 hoc
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER hoc
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/ready', timeout=3).status == 200 else 1)"
# Default: the API. The sync runner overrides the command with `hoc sync`.
CMD ["uvicorn", "headofcontext.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
