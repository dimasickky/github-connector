"""github-connector · per-repo webhook notifications (classic OAuth App model).

Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub App
-> classic OAuth App): a classic OAuth App has NO centralized, App-level
webhook the way a GitHub App does — GitHub only lets you subscribe to repo
events by registering a webhook on that specific repo
(`POST /repos/{owner}/{repo}/hooks`), one at a time, with the `admin:repo_hook`
scope. There's also no more "every connected repo notifies automatically"
default (that relied on the App's installation-wide webhook + repository
picker, both gone now) — so this module is now opt-in, per repo:

- `enable_repo_notifications(repo)` registers a real GitHub webhook on that
  one repo (events: issues, issue_comment, pull_request, workflow_run, push),
  pointed at our shared `ctx.webhook_url("events")` endpoint with a shared
  HMAC secret (`github_webhook_secret`), and records {repo_full_name,
  github_hook_id} so it can be torn down later (storage.save_repo_webhook —
  also writes the reverse index `repo_full_name -> imperal_id` the
  unauthenticated receiver below needs).
- `disable_repo_notifications(repo)` deletes that GitHub-side hook and the
  local record/index entry.
- `disconnect_github` (auth.py) sweeps every repo this user ever enabled
  notifications on before deleting their token, so no orphaned GitHub-side
  hooks are left pointing at a now-unauthorized integration.

The actual delivery receiver (`webhook_events`, `@ext.webhook("events")`)
is otherwise unchanged in spirit from the GitHub App version: GitHub POSTs
signed event deliveries, we verify `X-Hub-Signature-256` (constant-time
compare), decide if the event is notification-worthy, and call
`ctx.notify(...)` for the right real user(s). What changed is IDENTITY
resolution: there is no more `installation.id` in any payload (that field
only ever existed for GitHub Apps) — the only usable identity is
`repository.full_name`, resolved via `storage.resolve_imperal_ids_for_repo`
against the reverse index `enable_repo_notifications` wrote. A repo could in
principle be watched by more than one connected Imperal user (e.g. two
teammates each connected their own account to the same shared org repo), so
this notifies ALL of them, not just one.
"""
import hashlib
import hmac
import json

from imperal_sdk import ActionResult
from pydantic import BaseModel, Field

from app import ext, chat
from error_codes import GH_NOT_CONNECTED, GH_WEBHOOK_REGISTRATION_FAILED
from models import RepoNotificationResult
import github_client
import storage

_WEBHOOK_EVENTS = ["issues", "issue_comment", "pull_request", "workflow_run", "push"]


class _RepoParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo' — from list_repositories")


async def _get_token(ctx):
    token, err = await github_client.get_user_token(ctx)
    if err:
        return None, ActionResult.error(err, retryable=False, code=GH_NOT_CONNECTED)
    return token, None


@chat.function(
    "enable_repo_notifications",
    description=(
        "Turn on live GitHub notifications for one repo — get notified in "
        "Imperal (bell/telegram/email per your notification settings) when "
        "an issue/PR opens, your review is requested, a PR merges, CI fails, "
        "or someone pushes to the default branch. Registers a real webhook "
        "on that repo (requires admin access to it on GitHub's side)."
    ),
    action_type="write",
    data_model=RepoNotificationResult,
    effects=["github.enable_notifications"],
    event="github-connector-extension.enable_repo_notifications",
)
async def enable_repo_notifications(ctx, params: _RepoParams) -> ActionResult:
    """POST /repos/{owner}/{repo}/hooks — registers a webhook pointed at our
    shared `events` receiver, then records it so webhook_events can resolve
    this repo back to this real user, and so disable_repo_notifications /
    disconnect_github can find it again to delete it."""
    token, err = await _get_token(ctx)
    if err:
        return err

    existing = await storage.get_repo_webhook(ctx, params.repo)
    if existing:
        return ActionResult.success(
            data=RepoNotificationResult(id=params.repo, title=params.repo, repo=params.repo, enabled=True),
            summary=f"Notifications are already enabled for {params.repo}.",
        )

    secret = await ctx.secrets.get("github_webhook_secret")
    if not secret:
        return ActionResult.error(
            "Webhook secret is not configured yet — the developer needs to finish setup first.",
            code=GH_WEBHOOK_REGISTRATION_FAILED,
        )

    body = {
        "name": "web",
        "active": True,
        "events": _WEBHOOK_EVENTS,
        "config": {
            "url": ctx.webhook_url("events"),
            "content_type": "json",
            "secret": secret,
        },
    }
    resp = await github_client.gh_post(ctx, token, f"/repos/{params.repo}/hooks", json_body=body)
    if resp.status_code >= 400:
        return ActionResult.error(
            github_client.gh_error_message(resp.status_code),
            retryable=resp.status_code >= 500, code=GH_WEBHOOK_REGISTRATION_FAILED,
        )

    hook_id = resp.json().get("id")
    await storage.save_repo_webhook(ctx, params.repo, hook_id)

    return ActionResult.success(
        data=RepoNotificationResult(id=params.repo, title=params.repo, repo=params.repo, enabled=True),
        summary=f"Notifications enabled for {params.repo}. You'll hear about new issues/PRs, review requests, merges, and CI failures.",
    )


@chat.function(
    "disable_repo_notifications",
    description="Turn off live GitHub notifications for one repo and remove its webhook.",
    action_type="write",
    data_model=RepoNotificationResult,
    effects=["github.disable_notifications"],
    event="github-connector-extension.disable_repo_notifications",
)
async def disable_repo_notifications(ctx, params: _RepoParams) -> ActionResult:
    """Deletes the GitHub-side hook (best-effort — if it's already gone on
    GitHub's side, e.g. manually removed, we still clean up our own record)
    then the local record + reverse index entry."""
    token, err = await _get_token(ctx)
    if err:
        return err

    record = await storage.get_repo_webhook(ctx, params.repo)
    if not record:
        return ActionResult.success(
            data=RepoNotificationResult(id=params.repo, title=params.repo, repo=params.repo, enabled=False),
            summary=f"Notifications were not enabled for {params.repo} — nothing to do.",
        )

    hook_id = record.get("github_hook_id")
    if hook_id:
        resp = await github_client.gh_delete(ctx, token, f"/repos/{params.repo}/hooks/{hook_id}")
        if resp.status_code not in (204, 404):
            await ctx.log(
                f"disable_repo_notifications: GitHub hook delete returned {resp.status_code} for {params.repo} — cleaning up local record anyway",
                level="warning",
            )

    await storage.delete_repo_webhook(ctx, params.repo)
    return ActionResult.success(
        data=RepoNotificationResult(id=params.repo, title=params.repo, repo=params.repo, enabled=False),
        summary=f"Notifications disabled for {params.repo}.",
    )


async def disable_all_repo_notifications(ctx) -> None:
    """Sweep every repo this user enabled notifications on, deleting the
    GitHub-side hook for each. Called from auth.py's disconnect_github
    BEFORE the token is deleted (needs a live token to call GitHub's delete-
    hook endpoint) — otherwise disconnecting would leave orphaned hooks on
    GitHub still pointing at a now-unauthorized integration."""
    token, err = await github_client.get_user_token(ctx)
    if err:
        return  # no usable token left — nothing more we can do from our side
    for record in await storage.list_repo_webhooks(ctx):
        repo_full_name = record.get("repo_full_name", "")
        hook_id = record.get("github_hook_id")
        if repo_full_name and hook_id:
            try:
                await github_client.gh_delete(ctx, token, f"/repos/{repo_full_name}/hooks/{hook_id}")
            except Exception as e:
                await ctx.log(f"disable_all_repo_notifications: hook delete failed for {repo_full_name}: {e}", level="warning")
        await storage.delete_repo_webhook(ctx, repo_full_name)


def _notify_for(webhook_ctx, imperal_id: str):
    """Build a NotifyClient scoped to a real user, from the webhook's own
    __webhook__-scoped ctx.notify — mirrors storage._store_for's approach
    (rebuild the client with a different user_id, reusing gateway/auth wiring)
    since ctx.as_user() requires system-context, which a webhook ctx is not.
    """
    if not hasattr(webhook_ctx.notify, "_gateway_url"):
        return webhook_ctx.notify  # test double (MockNotify) — already records by call, no per-user split needed
    from imperal_sdk.notify.client import NotifyClient
    return NotifyClient(
        gateway_url=webhook_ctx.notify._gateway_url,
        service_token=webhook_ctx.notify._auth_token,
        user_id=imperal_id,
        extension_id=getattr(webhook_ctx.notify, "_extension_id", "github-connector-extension"),
    )


def _verify_signature(secret: str, body: str, signature_header: str) -> bool:
    """GitHub's documented HMAC-SHA256 check: compute over the raw body with
    the shared webhook secret, compare against `X-Hub-Signature-256:
    sha256=<hex>` using a constant-time comparison."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    got = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, got)


def _repo_full_name(payload: dict) -> str:
    return (payload.get("repository") or {}).get("full_name", "")


def _describe_event(event_name: str, payload: dict) -> tuple[str, str] | None:
    """Return (message, priority) for a notification-worthy event, or None to
    skip silently (event type/action we don't surface in v1)."""
    repo = _repo_full_name(payload)
    action = payload.get("action", "")

    if event_name == "issues" and action == "opened":
        issue = payload.get("issue") or {}
        title = issue.get("title", "")
        return (f"New issue on {repo}: \"{title}\" (#{issue.get('number', '?')})", "normal")

    if event_name == "issue_comment" and action == "created" and "pull_request" not in (payload.get("issue") or {}):
        issue = payload.get("issue") or {}
        return (f"New comment on issue #{issue.get('number', '?')} in {repo}", "normal")

    if event_name == "pull_request":
        pr = payload.get("pull_request") or {}
        number = pr.get("number", "?")
        if action == "opened":
            return (f"New pull request on {repo}: \"{pr.get('title', '')}\" (#{number})", "normal")
        if action == "review_requested":
            requested = (payload.get("requested_reviewer") or {}).get("login", "")
            return (f"Your review was requested on {repo} #{number}" + (f" (by request for {requested})" if requested else ""), "high")
        if action == "closed" and pr.get("merged"):
            return (f"Pull request merged on {repo}: #{number}", "normal")

    if event_name == "workflow_run":
        run = payload.get("workflow_run") or {}
        conclusion = run.get("conclusion", "")
        if action == "completed" and conclusion in ("failure", "timed_out", "cancelled"):
            return (f"CI failed on {repo}: workflow \"{run.get('name', '')}\" {conclusion}", "high")

    if event_name == "push":
        ref = payload.get("ref", "")
        default_branch = (payload.get("repository") or {}).get("default_branch", "main")
        if ref == f"refs/heads/{default_branch}":
            pusher = (payload.get("pusher") or {}).get("name", "")
            n_commits = len(payload.get("commits") or [])
            return (f"{n_commits} new commit(s) pushed to {repo}@{default_branch} by {pusher}", "low")

    return None


@ext.webhook("events", method="POST", secret_header="X-Hub-Signature-256")
async def webhook_events(ctx, headers: dict, body: str, query_params: dict) -> dict:
    """Receives signed per-repo GitHub webhook deliveries and turns
    notification-worthy ones into ctx.notify calls for every real user who
    enabled notifications on that repo.

    Runs unauthenticated (ctx.user.imperal_id == "__webhook__") per
    @ext.webhook's contract — HMAC verification below is the only trust
    boundary, same principle as auth.py's state-token check for the other
    (unsigned, GET-redirect) webhook.
    """
    secret = await ctx.secrets.get("github_webhook_secret")
    if not secret:
        await ctx.log("webhook_events: rejected — github_webhook_secret not configured", level="warning")
        return {"status": 501, "body": "Webhook secret not configured."}

    signature = headers.get("x-hub-signature-256", headers.get("X-Hub-Signature-256", ""))
    if not _verify_signature(secret, body, signature):
        await ctx.log("webhook_events: rejected — signature mismatch", level="warning")
        return {"status": 401, "body": "Signature verification failed."}

    event_name = headers.get("x-github-event", headers.get("X-GitHub-Event", ""))
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, TypeError):
        return {"status": 400, "body": "Malformed JSON body."}

    repo_full_name = _repo_full_name(payload)
    if not repo_full_name:
        # Some event types don't carry a repo we track notifications for.
        return {"status": 200, "body": "ignored"}

    imperal_ids = await storage.resolve_imperal_ids_for_repo(ctx, repo_full_name)
    if not imperal_ids:
        await ctx.log(f"webhook_events: no known watcher for repo={repo_full_name} — ignoring", level="info")
        return {"status": 200, "body": "ignored"}

    described = _describe_event(event_name, payload)
    if not described:
        return {"status": 200, "body": "ignored"}

    message, priority = described
    for imperal_id in imperal_ids:
        notify = _notify_for(ctx, imperal_id)
        try:
            await notify(message, priority=priority, channel="in_app")
        except Exception as e:
            await ctx.log(f"webhook_events: notify failed for {imperal_id}: {e}", level="error")

    return {"status": 200, "body": "ok"}
