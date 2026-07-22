"""github-connector · user-to-server OAuth (GitHub App "acting as the user").

Per extensions/github-connector.md §12.1 (2026-07-23 pivot): the App now
authenticates every API call as the real GitHub user who installed it,
not as the App's own bot identity. This is still the same GitHub App —
GitHub supports both token kinds on one App simultaneously — just with
"Request user authorization (OAuth) during installation" turned on in its
settings, which makes GitHub redirect back to our setup_url with a `code`
query param (in addition to `installation_id`) once the user finishes the
install/authorize screen.

Docs confirm this does NOT trade away per-repository scoping (the whole
reason we picked a GitHub App over a classic OAuth App/PAT in the first
place, §3): "a user access token can only access resources that both the
user and app have" (GitHub docs, generating-a-user-access-token-for-a-
github-app) — i.e. it's the intersection of what the user picked at
install time AND what they personally have rights to, never "everything".

Token lifecycle:
- Exchange `code` -> (user_access_token, refresh_token, expires_in,
  refresh_token_expires_in) via POST https://github.com/login/oauth/access_token.
- access_token is short-lived (~8h) -> refresh via the same endpoint with
  grant_type=refresh_token before every use if within ~5 min of expiry.
- refresh_token itself lives ~6 months (sliding window — refreshed on every
  use) -> if it's expired too, the user must reconnect from scratch.
- Both tokens are encrypted at rest with the same Fernet-at-rest pattern
  proven in wp-site-connector/crypto_util.py, using this extension's own
  app-scope `github_encryption_key` secret (never hardcoded, never shared
  across extensions).
"""
from __future__ import annotations

import time

from cryptography.fernet import Fernet, InvalidToken

_GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
# Refresh proactively once within this many seconds of expiry, so a tool
# call never races a token that expires mid-request.
_REFRESH_SKEW_SECONDS = 300


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
    since this module is new and never wrote plaintext)."""
    if not stored:
        return stored
    try:
        f = await _fernet(ctx)
        return f.decrypt(stored.encode()).decode()
    except InvalidToken:
        return stored
    except Exception:
        return stored


async def exchange_code_for_token(ctx, code: str) -> tuple[dict | None, str | None]:
    """Exchange the setup_url's `code` param for a user access token.
    Returns (token_payload, error_message). token_payload keys: access_token,
    refresh_token, expires_in, refresh_token_expires_in (raw, unencrypted —
    caller is responsible for encrypting before persisting)."""
    client_id = await ctx.secrets.get("github_client_id")
    client_secret = await ctx.secrets.get("github_client_secret")
    if not client_id or not client_secret:
        return None, "GitHub OAuth credentials are not configured (github_client_id / github_client_secret)."

    resp = await ctx.http.post(
        _GITHUB_OAUTH_TOKEN_URL,
        headers={"Accept": "application/json"},
        json={"client_id": client_id, "client_secret": client_secret, "code": code},
    )
    if resp.status_code >= 400:
        return None, f"GitHub rejected the authorization code exchange (HTTP {resp.status_code})."
    body = resp.json()
    if "error" in body:
        return None, f"GitHub OAuth error: {body.get('error_description') or body['error']}"
    if not body.get("access_token"):
        return None, "GitHub did not return an access token."
    return body, None


async def refresh_user_token(ctx, refresh_token: str) -> tuple[dict | None, str | None]:
    """Exchange a still-valid refresh_token for a new access_token (and a new
    refresh_token — GitHub rotates it on every refresh, sliding ~6mo window)."""
    client_id = await ctx.secrets.get("github_client_id")
    client_secret = await ctx.secrets.get("github_client_secret")
    if not client_id or not client_secret:
        return None, "GitHub OAuth credentials are not configured (github_client_id / github_client_secret)."

    resp = await ctx.http.post(
        _GITHUB_OAUTH_TOKEN_URL,
        headers={"Accept": "application/json"},
        json={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        },
    )
    if resp.status_code >= 400:
        return None, f"GitHub rejected the token refresh (HTTP {resp.status_code})."
    body = resp.json()
    if "error" in body:
        return None, f"GitHub OAuth error: {body.get('error_description') or body['error']}"
    if not body.get("access_token"):
        return None, "GitHub did not return a refreshed access token."
    return body, None


async def encrypt_token_record(ctx, token_payload: dict) -> dict:
    """Turn a raw GitHub token-endpoint response into the encrypted-at-rest
    shape we persist: access/refresh tokens encrypted, expiries as absolute
    unix timestamps (so a refresh check never needs the original issued_at)."""
    now = int(time.time())
    return {
        "access_token": await _encrypt(ctx, token_payload["access_token"]),
        "refresh_token": await _encrypt(ctx, token_payload.get("refresh_token", "")),
        "access_expires_at": now + int(token_payload.get("expires_in", 0)),
        "refresh_expires_at": now + int(token_payload.get("refresh_token_expires_in", 0)),
    }


async def decrypt_token_record(ctx, record: dict) -> dict:
    """Inverse of encrypt_token_record — decrypt both tokens for use."""
    return {
        "access_token": await _decrypt(ctx, record.get("access_token", "")),
        "refresh_token": await _decrypt(ctx, record.get("refresh_token", "")),
        "access_expires_at": record.get("access_expires_at", 0),
        "refresh_expires_at": record.get("refresh_expires_at", 0),
    }


def access_token_needs_refresh(record: dict) -> bool:
    return int(time.time()) >= record.get("access_expires_at", 0) - _REFRESH_SKEW_SECONDS


def refresh_token_expired(record: dict) -> bool:
    expires_at = record.get("refresh_expires_at", 0)
    return bool(expires_at) and int(time.time()) >= expires_at
