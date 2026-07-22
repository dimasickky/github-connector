from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "github-connector-extension",
    version="0.6.0",
    capabilities=["vcs:read", "vcs:write", "vcs:merge", "vcs:admin", "ci:trigger", "auth:oauth"],
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

# ─── User-to-server OAuth (adds acting-as-the-user on top of the App's own
# installation-token identity — needed for endpoints GitHub reserves for a
# real user (§12.1, 2026-07-23 pivot: this is now the ONLY auth mechanism —
# no App-level JWT/installation token is minted anywhere in this codebase).
# Requires "Request user authorization (OAuth) during installation" enabled
# in the GitHub App's own settings so GitHub's install redirect includes a
# `code` param. ── #

ext.secret(
    name="github_client_id",
    description=(
        "The GitHub App's OAuth Client ID (Developer settings → GitHub Apps "
        "→ your app → General). Public by nature (sent in browser redirect "
        "URLs), declared as a secret only for consistency with the rest of "
        "the App's identity."
    ),
    scope="app",
    required=True,
    max_bytes=128,
)(lambda: None)

ext.secret(
    name="github_client_secret",
    description=(
        "The GitHub App's OAuth Client Secret (Developer settings → GitHub "
        "Apps → your app → General → Client secrets → Generate a new client "
        "secret). Used server-side only, to exchange an authorization `code` "
        "for a user access token — never sent to the browser, never logged."
    ),
    scope="app",
    required=True,
    max_bytes=256,
)(lambda: None)

ext.secret(
    name="github_encryption_key",
    description=(
        "Fernet key (generate with `Fernet.generate_key()`) used to encrypt "
        "stored user OAuth tokens (access_token/refresh_token) at rest in "
        "ctx.store. Same pattern as wp-site-connector's wp_encryption_key — "
        "a dedicated key for this extension, not shared with any other."
    ),
    scope="app",
    required=True,
    max_bytes=128,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Liveness probe for the extension."""
    return {"status": "ok"}
