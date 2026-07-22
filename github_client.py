"""github-connector · thin GitHub REST client + user-token resolution.

Per extensions/github-connector.md §12.2 (2026-07-23, second pivot: GitHub
App -> classic OAuth App): every real API call authenticates as the GitHub
user themselves (classic OAuth token, see user_auth.py), not as any kind of
App-level bot identity. `get_user_token` resolves this user's stored
encrypted token record and returns a ready-to-use bearer token string — no
refresh cycle needed here (classic OAuth App tokens don't expire; see
user_auth.py's module docstring for why). A 401 from GitHub means the token
was revoked/the authorization was removed — the fix is always "reconnect",
never a refresh call. Everything else in this module is a thin `ctx.http`
wrapper over the GitHub REST API using that token, following the same shape
as wp-site-connector's `wp_client.py`.
"""
import user_auth
import storage

_GITHUB_API = "https://api.github.com"

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
    401: "GitHub rejected your access token — reconnect GitHub from the sidebar to refresh authorization.",
    403: "GitHub denied this request — your account may not have permission for this repository/action, or GitHub's rate limit was hit.",
    404: "Not found on GitHub — check the repository name/path/number, and that you actually have access to that repository.",
    422: "GitHub rejected the request content (validation error) — check the parameters.",
}


async def get_user_token(ctx) -> tuple[str | None, str | None]:
    """Resolve this user's stored classic OAuth access token. Returns
    (token, error_message). No refresh logic — classic OAuth App tokens
    don't expire; a stored-but-revoked token surfaces as a 401 from GitHub
    itself on the actual API call, not as something detectable here."""
    record = await storage.get_user_token(ctx)
    if not record:
        return None, "No GitHub account connected — use connect_github first."

    decrypted = await user_auth.decrypt_token_record(ctx, record)
    return decrypted["access_token"], None


async def gh_get(ctx, token: str, path: str, params: dict | None = None):
    """GET a GitHub REST endpoint with a bearer token. Returns the raw
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
