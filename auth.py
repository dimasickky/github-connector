"""github-connector · connect flow (classic OAuth App).

Per extensions/github-connector.md §12.2 (2026-07-23, second pivot: GitHub
App -> classic OAuth App): there is no more "install" step with a
repository picker — GitHub's classic OAuth Apps only have one screen,
"Authorize this app?", and once approved the token can reach everything on
GitHub the user themselves can reach. This is deliberately simpler than the
GitHub App flow it replaces (§12.1), traded for losing the per-repo scoping
that flow had (see extensions/github-connector.md §12.2 for the full
trade-off writeup — this was an explicit, discussed decision, not an
oversight).

Flow:
1. `connect_github` (authenticated chat.function) generates a one-shot state
   token, records it (storage.save_oauth_state — written under the shared
   "__webhook__" store partition so the webhook can find it later), and
   returns GitHub's `authorize` URL with our `client_id`, requested `scope`,
   and `state`.
2. GitHub redirects back to our registered callback URL
   (`ctx.webhook_url("oauth_callback")`) with `code` and our own `state` as
   query params — no `installation_id` exists in this model at all.
3. `oauth_callback` (unauthenticated @ext.webhook) validates `state`,
   exchanges `code` for an access token (user_auth.exchange_code_for_token),
   reads the account login (`GET /user`), and saves both the encrypted
   token record and a lightweight connection record under the REAL user's
   store partition (resolved from the state token, not from the request —
   the request itself carries no trustworthy identity).

Scopes requested: `repo` (full repo read/write — classic OAuth has no
finer-grained repo scope), `read:org` (list/read org repos the user can
reach), `workflow` (required specifically to trigger/update GitHub Actions
workflow files — GitHub rejects workflow-file writes without it even if
`repo` is granted), `admin:repo_hook` (create/delete the per-repo webhooks
this pivot now requires, see handlers_webhook_events.py).

No GitHub webhook HMAC verification is needed on `oauth_callback` itself
(it's a GET redirect from the browser, not a signed webhook event) — HMAC
verification only applies to `handlers_webhook_events.py`'s per-repo webhook
deliveries (issue/PR/push events), which is a separate handler.
"""
import secrets as _secrets_mod
import urllib.parse

from imperal_sdk import ActionResult, ui
from pydantic import BaseModel, Field

from app import ext, chat
from error_codes import GH_NOT_CONNECTED
from imperal_sdk.chat.error_codes import INTERNAL
from models import DestructiveActionResult
import github_client
import storage
import user_auth

_OAUTH_SCOPES = "repo read:org workflow admin:repo_hook"


class _NoParams(BaseModel):
    pass


class _ConfirmParams(BaseModel):
    confirm: bool = Field(default=False, description="Set true on a second call to actually disconnect. First call (default) only previews.")


class ConnectResult(BaseModel):
    authorize_url: str = Field(description="Open this URL to authorize GitHub access")


async def create_authorize_url(ctx) -> str:
    """Create a one-shot state and return the matching GitHub OAuth authorize
    URL. Shared by the chat function and the sidebar. Panel buttons must
    receive a concrete ``ui.Open(url)`` action at render time: a panel
    ``ui.Call`` only displays the returned ActionResult summary as a toast
    and does not execute UI actions nested in that result."""
    client_id = await ctx.secrets.get("github_client_id")
    if not client_id:
        return ""

    state = _secrets_mod.token_urlsafe(24)
    await storage.save_oauth_state(ctx, state, ctx.user.imperal_id)
    callback_url = ctx.webhook_url("oauth_callback")
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": callback_url,
        "scope": _OAUTH_SCOPES,
        "state": state,
    })
    return f"https://github.com/login/oauth/authorize?{params}"


@chat.function(
    "connect_github",
    description=(
        "Get the link to connect your GitHub account — opens GitHub's own "
        "authorization page. Use this when the user wants to connect/link "
        "their GitHub account."
    ),
    action_type="read",
    data_model=ConnectResult,
)
async def connect_github(ctx, params: _NoParams) -> ActionResult:
    """Generate a one-shot state token and return GitHub's authorize URL.
    Does not touch any GitHub API — this is a pure redirect link, the actual
    authorization happens on GitHub's own UI."""
    authorize_url = await create_authorize_url(ctx)
    if not authorize_url:
        return ActionResult.error(
            "GitHub OAuth App is not configured yet (github_client_id secret "
            "missing) — the developer needs to finish registering the OAuth "
            "App first.",
            code=INTERNAL,
        )

    return ActionResult.success(
        data={"authorize_url": authorize_url},
        summary=f"Open this link to connect GitHub, then come back here: {authorize_url}",
        ui=ui.Stack([
            ui.Button(
                "Authorize GitHub", icon="Github", variant="primary",
                on_click=ui.Open(authorize_url),
            ),
            ui.Text("Approve access on GitHub's page, then come back here."),
        ]),
    )


@chat.function(
    "disconnect_github",
    description=(
        "Disconnect your GitHub account — removes the stored access token. "
        "GitHub's own authorization record is not revoked automatically "
        "(revoke it from github.com/settings/applications if you also want "
        "that gone); this just makes Imperal forget about it. Requires an "
        "explicit confirm=true on a second call — the first call only "
        "previews."
    ),
    action_type="destructive",
    data_model=DestructiveActionResult,
    effects=["github.disconnect"],
    event="github-connector-extension.install_disconnected",
)
async def disconnect_github(ctx, params: _ConfirmParams) -> ActionResult:
    """Two-step confirm flow, same pattern as delete_branch/merge_pull_request."""
    connection = await storage.get_connection(ctx)
    if not connection:
        return ActionResult.error(
            "No GitHub account connected — nothing to disconnect.",
            retryable=False, code=GH_NOT_CONNECTED,
        )

    if not params.confirm:
        account = connection.get("account_login", "")
        return ActionResult.success(
            DestructiveActionResult(
                id=account or "github", title=account or "GitHub", kind="github_connection",
                action="disconnect", needs_confirmation=True,
            ),
            summary=(
                f"This will disconnect GitHub account '{account}' from Imperal "
                "— repository access from chat/panels will stop working until "
                "you reconnect. Call again with confirm=true to actually disconnect."
            ),
        )

    import handlers_webhook_events
    await handlers_webhook_events.disable_all_repo_notifications(ctx)

    await storage.delete_connection(ctx)
    await storage.delete_user_token(ctx)
    try:
        await ctx.extensions.emit("github-connector.install_disconnected", {
            "imperal_id": ctx.user.imperal_id,
        })
    except Exception as e:
        await ctx.log(f"disconnect_github: emit failed (non-fatal): {e}", level="warning")

    return ActionResult.success(
        DestructiveActionResult(
            id="github", title="GitHub", kind="github_connection",
            action="disconnect", needs_confirmation=False,
        ),
        summary="GitHub disconnected. You can reconnect any time from the sidebar.",
        refresh_panels=["sidebar"],
    )


@ext.webhook("oauth_callback", method="GET")
async def oauth_callback(ctx, headers: dict, body: str, query_params: dict) -> dict:
    """GitHub redirects here after the user approves (or denies) the
    authorize screen, with `code` and our own `state` as query params. Runs
    as an UNAUTHENTICATED webhook (ctx.user.imperal_id == "__webhook__") —
    the only thing that lets us attribute this to a real user is the
    `state` token matching one we wrote in `connect_github` under the shared
    "__webhook__" store partition (storage.find_and_consume_oauth_state).

    Returns a plain dict (not ActionResult) per @ext.webhook's contract —
    this is an HTTP-level handler, not a chat tool call.
    """
    state = query_params.get("state", "")
    code = query_params.get("code", "")
    oauth_error = query_params.get("error", "")

    if oauth_error:
        # The user clicked "Cancel" on GitHub's authorize screen, or GitHub
        # itself rejected the request — either way this is not a bug on our
        # side, so say so plainly rather than a generic 400.
        return {"status": 200, "body": f"GitHub authorization was not completed ({oauth_error}). You can retry connecting GitHub from chat any time."}

    if not state or not code:
        return {"status": 400, "body": "Missing state or code. Please retry connecting GitHub from chat."}

    imperal_id = await storage.find_and_consume_oauth_state(ctx, state)
    if not imperal_id:
        # Either forged/replayed state, or it expired — either way, do NOT
        # guess a user. Audit-log this as a rejected attempt, same principle
        # as wp-site-connector's manage_plugin/run_wp_cli reject logging.
        await ctx.log("oauth_callback: rejected — unknown/expired state", level="warning")
        return {"status": 400, "body": "Invalid or expired connect session. Please retry connecting GitHub from chat."}

    token_payload, err = await user_auth.exchange_code_for_token(ctx, code)
    if err:
        await ctx.log(f"oauth_callback: code exchange failed: {err}", level="error")
        return {"status": 502, "body": f"Could not complete GitHub authorization: {err}"}

    access_token = token_payload["access_token"]

    resp = await github_client.gh_get(ctx, access_token, "/user")
    if resp.status_code >= 400:
        await ctx.log(f"oauth_callback: /user lookup failed ({resp.status_code})", level="error")
        return {"status": 502, "body": "Connected, but could not read your GitHub account info."}

    account_login = resp.json().get("login", "")

    encrypted_record = await user_auth.encrypt_token_record(ctx, token_payload)
    await storage.save_user_token_for_user(ctx, imperal_id, encrypted_record)
    await storage.save_connection_for_user(ctx, imperal_id, {"account_login": account_login})

    # @ext.webhook has no event= param (that's chat.function-only) — the
    # sidebar panel's refresh="on_event:..." needs an explicit emit so it
    # actually re-fetches once the connect finishes. ctx.extensions.emit is
    # the ExtensionsProtocol method for exactly this (context.py:106).
    #
    # This handler runs under the webhook's own pseudo-identity
    # (ctx.user.imperal_id == "__webhook__"), so ctx.extensions.emit would
    # publish the event as "__webhook__" — a session the real user's panel
    # is never subscribed to, meaning the sidebar's refresh="on_event:..."
    # never actually fires for them. Emit through an ExtensionsClient
    # rescoped to the real imperal_id instead, same rescoping trick
    # storage.py already uses for the store.
    try:
        await storage._extensions_for(ctx, imperal_id).emit("github-connector.install_connected", {
            "imperal_id": imperal_id, "account_login": account_login,
        })
    except Exception as e:
        await ctx.log(f"oauth_callback: emit failed (non-fatal): {e}", level="warning")

    return {
        "status": 200,
        "body": f"GitHub connected as {account_login}. You can close this tab and go back to chat.",
    }
