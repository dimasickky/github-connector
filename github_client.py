"""github-connector · thin GitHub REST client + JWT/installation-token minting.

Mechanics (per extensions/github-connector.md §3-4): App-level JWT (RS256,
signed with the GitHub App's own private key) is exchanged for a short-lived
(~1h) installation access token on every real call — no long-lived token is
ever stored (simpler than an OAuth refresh-token flow: nothing to keep
refreshing, `app_id` + private key + `installation_id` is always enough to
mint a fresh one). Callers ask for a ready-to-use installation token via
`get_installation_token(ctx, installation_id)`; everything else in this
module is a thin `ctx.http` wrapper over the GitHub REST API using that
token, following the same shape as wp-site-connector's `wp_client.py`.
"""
import time

import jwt as _pyjwt

_GITHUB_API = "https://api.github.com"
_JWT_TTL_SECONDS = 540  # GitHub caps this at 600s; 540 leaves clock-skew margin

_EXT_LANG = {
    "py": "python", "js": "javascript", "ts": "typescript", "tsx": "typescript",
    "jsx": "javascript", "json": "json", "md": "markdown", "yml": "yaml",
    "yaml": "yaml", "html": "html", "css": "css", "sh": "bash", "rb": "ruby",
    "go": "go", "rs": "rust", "java": "java", "php": "php", "sql": "sql",
}


def guess_language(filename: str) -> str:
    """Map a filename's extension to a Code component `language` hint.
    Shared by handlers_repos.py (chat rendering) and panels_browser.py
    (center panel) so both render the same syntax highlighting for a file."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _EXT_LANG.get(ext, "")

_ERROR_MESSAGES = {
    401: "GitHub rejected the installation token — the App installation may have been removed or its permissions changed.",
    403: "GitHub denied this request — the installation may not have permission for this repository/action, or GitHub's rate limit was hit.",
    404: "Not found on GitHub — check the repository name/path/number, and that this installation actually covers that repository.",
    422: "GitHub rejected the request content (validation error) — check the parameters.",
}


def _mint_app_jwt(app_id: str, private_key_pem: str) -> str:
    """Build a short-lived App-level JWT per GitHub's documented algorithm:
    RS256, `iss`=App ID, `iat`/`exp` within GitHub's allowed clock-skew window.
    This JWT authenticates as the App itself — it is ONLY ever used to mint an
    installation token (next step), never sent to a repo-level endpoint.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,  # backdate 60s for clock drift, per GitHub's own docs
        "exp": now + _JWT_TTL_SECONDS,
        "iss": app_id,
    }
    return _pyjwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(ctx, installation_id: str) -> tuple[str | None, str | None]:
    """Mint a fresh installation access token (~1h TTL, GitHub-side). Returns
    (token, error_message). Called on every real tool-call — no caching of the
    token itself here (the goal is to never persist a live GitHub credential;
    ctx.http itself is not asked to cache across calls either)."""
    app_id = await ctx.secrets.get("github_app_id")
    private_key = await ctx.secrets.get("github_app_private_key")
    if not app_id or not private_key:
        return None, "GitHub App credentials are not configured (github_app_id / github_app_private_key)."

    app_jwt = _mint_app_jwt(app_id, private_key)
    resp = await ctx.http.post(
        f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if resp.status_code == 404:
        return None, "GitHub installation not found — it may have been uninstalled or revoked on GitHub's side."
    if resp.status_code >= 400:
        return None, _ERROR_MESSAGES.get(resp.status_code, f"GitHub token request failed (HTTP {resp.status_code}).")
    return resp.json().get("token"), None


async def gh_get(ctx, token: str, path: str, params: dict | None = None):
    """GET a GitHub REST endpoint with an installation token. Returns the raw
    httpx-like response — callers check .status_code and decode .json()/.text
    themselves (mirrors wp_client.py's thin-wrapper shape, not hiding errors
    behind a generic exception)."""
    return await ctx.http.get(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params or {},
    )


async def gh_post(ctx, token: str, path: str, json_body: dict | None = None):
    return await ctx.http.post(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=json_body or {},
    )


async def gh_patch(ctx, token: str, path: str, json_body: dict | None = None):
    return await ctx.http.patch(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=json_body or {},
    )


async def gh_put(ctx, token: str, path: str, json_body: dict | None = None):
    """PUT — GitHub uses this (not PATCH) for a few write endpoints that are
    conceptually 'create or replace', notably create/update file contents and
    merge pull request."""
    return await ctx.http.put(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=json_body or {},
    )


async def gh_get_diff(ctx, token: str, path: str) -> tuple[str | None, int]:
    """GET a GitHub endpoint requesting the unified-diff representation
    (Accept: application/vnd.github.v3.diff) instead of JSON — used to show a
    PR's diff before an irreversible merge. Returns (diff_text_or_None,
    status_code); diff_text is None on a non-2xx response."""
    resp = await ctx.http.get(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if resp.status_code >= 400:
        return None, resp.status_code
    return resp.text(), resp.status_code


async def gh_delete(ctx, token: str, path: str):
    return await ctx.http.delete(
        f"{_GITHUB_API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def gh_error_message(status_code: int) -> str:
    if status_code in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[status_code]
    if 500 <= status_code < 600:
        return "GitHub returned a server error — try again shortly."
    return f"GitHub request failed (HTTP {status_code})."
