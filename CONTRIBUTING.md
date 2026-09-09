# Contributing

The rules live in [`AGENTS.md`](AGENTS.md): what the project is and is not, the frozen stack,
the invariants, the security rules, the method (ADR before code, tests before implementation,
adversarial self-review, real services in integration tests), the commands and the definition
of done. Read it first.

In short:

1. Open an ADR in `docs/adr/` for anything that changes behaviour; discuss it before code.
2. Write the test, see it fail, implement, run `uv run ruff check . && uv run mypy --strict src`
   and the suites (`tests/unit tests/adversarial`, then integration and golden against the
   local stack).
3. One concern per pull request, Conventional Commits, in English, with the why. Merges are
   squashed: the pull request title becomes the commit on `main` and decides the next version
   (`feat` bumps the minor, `fix` the patch; `feat!:` marks a breaking change), and the release
   notes are generated from it. There is no changelog file to edit (ADR 0028).
4. Never weaken an invariant, never add a dependency without a justification, never copy code
   under a licence incompatible with Apache 2.0.

Security issues go through [`SECURITY.md`](SECURITY.md), not the issue tracker.
