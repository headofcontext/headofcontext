# ADR 0028 — Versioning and releases from Conventional Commits

Status: Accepted — 2026-09-09

## Context

The repository is public with a single hand-written `CHANGELOG.md`, a `version` duplicated in
`pyproject.toml`, `src/headofcontext/__init__.py` and the chart's `appVersion`, and an image job
that only ran on a `v*` tag nobody pushed. Merges are squash-only, so `main` carries exactly one
Conventional Commit per pull request: the information needed to compute the next version and
the release notes is already there.

## Decision

- **release-please** (`googleapis/release-please-action`, `release-type: python`) runs on every
  push to `main`. It keeps one release pull request open; merging it bumps the version, tags
  `vX.Y.Z` and publishes a GitHub release whose notes are generated from the commits.
- The version has one source, `pyproject.toml`. release-please updates `__init__.py` itself
  (Python strategy), and `extra-files` cover the chart's `appVersion` and the root package entry
  of `uv.lock`, both by jsonpath, so a release commit leaves `uv sync --locked` clean.
  (Amended 2026-09-09: the first release PR showed that a bare path routes a `.yaml` file to
  the YAML updater with `$.version`, and that the format-preserving TOML parser wraps values,
  so the lock filter is `@.name.value`.)
- `CHANGELOG.md` is removed (`skip-changelog`). The release notes live on the GitHub release;
  `pyproject.toml` points its `Changelog` URL at the releases page.
- Pre-1.0: `bump-minor-pre-major`. A `feat` bumps the minor, a `fix` bumps the patch, a
  breaking change bumps the minor until 1.0.0.
- Tags are `vX.Y.Z` without component (`include-component-in-tag: false`).
- The container image is built and pushed to ghcr.io by a job chained after the release job in
  the same workflow (`:vX.Y.Z` and `:latest`). It is not triggered by the tag: a tag created with
  `GITHUB_TOKEN` never fires another workflow.
- The chart's own `version` is bumped by hand when the chart changes; `appVersion` follows the
  release.
- The SDK keeps its own version in its own repository; it follows the OpenAPI contract, not the
  core release cadence.

## Consequences

- The pull request title is the commit subject on `main` and therefore decides the bump: it
  must be a valid Conventional Commit line (`feat(scope): …`, `fix(scope): …`, `docs: …`,
  `chore: …`). `feat!:` or a `BREAKING CHANGE:` footer in the PR body marks a breaking change.
- The definition of done no longer includes a changelog edit.
- The repository must allow GitHub Actions to create pull requests (organisation and repository
  setting), otherwise the release PR is never opened.
- Publishing to PyPI is a later, separate job chained the same way (trusted publishing).
