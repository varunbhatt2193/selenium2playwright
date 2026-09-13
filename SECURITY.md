# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
reporting instead:
[Report a vulnerability](https://github.com/varunbhatt2193/selenium2playwright/security/advisories/new).
You will get a reply within a week, and a fix or a plan within thirty days for
anything confirmed.

If that form is unavailable, email bhattvarunk@gmail.com with
"selenium2playwright security" in the subject line.

## Scope

- `main` is the only supported branch. There are no versioned releases yet.
- The code under `src/`, `ui/` and `deploy/` is in scope.
- The hosted demo at https://varun-s2p.fly.dev is one small machine paid for
  by the maintainer. Please do not load-test it, try to exhaust its daily
  budget, or probe it beyond what a normal user does. Report what you found
  instead. A clone runs the same code on your own machine, with your own key.

## What is already in place

Secret scanning and push protection, CodeQL on every push and pull request,
Dependabot alerts and updates, and a pull-request-only `main` with six required
checks. Details in [docs/github-security.md](docs/github-security.md) and
[docs/guardrails.md](docs/guardrails.md).
