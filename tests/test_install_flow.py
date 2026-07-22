"""End-to-end (mocked) test of the install flow: start_github_install ->
storage state round-trip -> install_callback -> storage.get_installation.

Uses conftest's seeded RSA key so JWT signing in github_client actually
runs (not mocked away), matching the "seed real crypto, don't mock it"
approach already used in wp-site-connector's test suite.
"""
import pytest

from imperal_sdk.testing import MockContext

import auth
import panels
import storage
from tests.conftest import TEST_APP_SLUG


@pytest.mark.asyncio
async def test_start_github_install_returns_link_with_state():
    ctx = MockContext(user_id="user-1")
    result = await auth.start_github_install(ctx, auth._NoParams())
    assert result.status == "success"
    assert TEST_APP_SLUG in result.data["install_url"]
    assert "state=" in result.data["install_url"]

    # the state token must be retrievable (one-shot) for the same user
    url = result.data["install_url"]
    state = url.split("state=")[1]
    resolved = await storage.find_and_consume_oauth_state(ctx, state)
    assert resolved == "user-1"
    # one-shot: second lookup must fail
    resolved_again = await storage.find_and_consume_oauth_state(ctx, state)
    assert resolved_again is None


@pytest.mark.asyncio
async def test_sidebar_connect_button_opens_github_directly():
    ctx = MockContext(user_id="user-1")
    tree = await panels.sidebar(ctx)
    payload = tree.to_dict()

    button = payload["props"]["children"][1]
    action = button["props"]["on_click"]
    assert action["action"] == "open"
    assert TEST_APP_SLUG in action["url"]
    assert "state=" in action["url"]


@pytest.mark.asyncio
async def test_start_github_install_missing_slug_secret_errors():
    ctx = MockContext(user_id="user-1")
    ctx.secrets._store.pop("github_app_slug", None)
    result = await auth.start_github_install(ctx, auth._NoParams())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_install_callback_rejects_missing_params():
    ctx = MockContext(user_id="__webhook__")
    resp = await auth.install_callback(ctx, headers={}, body="", query_params={})
    assert resp["status"] == 400


@pytest.mark.asyncio
async def test_install_callback_rejects_unknown_state():
    ctx = MockContext(user_id="__webhook__")
    resp = await auth.install_callback(
        ctx, headers={}, body="",
        query_params={"state": "forged-state", "installation_id": "999", "code": "some-code"},
    )
    assert resp["status"] == 400
    assert "Invalid or expired" in resp["body"]


@pytest.mark.asyncio
async def test_full_install_round_trip_saves_installation_and_emits_event():
    # Step 1: user starts install from their own authenticated ctx.
    user_ctx = MockContext(user_id="user-42")
    start_result = await auth.start_github_install(user_ctx, auth._NoParams())
    state = start_result.data["install_url"].split("state=")[1]

    # Step 2: GitHub calls back on an unauthenticated webhook ctx, sharing
    # the SAME underlying MockStore/MockHTTP/MockSecretStore instances (as
    # real deployments share the same backing services across contexts).
    webhook_ctx = MockContext(user_id="__webhook__")
    webhook_ctx.store = user_ctx.store
    webhook_ctx.secrets = user_ctx.secrets
    webhook_ctx.http = user_ctx.http

    webhook_ctx.http.mock_post(
        "login/oauth/access_token",
        {"access_token": "ghu_faketoken", "refresh_token": "ghr_fakerefresh",
         "expires_in": 28800, "refresh_token_expires_in": 15811200},
    )
    webhook_ctx.http.mock_get(
        "/user/installations/555/repositories",
        {"repositories": [
            {"full_name": "dimasickky/repo-one", "owner": {"login": "dimasickky"}},
            {"full_name": "dimasickky/repo-two", "owner": {"login": "dimasickky"}},
        ]},
    )

    resp = await auth.install_callback(
        webhook_ctx, headers={}, body="",
        query_params={"state": state, "installation_id": "555", "code": "fake-code"},
    )
    assert resp["status"] == 200
    assert "2 repositories" in resp["body"]

    installation = await storage.get_installation(user_ctx)
    assert installation is not None
    assert installation["account_login"] == "dimasickky"
    assert installation["repositories"] == ["dimasickky/repo-one", "dimasickky/repo-two"]

    token_record = await storage.get_user_token(user_ctx)
    assert token_record is not None


class _FakeProdExtensionsClient:
    """Records what emit() was called with — used to inspect the rescoped
    client storage._extensions_for builds, instead of the plain MockExtensions
    both contexts start with.
    """

    def __init__(self):
        self.emitted: list[dict] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.emitted.append({"event_type": event_type, "data": data})


@pytest.mark.asyncio
async def test_install_callback_emits_event_scoped_to_real_user_not_webhook(monkeypatch):
    """Regression test for the sidebar-doesn't-auto-refresh bug: the emit must
    go out through a client rescoped to the REAL imperal_id, resolved from the
    same oauth-state lookup install_callback already does — not the webhook's
    own "__webhook__" pseudo-identity, which the real user's panel session
    never sees.
    """
    user_ctx = MockContext(user_id="user-77")
    start_result = await auth.start_github_install(user_ctx, auth._NoParams())
    state = start_result.data["install_url"].split("state=")[1]

    webhook_ctx = MockContext(user_id="__webhook__")
    webhook_ctx.store = user_ctx.store
    webhook_ctx.secrets = user_ctx.secrets
    webhook_ctx.http = user_ctx.http

    webhook_ctx.http.mock_post("login/oauth/access_token", {
        "access_token": "ghu_faketoken", "refresh_token": "ghr_fakerefresh",
        "expires_in": 28800, "refresh_token_expires_in": 15811200,
    })
    webhook_ctx.http.mock_get("/user/installations/777/repositories", {"repositories": [
        {"full_name": "dimasickky/repo-x", "owner": {"login": "dimasickky"}},
    ]})

    seen_user_ids = []
    fake_client = _FakeProdExtensionsClient()

    def _fake_extensions_for(ctx, user_id):
        seen_user_ids.append(user_id)
        return fake_client

    monkeypatch.setattr(storage, "_extensions_for", _fake_extensions_for)

    resp = await auth.install_callback(
        webhook_ctx, headers={}, body="",
        query_params={"state": state, "installation_id": "777", "code": "fake-code"},
    )
    assert resp["status"] == 200
    assert seen_user_ids == ["user-77"]  # real user, never "__webhook__"
    assert len(fake_client.emitted) == 1
    assert fake_client.emitted[0]["event_type"] == "github-connector.install_connected"
    assert fake_client.emitted[0]["data"]["imperal_id"] == "user-77"


@pytest.mark.asyncio
async def test_disconnect_github_no_installation_errors():
    ctx = MockContext(user_id="user-1")
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=True))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_disconnect_github_preview_does_not_delete():
    ctx = MockContext(user_id="user-1")
    await storage.save_installation(ctx, {
        "installation_id": "1", "account_login": "octocat", "repositories": ["octocat/hello"],
    })
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=False))
    assert result.status == "success"
    assert result.data.needs_confirmation is True
    # not actually deleted yet
    assert await storage.get_installation(ctx) is not None


@pytest.mark.asyncio
async def test_disconnect_github_confirmed_deletes_and_refreshes_sidebar():
    ctx = MockContext(user_id="user-1")
    await storage.save_installation(ctx, {
        "installation_id": "1", "account_login": "octocat", "repositories": ["octocat/hello"],
    })
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=True))
    assert result.status == "success"
    assert result.data.needs_confirmation is False
    assert result.refresh_panels == ["sidebar"]
    assert await storage.get_installation(ctx) is None

    # sidebar must fall back to the "not connected" state afterwards
    tree = await panels.sidebar(ctx)
    assert tree.to_dict()["type"] == "Stack"
    assert tree.to_dict()["props"]["children"][0]["type"] == "Empty"


@pytest.mark.asyncio
async def test_sidebar_connected_shows_switch_and_disconnect_buttons():
    ctx = MockContext(user_id="user-1")
    await storage.save_installation(ctx, {
        "installation_id": "1", "account_login": "octocat", "repositories": ["octocat/hello"],
    })
    tree = await panels.sidebar(ctx)
    payload = tree.to_dict()
    footer = payload["props"]["children"][-1]
    labels = [b["props"]["label"] for b in footer["props"]["children"]]
    assert "Switch account" in labels
    assert "Disconnect" in labels

    switch_btn = next(b for b in footer["props"]["children"] if b["props"]["label"] == "Switch account")
    assert switch_btn["props"]["on_click"]["action"] == "open"

    disconnect_btn = next(b for b in footer["props"]["children"] if b["props"]["label"] == "Disconnect")
    assert disconnect_btn["props"]["on_click"]["action"] == "call"
    assert disconnect_btn["props"]["on_click"]["function"] == "disconnect_github"
    assert disconnect_btn["props"]["on_click"]["params"]["confirm"] is True
