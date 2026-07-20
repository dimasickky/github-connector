# Changelog

## v0.4.0 — 2026-07-21 — Pull request reviews, single-item lookup, labels/assignees/draft

### Added

- **`review_pull_request`** — the main gap closed this release. Submits a
  real GitHub PR review (`POST /pulls/{number}/reviews`) with a verdict —
  `APPROVE`, `REQUEST_CHANGES`, or `COMMENT` — distinct from
  `comment_on_issue_or_pr`'s plain issue-style comment: this shows up as an
  actual review with GitHub's own green/red badge on the PR. Validates that
  `REQUEST_CHANGES`/`COMMENT` carry a non-empty body before calling GitHub,
  so a caller gets an actionable message instead of GitHub's own 422.
- `get_pull_request` / `get_issue` — fetch one PR or issue in full by
  number (body, labels, assignees, `mergeable_state` for PRs) instead of
  paging through `list_pull_requests`/`list_issues` hoping the right one
  lands on the first page.
- `create_pull_request` / `create_issue` now accept `labels`, `assignees`,
  and (for PRs) `draft` — applied via a follow-up PATCH to the Issues API
  (GitHub's `POST /pulls` itself doesn't take labels/assignees). A failed
  label/assignee PATCH is logged as a warning, not a hard failure — the
  PR/issue itself is already created by that point.
- `merge_pull_request`'s preview now also surfaces `mergeable_state` and
  warns explicitly when it's `dirty`/`conflicting` (merge conflicts),
  `blocked` (a required check/review isn't satisfied), or `unstable`
  (non-required checks failing) — so `confirm=true` is never a blind leap
  on a PR that GitHub itself expects to reject.

### Why this release

Code review against the actual handlers (not the spec) turned up one real
semantic gap for an extension literally named "manage pull requests":
there was no way to approve, request changes, or leave a genuine PR review
— only a diff preview, merge, and close. Everything else here was cheap to
add once the same files were open: single-item lookup and labels/
assignees/draft round out `create_*`/`list_*` without materially growing
the surface area. Deliberately left for later (bigger lift, lower urgency):
workflow run rerun/cancel + job logs, atomic multi-file commits, `delete_file`.

### Tests
62/62 tests passing (55 existing + 7 new).

## v0.3.0 — 2026-07-20 — Sidebar-refresh fix, code search, releases, markdown README

### Fixed

- **Sidebar didn't auto-refresh after reconnecting GitHub in a separate tab.**
  Root cause: `install_callback` runs under the webhook's own pseudo-identity
  (`ctx.user.imperal_id == "__webhook__"`), and `ctx.extensions.emit(...)`
  publishes the panel-refresh event under whatever user_id the calling
  context carries — so the event went out as `"__webhook__"`, a session the
  real user's own panel is never subscribed to. `refresh="on_event:..."`
  therefore never fired for the actual user; only a manual panel reload
  picked up the freshly-connected installation.
  Fixed by rescoping the emit through an `ExtensionsClient` built for the
  real, resolved `imperal_id` (`storage._extensions_for`, the same rescoping
  trick already used for the store) instead of emitting through the
  webhook's own client. No SDK change needed. Covered by a new regression
  test (`test_install_callback_emits_event_scoped_to_real_user_not_webhook`).

### Added

- `search_code` — search for code inside a connected repository using
  GitHub's own code-search syntax (e.g. `TODO language:python`), scoped
  automatically to the repo you pass.
- `list_releases` — list a repository's releases/tags (name, tag, draft/
  prerelease flags, published date, release notes).
- README.md (and any `.md` file) now renders as formatted markdown in the
  center panel's file browser instead of a raw syntax-highlighted code
  block.

### Tests
55/55 tests passing (50 existing + 5 new).

## v0.2.1 — 2026-07-20 — Diff preview before merge

### Added

- `merge_pull_request`'s preview call (confirm=false) now fetches and shows
  the pull request's unified diff (`Accept: application/vnd.github.v3.diff`)
  as a syntax-highlighted code block before you're asked to confirm — so
  "irreversible" merges are no longer a blind confirmation. Truncated at
  6000 chars with a note to review the full diff on GitHub for larger PRs.
  Falls back gracefully (confirmation flow unaffected) if GitHub doesn't
  return a diff for any reason.

### Tests
49/49 tests passing (48 existing + 1 new for the diff preview).

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
