# GitHub Connector

[![Imperal SDK](https://img.shields.io/badge/Imperal%20SDK-5.9.11-6c5ce7?logo=python&logoColor=white)](https://imperal.io)
[![License: LGPL v2.1](https://img.shields.io/badge/License-LGPL--2.1-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)

> Chat-driven GitHub for Imperal Cloud — browse repos, read code and commit history, open and merge pull requests, triage issues, and trigger the CI/CD you already have, without leaving your workspace.

**GitHub Connector** connects your GitHub account to [Imperal Cloud](https://imperal.io), the ICNLI AI Cloud OS, via a standard **OAuth App** authorization — the same one-click "Authorize this app?" flow you already know from every other GitHub integration. Once you approve it, every action runs as *you*, with your own real GitHub identity and permissions — nothing is stored on GitHub's side beyond the authorization itself.

## What it can do

| Area | Capabilities |
| --- | --- |
| 🔌 **Connect** | One-click GitHub OAuth authorize flow — no repository picker, no app installation step |
| 📚 **Browse** | List repositories, read file contents at any path/ref, walk commit history, see top contributors, search code across a repo, list releases |
| 🗂️ **Center panel** | File-tree + code viewer for any of your repositories, right inside the panel — README and other `.md` files render as formatted markdown, not raw text |
| ✍️ **Write** | Create branches, commit file changes, open pull requests (with labels/assignees/draft), open issues (with labels/assignees), comment on issues/PRs, create new repositories (personal or org) |
| ✅ **Review** | Approve, request changes, or comment on a pull request as a real GitHub review (`/pulls/{number}/reviews`) — a genuine review verdict, not a plain comment |
| 🔎 **Single-item lookup** | Get one pull request or issue by number in full (body, labels, assignees, mergeable_state) — no need to page through a list |
| 🔀 **Merge & close** | Merge a pull request (with a diff preview *and* a mergeable_state warning — conflicts/blocked/unstable — shown before you confirm), close a pull request or issue — each requires an explicit second confirmation call, never a first-call surprise |
| 🗑️ **Delete branch** | Same explicit two-step confirmation as merge/close |
| 🚀 **Deploy** | Trigger an existing GitHub Actions `workflow_dispatch` — runs the CI/CD you already configured, doesn't invent its own |
| 🔔 **Live notifications** | Opt in per repository: new issues, PRs opened, review requests, PR merges, failed CI runs, and pushes to the default branch push a notification the moment GitHub reports them — no need to ask |

## Why a plain OAuth App

A GitHub App with a per-repository installation picker is the more surgical option, but it comes with real friction: every new repository you create has to be manually granted to the installation before this extension can see it. A classic OAuth App trades that scoping for zero friction — authorize once, every repository you can already reach on GitHub (owned, collaborator, or org member) works immediately, new ones included, with no extra step.

- **One real identity** — every action runs as you, not a bot; it shows up in GitHub's own audit log as *you*.
- **No stale installation state** — nothing to "re-add" a repo to; access is always resolved live by GitHub, on every call.
- **Revocable in one place** — remove the authorization from github.com/settings/applications and access stops instantly, no separate token to hunt down and rotate.
- **Trade-off, stated plainly** — the token can reach anything your account can reach with the requested scopes (`repo`, `read:org`, `workflow`, `admin:repo_hook`), not just a hand-picked subset. If you'd rather scope this down to specific repositories, that's a deliberate, discussed trade-off — not an oversight.

## Quick start

### 1. Install the extension

Install **GitHub Connector** from Imperal Cloud when it is available in your workspace.

### 2. Connect your GitHub account

Ask Webbee to connect GitHub, or use the sidebar panel's **Connect GitHub** button. You'll be redirected to GitHub's own "Authorize this app?" screen — approve it and you're back.

### 3. Start browsing and working with your repos

Once connected, the sidebar lists your repositories, fetched live from GitHub. Click one to open the file/code browser, or just ask Webbee things like:

- "list my repos" / "show me the file tree for `owner/repo`"
- "what changed in the last 10 commits on `owner/repo`?"
- "open a PR from `feature/x` into `main`"
- "approve PR #42" / "request changes on #42, tell them to fix the tests"
- "show me PR #42" / "show me issue #7"
- "trigger the deploy workflow on `main`"
- "create a new repo called `my-new-project`"

### 4. (Optional) Turn on live notifications for a repo

Notifications are opt-in, per repository — ask Webbee to "turn on notifications for `owner/repo`", or call it explicitly. This registers a real webhook on that one repo; turning it off removes the webhook again. Disconnecting your account sweeps every repo you ever enabled notifications on, so nothing is left behind on GitHub's side.

## Confirmation on destructive actions

Merging a pull request, closing a pull request/issue, deleting a branch, and disconnecting your account all use an explicit **two-step confirm** built into the tool itself: the first call always previews what would happen and changes nothing; only a second call with `confirm=true` actually does it. This doesn't depend on any account-level confirmation toggle — it's built in regardless of your settings.

## Security model

- **Tokens are encrypted at rest.** Your OAuth access token is stored encrypted (Fernet) under this extension's own encryption key — never logged, never shared with other extensions.
- The OAuth App's own identity (client ID, client secret, webhook secret) is a Developer Portal secret, never committed to this repository.
- The connect flow's one-shot state token expires quickly and is consumed exactly once, closing the window for a replayed or guessed callback.
- The live event webhook (issues/PRs/CI notifications) verifies GitHub's HMAC-SHA256 signature (`X-Hub-Signature-256`) on every delivery before trusting it, using a constant-time comparison; anything that doesn't match is rejected and logged, never processed.
- Every action runs as your real GitHub identity — actions show up in GitHub's audit log attributed to you, not a bot.
- Force-push, repository deletion, and org-level admin actions are explicitly out of scope — not implemented, not planned for v1.

## Development

### Requirements

- Python 3.11+
- [Imperal SDK](https://github.com/imperalcloud/imperal-sdk) 5.9.11

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

- [x] Classic OAuth App connect flow (state token + callback)
- [x] Sidebar panel: connection status + live repository list
- [x] Read-only repo browsing: repositories, file contents, commits, contributors
- [x] Center panel: file tree + code viewer (markdown-aware)
- [x] Read-only pull requests, issues, workflow runs
- [x] Write: branches, file commits, pull requests, issues, comments, repository creation
- [x] Destructive (own two-step confirm): merge, close, delete branch, disconnect
- [x] Trigger existing GitHub Actions workflows (`workflow_dispatch`)
- [x] Opt-in, per-repository live notifications for issues/PRs/CI/pushes via a signed webhook
- [x] Code search within a connected repository
- [x] List releases/tags
- [x] Pull request reviews: approve / request changes / comment (`/pulls/{number}/reviews`)
- [x] Single-item lookup: `get_pull_request`, `get_issue`
- [x] labels/assignees/draft on `create_pull_request`/`create_issue`
- [x] `mergeable_state` surfaced (with a conflict/blocked/unstable warning) in the merge preview
- [ ] Sidebar summary counts (open PRs/issues at a glance)
- [ ] Workflow run rerun/cancel + job logs
- [ ] Atomic multi-file commits, `delete_file`

## Non-goals (v1)

- GitLab/Bitbucket — GitHub only.
- Real git protocol (clone/push over SSH) — not needed, the REST API covers the full v1 feature set.
- Force-push, repository deletion, org-level admin actions.

## License

Licensed under the [GNU Lesser General Public License v2.1](LICENSE).
