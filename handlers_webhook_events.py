"""github-connector · real GitHub App webhook event delivery -> notifications.

Distinct from `auth.py`'s `install_callback` (a GET redirect from the
browser after the install-page flow finishes). This module is the *other*
kind of GitHub webhook: signed POST deliveries GitHub sends for repo/org
events (issues, pull_request, workflow_run, push, ...) once the App is
installed and configured with a Webhook URL + secret in its GitHub settings.

Flow:
1. GitHub POSTs to our `@ext.webhook("events")` URL for every event type the
   App subscribes to, with an `X-Hub-Signature-256` HMAC header computed over
   the raw body using the shared `github_webhook_secret`.
2. `webhook_events` verifies that signature (constant-time compare — timing
   side-channels on the compare itself are exactly what HMAC verification is
   supposed to close), rejecting anything that doesn't match with
   GH_WEBHOOK_SIGNATURE_INVALID (matches the code already declared in
   error_codes.py for the setup-callback's sibling check).
3. Every event payload carries `installation.id` — the ONLY identity info
   available to this otherwise-unauthenticated handler. `storage.
   resolve_imperal_id_for_installation` looks that up against the reverse
   index auth.py's `install_callback` writes on every successful install/
   reinstall, to find which real Imperal user to notify.
4. A small per-event-type table decides whether an event is notification-
   worthy at all (e.g. a PR being *opened* is; a PR being merely *labeled* is
   not, v1) and what message to send, then calls `ctx.notify(...)` — built
   the same way `storage._store_for` builds a StoreClient for an arbitrary
   user_id, because `ctx.notify` here is scoped to `__webhook__`, not the
   real recipient (see `_notify_for`).

Non-goals (v1, matches extensions/github-connector.md §2): no per-user
subscription preferences (which repos/event types to notify for) — every
connected repo notifies for the fixed event set below. No webhook redelivery/
backoff bookkeeping — GitHub itself retries failed deliveries, we don't need
our own queue for that.
"""
import hashlib
import hmac
import json

from app import ext
import storage


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
    """Receives signed GitHub App event deliveries and turns notification-
    worthy ones into ctx.notify calls for the right real user.

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

    installation_id = str((payload.get("installation") or {}).get("id", ""))
    if not installation_id:
        # Some event types (e.g. GitHub App-level "installation" lifecycle
        # events) don't carry a repo/installation we track notifications for.
        return {"status": 200, "body": "ignored"}

    imperal_id = await storage.resolve_imperal_id_for_installation(ctx, installation_id)
    if not imperal_id:
        await ctx.log(f"webhook_events: no known user for installation_id={installation_id} — ignoring", level="info")
        return {"status": 200, "body": "ignored"}

    described = _describe_event(event_name, payload)
    if not described:
        return {"status": 200, "body": "ignored"}

    message, priority = described
    notify = _notify_for(ctx, imperal_id)
    try:
        await notify(message, priority=priority, channel="in_app")
    except Exception as e:
        await ctx.log(f"webhook_events: notify failed for {imperal_id}: {e}", level="error")
        return {"status": 200, "body": "processed, notify failed"}

    return {"status": 200, "body": "ok"}
