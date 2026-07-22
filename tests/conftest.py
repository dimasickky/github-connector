"""Shared pytest fixtures for github-connector tests.

Seeds ctx.secrets with the OAuth/encryption secrets the extension now
requires end-to-end (§12.1, 2026-07-23 pivot to full user-to-server auth):
github_client_id/github_client_secret (code-exchange + refresh) and
github_encryption_key (a real Fernet key, so token encrypt/decrypt actually
round-trips in tests rather than being mocked away) — mirroring the
"seed real crypto, don't mock it" approach already used in
wp-site-connector's test suite.
"""
import time

from cryptography.fernet import Fernet

import imperal_sdk.testing as _testing_mod
from imperal_sdk.testing import MockContext as _RealMockContext, MockSecretStore

TEST_APP_SLUG = "webbee-imperal-test"
TEST_WEBHOOK_SECRET = "test-webhook-secret"
TEST_CLIENT_ID = "Iv23test0000000000"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
TEST_ACCESS_TOKEN = "ghu_test_user_token"
TEST_REFRESH_TOKEN = "ghr_test_refresh_token"


def _mock_context_with_secrets(*args, **kwargs):
    ctx = _RealMockContext(*args, **kwargs)
    ctx.secrets = MockSecretStore({
        "github_app_slug": TEST_APP_SLUG,
        "github_webhook_secret": TEST_WEBHOOK_SECRET,
        "github_client_id": TEST_CLIENT_ID,
        "github_client_secret": TEST_CLIENT_SECRET,
        "github_encryption_key": TEST_ENCRYPTION_KEY,
    })
    return ctx


_testing_mod.MockContext = _mock_context_with_secrets


async def seed_user_token(ctx, expired=False) -> None:
    """Write a valid (or deliberately expired) encrypted user-token record
    directly into this ctx's own store partition — the equivalent of a
    completed install_callback, without re-running the whole code-exchange
    HTTP round trip in every handler test."""
    import storage
    import user_auth

    now = int(time.time())
    fernet = Fernet(TEST_ENCRYPTION_KEY.encode())
    record = {
        "access_token": fernet.encrypt(TEST_ACCESS_TOKEN.encode()).decode(),
        "refresh_token": fernet.encrypt(TEST_REFRESH_TOKEN.encode()).decode(),
        "access_expires_at": (now - 10) if expired else (now + 3600),
        "refresh_expires_at": (now - 10) if expired else (now + 3600 * 24 * 30),
    }
    await storage.save_user_token(ctx, record)

