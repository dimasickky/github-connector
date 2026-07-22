# Changelog

## v0.7.0 — 2026-07-23 — Second pivot: GitHub App -> classic OAuth App

### Changed (breaking, internal + user-facing)

- **The whole "installation" concept is gone.** Replaced the GitHub App
  (installation-scoped, repository-picker) model from v0.6.0 with a classic
  OAuth App: one "Authorize this app?" screen, no repository selection —
  the token can reach anything the connected account can reach.
- `storage.py` rewritten: `gh_installations` -> `gh_connections` (just
  `{account_login}`, no `installation_id`, no cached `repositories[]`).
  New `gh_repo_webhooks` + `gh_repo_webhook_index` collections replace the
  old installation-wide notification wiring (see below).
- `user_auth.py` rewritten: classic OAuth code-exchange only. No refresh
  cycle — classic OAuth App tokens don't expire by default, so there is
  nothing to refresh (v0.6.0's ~8h access/~6mo refresh token rotation is
  gone, it doesn't apply to this token type).
- `auth.py` rewritten: `start_github_install`/`install_callback` ->
  `connect_github`/`oauth_callback`. Scopes requested: `repo`, `read:org`,
  `workflow`, `admin:repo_hook` (new — needed for per-repo webhook
  registration, see below).
- `handlers_repos.py`: `list_repositories` now reads `GET /user/repos`
  directly (no more `/user/installations` + per-installation repo list
  round trip) — simpler, and no longer silently limited to one
  installation's repository_selection.
- `panels.py`: sidebar now fetches the repo list live from `GET /user/repos`
  on every render instead of reading a cached `repositories[]` field off
  the old installation record — there's nothing left to cache that
  correctly, since a classic OAuth token's reach isn't fixed at connect time.
- **Live notifications are now opt-in, per repository**, not automatic for
  every repo an installation covered. A classic OAuth App has no
  App-level webhook the way a GitHub App does — each repo's hook must be
  registered individually (`POST /repos/{owner}/{repo}/hooks`, needs
  `admin:repo_hook`). New tools: `enable_repo_notifications(repo)`,
  `disable_repo_notifications(repo)`. `disconnect_github` now sweeps every
  repo-level webhook it ever registered before deleting the token, so
  disconnecting never leaves an orphaned hook pointing at a dead
  integration.
- `handlers_webhook_events.py`: event identity resolution switched from
  `installation.id` (a GitHub-App-only payload field, gone entirely from
  a classic OAuth App's webhook deliveries) to `repository.full_name`,
  resolved against the new per-repo reverse index.
- Secrets: `github_app_slug` removed (no more App identity to display a
  slug for). `github_client_id`/`github_client_secret` re-scoped to mean
  the classic OAuth App's credentials, not the GitHub App's. Unchanged:
  `github_webhook_secret`, `github_encryption_key`.

### Why this release

Creating a new repo and having it usable immediately, with zero manual
"add this repo to the installation" step, was explicitly weighed against
keeping the GitHub App's tighter per-repository scoping — and the friction
of the installation picker was judged not worth it for this extension's
actual usage pattern. This was a deliberate, discussed trade-off (broader
account access, zero picker friction), not an oversight; see
`extensions/github-connector.md` §12.2 for the full reasoning.

### Tests
67/67 tests passing (all install-flow/repo/webhook tests rewritten for the
new connection model; +6 net new for per-repo notification toggles).
`imperal validate .`: 0 errors, 0 warnings, 1 informational note (unchanged,
no `@ext.on_install` hook).

### Deploy blocker
Not yet deployed. Requires registering a **new, separate classic OAuth App**
on github.com (Settings → Developer settings → **OAuth Apps**, NOT GitHub
Apps — a fresh registration, not a settings toggle on the old GitHub App),
with Authorization callback URL = this extension's
`ctx.webhook_url("oauth_callback")` value. Then save its Client ID/Client
Secret as `github_client_id`/`github_client_secret`. The old GitHub App
registration can be left alone or removed later — not urgent, not required
for this to work.

## v0.6.0 — 2026-07-23 — Full migration to user-to-server OAuth

### Changed (breaking, internal)

- **Every API call now authenticates as the real GitHub user, not the App's own bot identity.** Installation-token minting (App-level JWT signed with the App's private key) is removed from the codebase entirely — `get_installation_token` is gone, replaced by `get_user_token` which resolves a stored, encrypted, auto-refreshing user-to-server OAuth token.
- `install_callback` now reads a `code` query param (present once "Request user authorization (OAuth) during installation" is enabled in the GitHub App's settings) and exchanges it for a user access token + refresh token pair (`user_auth.exchange_code_for_token`), persisted encrypted (Fernet, new `github_encryption_key` secret) in a new `gh_user_tokens` store collection. Tokens auto-refresh ~5 minutes before their ~8h expiry; the refresh token itself rotates on every use (~6mo sliding window).
- Secrets: `github_app_id` / `github_app_private_key` removed. Added `github_client_id`, `github_client_secret`, `github_encryption_key`. `github_app_slug` / `github_webhook_secret` unchanged.
- `list_repositories` now reads from `/user/installations` + `/user/installations/{id}/repositories` (user-token-compatible) instead of `/installation/repositories` (installation-token-only).

### Added

- **`create_repository`** — create a new repo in your personal account or an org you belong to. This is the concrete capability an installation token could never provide (`POST /user/repos` hard-rejects installation tokens regardless of permissions) — the actual motivation for this migration.

### Why this release

`create_repository` was requested and turned out to be structurally impossible on the old installation-token-only model — a GitHub platform restriction, not a bug. Rather than bolt on a narrow OAuth carve-out for just that one tool, the whole extension was moved to user-to-server auth: same GitHub App, same per-repository scoping (GitHub docs confirm a user access token is the *intersection* of what the user picked at install time and what they personally can access — never broader), but no more artificial ceiling on which GitHub REST endpoints are reachable going forward.

### Tests
65/65 tests passing (62 existing, migrated off installation-token mocks onto a seeded user-token fixture, + 3 new for `create_repository`). `imperal validate .`: 0 errors, 0 warnings, 1 informational note (unchanged, no `@ext.on_install` hook).

### Deploy blocker
Not yet deployed. Requires from the GitHub App owner: (1) the App's OAuth **Client Secret** (Developer settings → GitHub Apps → your app → General → Client secrets), saved as the `github_client_secret` secret; (2) **"Request user authorization (OAuth) during installation"** enabled in the same settings page — without it GitHub's install redirect won't carry the `code` param `install_callback` now requires.

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
