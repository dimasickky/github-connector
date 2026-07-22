from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "github-connector-extension",
    version="0.7.0",
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


# ─── Secrets (app-scope: developer-owned GitHub OAuth App identity, shared
# by all users). Per-user data (connection status, per-repo webhook
# registrations) lives in ctx.store — see storage.py. These secrets are the
# OAuth App's own identity, the same for every user of this extension.
# Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub
# App -> classic OAuth App) — no more App-level identity (no app slug, no
# installation JWT signing key) needed at all; a classic OAuth App only ever
# needs a client_id/client_secret pair, same shape as spotify/google/etc. ── #

ext.secret(
    name="github_webhook_secret",
    description=(
        "Shared secret used to sign per-repo webhooks this extension "
        "registers on GitHub (one per repo with notifications enabled, see "
        "handlers_webhook_events.py) — verifies that incoming event "
        "deliveries really come from GitHub (HMAC-SHA256 signature check) "
        "before trusting them. Generate any random string; it's set as the "
        "webhook's own secret at registration time, not read from GitHub."
    ),
    scope="app",
    required=True,
    max_bytes=256,
)(lambda: None)

# ─── Classic OAuth App credentials (§12.2, 2026-07-23) — replaces the
# GitHub App + user-to-server OAuth design (§12.1). Register a classic OAuth
# App at github.com/settings/developers → OAuth Apps → New OAuth App, with
# Authorization callback URL = this extension's ctx.webhook_url("oauth_callback")
# value (visible from connect_github's return value / the sidebar's Connect
# button target once client_id is set). ── #

ext.secret(
    name="github_client_id",
    description=(
        "The classic OAuth App's Client ID (Developer settings → OAuth Apps "
        "→ your app). Public by nature (sent in browser redirect URLs), "
        "declared as a secret only for consistency with the rest of the "
        "App's identity."
    ),
    scope="app",
    required=True,
    max_bytes=128,
)(lambda: None)

ext.secret(
    name="github_client_secret",
    description=(
        "The classic OAuth App's Client Secret (Developer settings → OAuth "
        "Apps → your app → Generate a new client secret). Used server-side "
        "only, to exchange an authorization `code` for an access token — "
        "never sent to the browser, never logged."
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
