"""Shared pytest fixtures for github-connector tests.

Seeds ctx.secrets with a throwaway RSA keypair (github_app_private_key) and
app id/slug — mirroring the pattern in wp-site-connector/tests/conftest.py
(seed real crypto material into MockSecretStore rather than mocking the
crypto functions themselves, so JWT signing actually round-trips).
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import imperal_sdk.testing as _testing_mod
from imperal_sdk.testing import MockContext as _RealMockContext, MockSecretStore

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_KEY_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

TEST_APP_ID = "123456"
TEST_APP_SLUG = "webbee-imperal-test"
TEST_WEBHOOK_SECRET = "test-webhook-secret"


def _mock_context_with_secrets(*args, **kwargs):
    ctx = _RealMockContext(*args, **kwargs)
    ctx.secrets = MockSecretStore({
        "github_app_id": TEST_APP_ID,
        "github_app_private_key": TEST_PRIVATE_KEY_PEM,
        "github_app_slug": TEST_APP_SLUG,
        "github_webhook_secret": TEST_WEBHOOK_SECRET,
    })
    return ctx


_testing_mod.MockContext = _mock_context_with_secrets
