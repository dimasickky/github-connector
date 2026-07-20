# GitHub Connector

[![Imperal SDK](https://img.shields.io/badge/Imperal%20SDK-5.9.9-6c5ce7?logo=python&logoColor=white)](https://imperal.io)
[![License: LGPL v2.1](https://img.shields.io/badge/License-LGPL--2.1-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)

> Chat-driven GitHub for Imperal Cloud — browse repos, read code and commit history, open and merge pull requests, triage issues, and trigger the CI/CD you already have, without leaving your workspace.

**GitHub Connector** connects your GitHub repositories to [Imperal Cloud](https://imperal.io), the ICNLI AI Cloud OS, using a **GitHub App** installation — never a personal access token, never your own OAuth login. Every action runs with a short-lived, narrowly-scoped installation token minted fresh per call and never stored.

## What it can do

| Area | Capabilities |
| --- | --- |
| 🔌 **Connect** | One-click GitHub App install flow; pick which repositories the App can see, right from GitHub's own install screen |
| 📚 **Browse** | List repositories, read file contents at any path/ref, walk commit history, see top contributors |
| 🗂️ **Center panel** | File-tree + code viewer for any connected repository, right inside the panel |
| ✍️ **Write** | Create branches, commit file changes, open pull requests, open issues, comment on issues/PRs |
| 🔀 **Merge & close** | Merge a pull request (with a diff preview shown before you confirm), close a pull request or issue — each requires an explicit second confirmation call, never a first-call surprise |
| 🗑️ **Delete branch** | Same explicit two-step confirmation as merge/close |
| 🚀 **Deploy** | Trigger an existing GitHub Actions `workflow_dispatch` — runs the CI/CD you already configured, doesn't invent its own |
| 🔔 **Live notifications** | New issues, PRs opened, review requests, PR merges, failed CI runs, and pushes to the default branch push a notification the moment GitHub reports them — no need to ask |

## Why a GitHub App, not a personal token

A classic personal access token (or a broad OAuth login) hands over "everything on your account, forever" — one token, every repo, every scope, no expiry you control from here. A GitHub App is the opposite by design:

- **Installation-scoped** — you choose exactly which repositories the App can see when you install it, and can change that anytime from GitHub's own settings.
- **Short-lived tokens** — every call mints a fresh installation access token (GitHub-side ~1 hour expiry) signed by the App's own private key; nothing long-lived is stored by this extension.
- **Revocable in one place** — uninstalling the App on github.com instantly cuts off access, no separate token to hunt down and rotate.

## Quick start

### 1. Install the extension

Install **GitHub Connector** from Imperal Cloud when it is available in your workspace.

### 2. Connect your GitHub account

Ask Webbee to connect GitHub, or use the sidebar panel's **Connect GitHub** button. You'll be redirected to GitHub's own install screen to pick an organization/account and choose "all repositories" or specific ones.

### 3. Start browsing and working with your repos

Once connected, the sidebar lists every repository the installation covers. Click one to open the file/code browser, or just ask Webbee things like:

- "list my repos" / "show me the file tree for `owner/repo`"
- "what changed in the last 10 commits on `owner/repo`?"
- "open a PR from `feature/x` into `main`"
- "trigger the deploy workflow on `main`"

### 4. (Optional) Turn on live notifications

Live notifications need the GitHub App itself configured with a Webhook URL and secret (Developer Portal secret `github_webhook_secret`, not part of this repository):

1. In your GitHub App's settings, set **Webhook URL** to this extension's `events` endpoint and set a **Webhook secret** — save that same secret as `github_webhook_secret` on the extension.
2. Subscribe to at least: `Issues`, `Issue comments`, `Pull requests`, `Workflow runs`, `Pushes`.
3. That's it — once an installation is connected, Webbee notifies you the moment a new issue/PR lands, your review is requested, a PR merges, CI fails, or someone pushes to the default branch.

No webhook secret configured? Everything else keeps working exactly as before — notifications are simply skipped.

## Confirmation on destructive actions

Merging a pull request, closing a pull request/issue, and deleting a branch all use an explicit **two-step confirm** built into the tool itself: the first call always previews what would happen and changes nothing; only a second call with `confirm=true` actually does it. This doesn't depend on any account-level confirmation toggle — it's built in regardless of your settings.

## Security model

- **No GitHub tokens are ever persisted.** Installation access tokens are minted fresh per call (signed JWT → GitHub's token endpoint) and used immediately — never written to storage, never logged.
- The GitHub App's own identity (App ID, private key, webhook secret) is a Developer Portal secret, never committed to this repository.
- The install-flow's one-shot state token expires quickly and is consumed exactly once, closing the window for a replayed or guessed callback.
- The live event webhook (issues/PRs/CI notifications) verifies GitHub's HMAC-SHA256 signature (`X-Hub-Signature-256`) on every delivery before trusting it, using a constant-time comparison; anything that doesn't match is rejected and logged, never processed.
- v1 acts as the GitHub App's own bot identity (installation token), not a specific human GitHub user — actions show up in GitHub's audit log attributed to the App.
- Force-push, repository deletion, and org-level admin actions are explicitly out of scope — not implemented, not planned for v1.

## Development

### Requirements

- Python 3.11+
- [Imperal SDK](https://github.com/imperalcloud/imperal-sdk) 5.9.9

### Install and test

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e /path/to/imperal-sdk
pip install -r requirements.txt pytest pytest-asyncio

pytest tests/ -q
imperal validate .
imperal build .
```

Expected current result:

- test suite: all tests pass;
- validator: 0 errors, 0 warnings.

## Roadmap

- [x] GitHub App install flow (state token + webhook callback)
- [x] Sidebar panel: connection status + repository list
- [x] Read-only repo browsing: repositories, file contents, commits, contributors
- [x] Center panel: file tree + code viewer
- [x] Read-only pull requests, issues, workflow runs
- [x] Write: branches, file commits, pull requests, issues, comments
- [x] Destructive (own two-step confirm): merge, close, delete branch
- [x] Trigger existing GitHub Actions workflows (`workflow_dispatch`)
- [x] Live notifications for issues/PRs/CI/pushes via a signed GitHub webhook
- [ ] Sidebar summary counts (open PRs/issues at a glance)
- [ ] Per-user audit attribution (currently: App bot identity only)

## Non-goals (v1)

- GitLab/Bitbucket — GitHub only.
- Real git protocol (clone/push over SSH) — not needed, the REST API covers the full v1 feature set.
- Force-push, repository deletion, org-level admin actions.

## License

Licensed under the [GNU Lesser General Public License v2.1](LICENSE).
