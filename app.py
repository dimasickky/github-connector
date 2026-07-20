from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "github-connector-extension",
    version="0.4.0",
    capabilities=["vcs:read", "vcs:write", "vcs:merge", "ci:trigger", "auth:oauth"],
    display_name="GitHub Connector",
    description=(
        "Browse your GitHub repositories, view code and commit history, and "
        "manage pull requests and issues directly from chat."
    ),
    icon="icon.svg",
    actions_explicit=True,
)

chat = ChatExtension(
    ext, tool_name="github-connector",
    description="Connect GitHub repositories and browse code, commits, pull requests, and issues",
)


# ─── Secrets (app-scope: developer-owned GitHub App identity, shared by all users) ── #
# Per-user data (which installation_id belongs to which Imperal user, which
# repos it covers) lives in ctx.store — see storage.py. These two secrets are
# the GitHub App's own identity, the same for every user of this extension.

ext.secret(
    name="github_app_slug",
    description=(
        "The GitHub App's URL slug, e.g. 'webbee-imperal' for "
        "github.com/apps/webbee-imperal — shown in the App's settings URL "
        "on github.com. Public by nature (it's part of a public URL), but "
        "declared as a secret for consistency with the rest of the App's "
        "identity and to keep it configurable without a code change."
    ),
    scope="app",
    required=True,
    max_bytes=128,
)(lambda: None)

ext.secret(
    name="github_app_id",
    description=(
        "Numeric GitHub App ID, shown at the top of the App's settings page "
        "on github.com (Developer settings → GitHub Apps → your app)."
    ),
    scope="app",
    required=True,
    max_bytes=32,
)(lambda: None)

ext.secret(
    name="github_app_private_key",
    description=(
        "PEM-encoded private key generated for the GitHub App (Developer "
        "settings → GitHub Apps → your app → Private keys → Generate a "
        "private key). Used to sign short-lived JWTs that are exchanged for "
        "per-installation access tokens — never sent to GitHub directly, "
        "never logged, never returned to chat."
    ),
    scope="app",
    required=True,
    max_bytes=8192,
)(lambda: None)

ext.secret(
    name="github_webhook_secret",
    description=(
        "Shared secret configured in the GitHub App's Webhook settings, used "
        "to verify that incoming setup-callback requests really come from "
        "GitHub (HMAC-SHA256 signature check) before trusting them."
    ),
    scope="app",
    required=False,
    max_bytes=256,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Liveness probe for the extension."""
    return {"status": "ok"}
