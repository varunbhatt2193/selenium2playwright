# Enable GitHub security for Selenium2Playwright

Prepared September 7, 2026 for the public repository
[`varunbhatt2193/selenium2playwright`](https://github.com/varunbhatt2193/selenium2playwright).

The files in this checkout prepare scanning. They do not activate repository
settings until you publish them and finish the steps below. The connected GitHub
app used to inspect the repository has read-only access.

## 1. Publish the configuration

Commit and push these files through a pull request, then merge them into `main`:

- [CodeQL workflow](../.github/workflows/codeql.yml)
- [Dependabot configuration](../.github/dependabot.yml)

CodeQL scans Python and JavaScript/TypeScript on pull requests targeting `main`,
pushes to `main`, and every Monday at 08:23 UTC. It can also be started manually
from Actions after the workflow reaches the default branch. It uses the
`security-extended` query suite and standard GitHub-hosted Ubuntu runners.
Actions are pinned to verified upstream commit IDs and monitored by Dependabot.
The workflow needs no application credentials and does not invoke the LLM.

This uses CodeQL **advanced setup**. If CodeQL default setup is already enabled,
switch it off using Settings → Advanced Security → CodeQL analysis → Switch to
advanced before using this workflow. Do not create a second generated workflow.
Ensure GitHub Actions is enabled and allows the official `actions/checkout` and
`github/codeql-action` actions. [GitHub setup instructions](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configuring-advanced-setup-for-code-scanning).

Dependabot proposes weekly version updates for Python's root `uv.lock`, npm in
`sandbox/` and `samples/`, and GitHub Actions. Minor/patch application updates are
grouped; major updates remain separate. Nothing auto-merges. Use the native `uv`
ecosystem, not `pip`, for this lockfile. [uv integration](https://docs.astral.sh/uv/guides/integration/dependabot/).

## 2. Enable alerts and secret protection

As repository owner, open
[Settings → Advanced Security](https://github.com/varunbhatt2193/selenium2playwright/settings/security_analysis).
GitHub may group these controls under “Security and quality.”

Confirm these settings are enabled:

| Setting | Purpose |
| --- | --- |
| Dependency graph | Inventory supported dependencies; automatically enabled for public repositories |
| Dependabot alerts | Notify about known vulnerable dependencies |
| Dependabot security updates | Open available fix PRs for vulnerability alerts |
| Secret Protection / secret scanning | Detect supported credentials in the repository and history |
| Push protection | Block pushes containing supported secrets; authorized bypasses remain possible |

The Dependabot YAML controls scheduled version updates. It is not a replacement
for enabling alerts and security updates. GitHub evaluates newly published
advisories independently of the weekly version-update schedule. Check Insights →
Dependency graph to verify Python and npm packages are inventoried, and inspect
Dependabot update-job errors if a manifest fails to resolve. [Dependabot alerts](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-dependabot-alerts),
[push protection](https://docs.github.com/en/code-security/how-tos/secure-your-secrets/prevent-future-leaks/enable-push-protection).

Push protection recognizes supported secret patterns; it is not a guarantee that
every arbitrary password is detected. Continue keeping `.env` and
`deploy/fly/.pgpassword` out of Git. Do not test protection using a real key.

## 3. Make security findings block merges

After the first CodeQL analysis succeeds, open
[Settings → Rules → Rulesets](https://github.com/varunbhatt2193/selenium2playwright/settings/rules).
Create a branch ruleset for `main` with enforcement **Active**:

- Require a pull request before merging. A solo maintainer need not require
  another person's approval.
- Require code scanning results: tool **CodeQL**, security alerts **High or
  higher**, other alerts **Errors**.
- Require successful status checks for `Analyze (python)` and
  `Analyze (javascript-typescript)`, selecting their actual names once GitHub
  has observed a run.
- Block force pushes and restrict deletion of `main`.

The result requirement checks findings; successful analysis jobs alone mean the
scanner ran, not that it found no vulnerabilities. Review medium findings too.
The branch rule governs pull requests; it does not automatically block an
independent `fly deploy` command. [Merge protection and thresholds](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/manage-your-configuration/set-merge-protection).

## 4. Verify it is working

1. Under [Actions](https://github.com/varunbhatt2193/selenium2playwright/actions),
   confirm the CodeQL workflow completed for both languages.
2. Under [Security and quality](https://github.com/varunbhatt2193/selenium2playwright/security),
   inspect Code scanning, Dependabot, and Secret scanning. A successful workflow
   may still produce findings that need fixes.
3. Confirm Dependabot recognizes the four configured update entries and the
   dependency graph lists the expected manifests.
4. Open a subsequent pull request and confirm both analysis jobs run and the
   `main` ruleset requires the configured scan results.
5. Revisit Advanced Security and confirm secret scanning and push protection
   explicitly show as enabled.

## Cost and scope

Code scanning and secret protection are available free for public repositories.
Standard GitHub-hosted runner time is free for public repositories. This workflow
does not request larger runners or extra artifact uploads. Additional paid
products are not required. [Feature availability](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository),
[Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

These checks cover repository code, supported dependencies and secrets. They do
not scan the running Fly API, PostgreSQL/Redis exposure, or all OS packages in the
deployed images. Dependabot will not update image references embedded in
`langgraph.json` or Fly TOML files via this configuration; the generated Dockerfile
is intentionally untracked. Keep the separate container scans and authenticated
staging tests from the launch plan.
