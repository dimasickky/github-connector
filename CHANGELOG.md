# Changelog

## v0.2.0 — 2026-07-20 — Disconnect/switch account, live GitHub notifications

### Added

**Account management**
- `disconnect_github` — removes the stored installation record (own explicit
  two-step confirm flow, same pattern as merge/close/delete branch). GitHub's
  own App installation is untouched — this only makes Imperal forget it.
- Sidebar footer: "Switch account" (reopens GitHub's install/config page to
  connect a different account or org) and "Disconnect" buttons.

**Live notifications (real GitHub webhook events)**
- New `@ext.webhook("events")` endpoint — signed POST deliveries from GitHub
  (not to be confused with `install_callback`, the unsigned GET redirect from
  the install-page flow). Verifies `X-Hub-Signature-256` HMAC-SHA256 with a
  constant-time comparison before trusting anything.
- Notifies (`ctx.notify`) the right real user — resolved via a new
  installation_id -> imperal_id reverse index written on every successful
  install/reinstall — for: new issue opened, new PR opened, your review
  requested, PR merged, CI (`workflow_run`) failed, and pushes to the default
  branch.
- No webhook secret configured? Everything else keeps working unchanged;
  notifications are simply skipped (feature is fully optional, opt-in on the
  GitHub App's own settings page).

### Tests
48/48 tests passing (40 existing + 8 new for the webhook events handler).
`imperal validate .`: 0 errors, 0 warnings, 1 informational note (unchanged).

## v0.1.0 — 2026-07-19 — Initial build: install flow through CI/CD trigger (P0–P6)

### Added

**Install flow (P1)**
- `start_github_install` — mints a one-shot state token and redirects the
  user to GitHub's own App install screen.
- `install_callback` webhook — verifies GitHub's HMAC-SHA256 signature,
  resolves the one-shot state back to the real Imperal user, and stores the
  resulting installation record (`installation_id`, account, repository
  list). No GitHub token is ever stored — only the installation metadata.
- Sidebar panel (`panels.py`): connection status + repository list, with a
  "Connect GitHub" button when not yet connected.

**Read-only repo browsing (P2)**
- `list_repositories`, `get_file_contents`, `list_recent_commits`,
  `list_contributors` — all installation-token-scoped, all read-only.
- Center panel (`panels_browser.py`): file tree + code viewer for any
  connected repository at any path/ref.

**Read-only PR / issues / actions (P3)**
- `list_pull_requests`, `list_issues` (pull requests excluded — GitHub's
  issues endpoint returns both under the hood), `get_workflow_runs`.

**Write tools (P4)**
- `create_branch`, `create_or_update_file` (a single-file commit),
  `create_pull_request`, `create_issue`, `comment_on_issue_or_pr`. None of
  these touch or remove anything that already exists.

**Destructive tools, own confirm flow (P5)**
- `merge_pull_request`, `close_pull_request_or_issue`, `delete_branch` —
  each implements its own explicit two-step `confirm: bool` flow (first call
  previews and changes nothing; a second call with `confirm=true` executes).
  This does not depend on the platform's account-level confirmation gate,
  which defaults off and cannot be forced on by an extension — same pattern
  already proven in `wp-site-connector`'s `manage_plugin`.

**CI/CD trigger (P6)**
- `trigger_workflow_dispatch` — runs an existing GitHub Actions workflow
  that already declares a `workflow_dispatch` trigger. Doesn't write or
  modify any workflow, just runs what the user already configured — the
  "deploy from chat" path.

### Auth model
GitHub App (not OAuth App, not a personal access token): installation-scoped
access, short-lived tokens minted fresh per call via a JWT signed with the
App's own private key, nothing long-lived stored. See README.md "Why a
GitHub App" for the full reasoning.

### Tests
31/31 tests passing across install flow, P2–P6 tool handlers (including
both preview and confirmed states of every destructive tool). `imperal
validate .`: 0 errors, 0 warnings, 1 informational note (no `@ext.on_install`
lifecycle hook — not required).

### Not done yet (open, tracked in extensions/github-connector.md)
- Sidebar summary counts (open PR/issue totals at a glance) — currently
  repo list only.
- Per-user audit attribution — v1 acts as the App's own bot identity;
  actions show up in GitHub's audit log attributed to the App, not the
  specific Imperal user who triggered them.
- P0 (manual GitHub App registration on github.com) is a one-time manual
  step outside this codebase — required before any of the above can be
  exercised end-to-end against a real GitHub account.
