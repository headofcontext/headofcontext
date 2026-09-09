# Security policy

HeadOfContext is an authorization layer: a bypass is a vulnerability, a denial that should have
been an allow is a bug. Both matter; only the first is a security issue.

## Reporting a vulnerability

Do not open a public issue. Send the report to the address published on the project's
repository page (security contact) with: the affected version or commit, the invariant or threat
you believe is broken (`docs/threat-model.md`, `AGENTS.md` I1–I5), a reproduction, and the impact
you observed. You will get an acknowledgement within three working days and a fix or a
mitigation plan within thirty days for confirmed issues. Coordinated disclosure is the default;
we will credit you unless you prefer otherwise.

## What is in scope

- Any way for an agent to read, do or remember more than the user it acts for may (I1–I3).
- Any decision reached without a journal entry, or a journal that can be altered silently.
- Any way to keep a right after its revocation (I4), or any error path that allows instead of
  denying (I5).
- Token forgery, replay, or attenuation that widens rights; approval or mandate state that can
  be reused or flipped.
- Secrets or document content leaking into logs, audit events or error messages.

## What is out of scope

Denial of service against the IdP or PostgreSQL, compromise of the OpenFGA database itself,
physical access to the host, and findings in the fictional ACME fixtures.

## Supported versions

The latest release and the `main` branch. Fixes land with a threat-model row and an adversarial
test, as every security change in this repository does (`AGENTS.md`, rule 7).
