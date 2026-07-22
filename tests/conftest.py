"""Shared pytest fixtures for github-connector tests.

Seeds ctx.secrets with the secrets the extension now requires end-to-end
(§12.2, 2026-07-23 second pivot: GitHub App -> classic OAuth App):
github_client_id/github_client_secret (classic OAuth App code-exchange,
no installation/JWT signing key involved anymore) and github_encryption_key
(a real Fernet key, so token encrypt/decrypt actually round-trips in tests
rather than being mocked away) — mirroring the "seed real crypto, don't
mock it" approach already used in wp-site-connector's test suite.
"""
from cryptography.fernet import Fernet

import imperal_sdk.testing as _testing_mod
from imperal_sdk.testing import MockContext as _RealMockContext, MockSecretStore

TEST_WEBHOOK_SECRET = "test-webhook-secret"
TEST_CLIENT_ID = "test-client-id-0000"
TEST_CLIENT_SECRET = "test-client-secret"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
TEST_ACCESS_TOKEN = "gho_test_user_token"


def _mock_context_with_secrets(*args, **kwargs):
    ctx = _RealMockContext(*args, **kwargs)
    ctx.secrets = MockSecretStore({
        "github_webhook_secret": TEST_WEBHOOK_SECRET,
        "github_client_id": TEST_CLIENT_ID,
        "github_client_secret": TEST_CLIENT_SECRET,
        "github_encryption_key": TEST_ENCRYPTION_KEY,
    })
    return ctx


_testing_mod.MockContext = _mock_context_with_secrets


async def seed_user_token(ctx) -> None:
    """Write a valid encrypted user-token record directly into this ctx's
    own store partition — the equivalent of a completed oauth_callback,
    without re-running the whole code-exchange HTTP round trip in every
    handler test. Classic OAuth App tokens have no expiry to fake (§12.2)."""
    import storage

    fernet = Fernet(TEST_ENCRYPTION_KEY.encode())
    record = {
        "access_token": fernet.encrypt(TEST_ACCESS_TOKEN.encode()).decode(),
        "scope": "repo read:org workflow admin:repo_hook",
    }
    await storage.save_user_token(ctx, record)


async def seed_connection(ctx, account_login: str = "octocat") -> None:
    """Write a connection record directly — the equivalent of a completed
    oauth_callback's other half (seed_user_token covers the token itself)."""
    import storage

    await storage.save_connection(ctx, {"account_login": account_login})
