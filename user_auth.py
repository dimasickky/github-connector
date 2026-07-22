"""github-connector · classic OAuth App user token exchange + at-rest crypto.

Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub App
-> classic OAuth App): every API call authenticates as the real GitHub user,
same principle as the first §12.1 pivot — but the mechanics are simpler now
because a classic OAuth App has no installation concept and (critically) its
tokens do not expire by default:

- Exchange `code` -> access_token via POST
  https://github.com/login/oauth/access_token — GitHub's classic OAuth Apps
  web application flow. The response for a classic OAuth App is just
  {access_token, token_type, scope} — no refresh_token, no expires_in, unless
  the OAuth App owner has explicitly opted into "token expiration" (a GitHub
  App-only feature; not applicable here since we deliberately chose OAuth App
  over GitHub App this time). So this module does NOT implement a refresh
  cycle — there is nothing to refresh. If GitHub ever does return expires_in/
  refresh_token fields anyway (future GitHub product change, or if this App
  registration turns out to be a GitHub App under the hood after all), they
  are tolerated and stored, but no code currently reads them to force a
  refresh — a 401 from GitHub is the only real signal a token stopped
  working (revoked by the user, or the OAuth App's authorization removed),
  and the fix in that case is always "reconnect from scratch", never a
  refresh_token exchange.
- The access_token itself is still encrypted at rest with the same
  Fernet-at-rest pattern proven in wp-site-connector/crypto_util.py, using
  this extension's own app-scope `github_encryption_key` secret (never
  hardcoded, never shared across extensions) — losing the refresh cycle does
  not mean losing encryption at rest, that protection is unrelated to token
  lifetime.

Scope: a classic OAuth App token's reach is NOT restricted by anything we
configure ourselves the way a GitHub App installation's repository_selection
was — per extensions/github-connector.md §12.2, this means the token can
reach every repository (public and private) the authorizing user can
personally reach on GitHub, for whatever scopes we request at authorize time
(see auth.py's `_OAUTH_SCOPES`). This is the explicit, acknowledged trade-off
of this pivot: no more per-repo picker, in exchange for zero "repo not in
installation" friction.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

_GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"


async def _fernet(ctx) -> Fernet:
    key = (await ctx.secrets.get("github_encryption_key")) or ""
    if not key:
        raise RuntimeError(
            "github_encryption_key not set — configure it in Developer Portal → Secrets "
            "(generate one with Fernet.generate_key())."
        )
    return Fernet(key.encode())


async def _encrypt(ctx, plaintext: str) -> str:
    """Encrypt a token for storage. Empty input passes through unchanged."""
    if not plaintext:
        return plaintext
    f = await _fernet(ctx)
    return f.encrypt(plaintext.encode()).decode()


async def _decrypt(ctx, stored: str) -> str:
    """Decrypt a stored token. Falls back to the raw value for anything that
    isn't a valid Fernet token (defensive — should not happen in practice,
    since this module never wrote plaintext)."""
    if not stored:
        return stored
    try:
        f = await _fernet(ctx)
        return f.decrypt(stored.encode()).decode()
    except InvalidToken:
        return stored
    except Exception:
        return stored


async def exchange_code_for_token(ctx, code: str, redirect_uri: str = "") -> tuple[dict | None, str | None]:
    """Exchange the authorize callback's `code` param for a user access
    token. Returns (token_payload, error_message). token_payload keys:
    access_token, scope, token_type (raw, unencrypted — caller is
    responsible for encrypting before persisting)."""
    client_id = await ctx.secrets.get("github_client_id")
    client_secret = await ctx.secrets.get("github_client_secret")
    if not client_id or not client_secret:
        return None, "GitHub OAuth credentials are not configured (github_client_id / github_client_secret)."

    body = {"client_id": client_id, "client_secret": client_secret, "code": code}
    if redirect_uri:
        body["redirect_uri"] = redirect_uri

    resp = await ctx.http.post(
        _GITHUB_OAUTH_TOKEN_URL,
        headers={"Accept": "application/json"},
        json=body,
    )
    if resp.status_code >= 400:
        return None, f"GitHub rejected the authorization code exchange (HTTP {resp.status_code})."
    payload = resp.json()
    if "error" in payload:
        return None, f"GitHub OAuth error: {payload.get('error_description') or payload['error']}"
    if not payload.get("access_token"):
        return None, "GitHub did not return an access token."
    return payload, None


async def encrypt_token_record(ctx, token_payload: dict) -> dict:
    """Turn a raw GitHub token-endpoint response into the encrypted-at-rest
    shape we persist. Classic OAuth App tokens do not expire, so there is no
    expiry timestamp to track — only the token itself (encrypted) and the
    granted scope string (plaintext — not a secret, useful for
    diagnostics/support: "which permissions did this connection actually
    get")."""
    return {
        "access_token": await _encrypt(ctx, token_payload["access_token"]),
        "scope": token_payload.get("scope", ""),
    }


async def decrypt_token_record(ctx, record: dict) -> dict:
    """Inverse of encrypt_token_record — decrypt the token for use."""
    return {
        "access_token": await _decrypt(ctx, record.get("access_token", "")),
        "scope": record.get("scope", ""),
    }
