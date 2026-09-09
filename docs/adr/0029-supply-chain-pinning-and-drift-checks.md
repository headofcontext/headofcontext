# ADR 0029 — Supply chain: pinned actions and images, locked installs, drift checks

Status: Accepted — 2026-09-09

## Context

The repository is public and its workflows hold write rights: the release job creates tags and
releases, the image job pushes to ghcr.io. Third-party actions were referenced by mutable tags,
the base images by floating tags, CI resolved dependencies afresh instead of using the lock,
and nothing checked that the generated artefacts committed to the tree (`docs/openapi.json`,
`docs/authz-model.json` and its packaged copy) still matched their sources.

## Decision

- Every `uses:` in the workflows is pinned to a full commit SHA with the version in a trailing
  comment. The repository's Actions policy is an allow-list (GitHub-owned actions plus the few
  named third parties) and, once the workflows are pinned, requires SHA pinning.
- The base images of the `Dockerfile` are pinned by digest.
- Dependabot watches GitHub Actions, the `uv` lock and the Dockerfile weekly, with
  `chore(deps)` commits so that dependency bumps never trigger a release on their own.
- CI installs with `uv sync --locked`: a pull request that changes `pyproject.toml` without
  updating `uv.lock` fails.
- CI fails when a generated artefact drifts: `scripts/export_openapi.py` and
  `scripts/fga-model.sh` are run and `git diff --exit-code` guards their outputs.
- Runs are serialised per branch (`concurrency`, cancel in progress) and every job has a
  timeout.
- The release image is built with BuildKit provenance (`mode=max`) and an SBOM, and a signed
  build provenance attestation is pushed to the registry next to the image.

## Consequences

- Bumping an action means changing a SHA; Dependabot proposes those bumps.
- A contributor who edits dependencies must commit the lock in the same pull request.
- Consumers can verify the image with `gh attestation verify oci://ghcr.io/headofcontext/headofcontext:vX.Y.Z --owner headofcontext`.
- The development stack in `docker-compose.yml` keeps floating tags: it never ships.
