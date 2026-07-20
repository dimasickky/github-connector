"""github-connector · install/connect flow.

Follows the manual OAuth-like pattern already proven by spotify/app.py +
handlers/auth.py (per extensions/github-connector.md §4), adapted for a
GitHub App install rather than a classic OAuth authorize/token exchange:

1. `start_install` (authenticated chat.function) generates a one-shot state
   token, records it (storage.save_oauth_state — written under the shared
   "__webhook__" store partition so the webhook can find it later), and
   returns the public GitHub install URL for the user to open.
2. GitHub redirects back to our `setup_url` (`ctx.webhook_url("install_callback")`)
   with `installation_id` + `state` as query params.
3. `install_callback` (unauthenticated @ext.webhook) validates `state`,
   verifies the installation is live by minting a real installation token,
   fetches installation + repository metadata, and saves the finished
   installation record under the REAL user's store partition (resolved from
   the state token, not from the request — the request itself carries no
   trustworthy identity).

No GitHub webhook HMAC verification is needed on `install_callback` itself
(it's a GET redirect from the browser, not a signed webhook event) — HMAC
verification only applies to a future `webhook_events` handler receiving
actual GitHub App webhook deliveries (issue/PR/push events), not built here.
"""
import secrets as _secrets_mod

from imperal_sdk import ActionResult, ui
from pydantic import BaseModel, Field

from app import ext, chat
from error_codes import GH_INSTALLATION_NOT_FOUND
from imperal_sdk.chat.error_codes import INTERNAL
import github_client
import storage


class _NoParams(BaseModel):
    pass


class StartInstallResult(BaseModel):
    install_url: str = Field(description="Open this URL to install/authorize the GitHub App on your chosen repositories")


@chat.function(
    "start_github_install",
    description=(
        "Get the link to connect GitHub — opens GitHub's own installation "
        "page where you pick which repositories to grant access to. Use "
        "this when the user wants to connect/link their GitHub account."
    ),
    action_type="read",
    data_model=StartInstallResult,
)
async def start_github_install(ctx, params: _NoParams) -> ActionResult:
    """Generate a one-shot state token and return the GitHub App's public
    install URL. Does not touch any GitHub API — this is a pure redirect
    link, the actual installation happens on GitHub's own UI."""
    app_slug = await ctx.secrets.get("github_app_slug")
    if not app_slug:
        return ActionResult.error(
            "GitHub App is not configured yet (github_app_slug secret missing) — "
            "the developer needs to finish registering the GitHub App first.",
            code=INTERNAL,
        )

    state = _secrets_mod.token_urlsafe(24)
    await storage.save_oauth_state(ctx, state, ctx.user.imperal_id)

    install_url = f"https://github.com/apps/{app_slug}/installations/new?state={state}"
    return ActionResult.success(
        data={"install_url": install_url},
        summary=(
            f"Open this link to connect GitHub — choose which repositories "
            f"to give access to, then come back here: {install_url}"
        ),
        ui=ui.Stack([
            ui.Button(
                "Open GitHub install page",
                icon="Github",
                variant="primary",
                on_click=ui.Open(install_url),
            ),
            ui.Text("Pick which repositories to grant access to, then come back here."),
        ]),
    )


@ext.webhook("install_callback", method="GET")
async def install_callback(ctx, headers: dict, body: str, query_params: dict) -> dict:
    """GitHub redirects here after the user finishes the install-page flow,
    with `installation_id` and our own `state` as query params. Runs as an
    UNAUTHENTICATED webhook (ctx.user.imperal_id == "__webhook__") — the only
    thing that lets us attribute this to a real user is the `state` token
    matching one we wrote in `start_github_install` under the shared
    "__webhook__" store partition (storage.find_and_consume_oauth_state).

    Returns a plain dict (not ActionResult) per @ext.webhook's contract —
    this is an HTTP-level handler, not a chat tool call.
    """
    state = query_params.get("state", "")
    installation_id = query_params.get("installation_id", "")

    if not state or not installation_id:
        return {"status": 400, "body": "Missing state or installation_id."}

    imperal_id = await storage.find_and_consume_oauth_state(ctx, state)
    if not imperal_id:
        # Either forged/replayed state, or it expired — either way, do NOT
        # guess a user. Audit-log this as a rejected attempt, same principle
        # as wp-site-connector's manage_plugin/run_wp_cli reject logging.
        await ctx.log(f"install_callback: rejected — unknown/expired state (installation_id={installation_id})", level="warning")
        return {"status": 400, "body": "Invalid or expired install session. Please retry connecting GitHub from chat."}

    # Verify the installation is actually live and fetch its metadata using a
    # real installation token — do not trust query params alone.
    token, err = await github_client.get_installation_token(ctx, installation_id)
    if err:
        await ctx.log(f"install_callback: token mint failed for installation_id={installation_id}: {err}", level="error")
        return {"status": 502, "body": f"Could not verify the GitHub installation: {err}"}

    resp = await github_client.gh_get(ctx, token, "/installation/repositories")
    if resp.status_code >= 400:
        await ctx.log(f"install_callback: repository list fetch failed ({resp.status_code}) for installation_id={installation_id}", level="error")
        return {"status": 502, "body": "Connected, but could not read the repository list from GitHub."}

    data = resp.json()
    repos = data.get("repositories", []) if isinstance(data, dict) else []
    repo_names = [r.get("full_name", "") for r in repos]
    account_login = repos[0]["owner"]["login"] if repos else ""

    await storage.save_installation_for_user(ctx, imperal_id, {
        "installation_id": installation_id,
        "account_login": account_login,
        "repositories": repo_names,
    })

    # @ext.webhook has no event= param (that's chat.function-only) — the
    # sidebar panel's refresh="on_event:..." needs an explicit emit so it
    # actually re-fetches once the install finishes. ctx.extensions.emit is
    # the ExtensionsProtocol method for exactly this (context.py:106).
    try:
        await ctx.extensions.emit("github-connector.install_connected", {
            "imperal_id": imperal_id, "account_login": account_login,
        })
    except Exception as e:
        await ctx.log(f"install_callback: emit failed (non-fatal): {e}", level="warning")

    return {
        "status": 200,
        "body": (
            f"GitHub connected — {len(repo_names)} repositories linked "
            f"({account_login}). You can close this tab and go back to chat."
        ),
    }
