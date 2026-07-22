"""End-to-end (mocked) test of the connect flow: connect_github ->
storage state round-trip -> oauth_callback -> storage.get_connection.

Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub
App -> classic OAuth App) — there is no more installation_id/repository
picker in this flow at all; connect_github just returns GitHub's classic
`authorize` URL and oauth_callback resolves a plain {account_login}
connection record plus the OAuth token.
"""
import pytest

from imperal_sdk.testing import MockContext

import auth
import panels
import storage
from tests.conftest import TEST_CLIENT_ID, seed_user_token


@pytest.mark.asyncio
async def test_connect_github_returns_link_with_state():
    ctx = MockContext(user_id="user-1")
    result = await auth.connect_github(ctx, auth._NoParams())
    assert result.status == "success"
    assert TEST_CLIENT_ID in result.data["authorize_url"]
    assert "state=" in result.data["authorize_url"]

    # the state token must be retrievable (one-shot) for the same user
    url = result.data["authorize_url"]
    state = url.split("state=")[1].split("&")[0]
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
    assert TEST_CLIENT_ID in action["url"]
    assert "state=" in action["url"]


@pytest.mark.asyncio
async def test_connect_github_missing_client_id_errors():
    ctx = MockContext(user_id="user-1")
    ctx.secrets._store.pop("github_client_id", None)
    result = await auth.connect_github(ctx, auth._NoParams())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_oauth_callback_rejects_missing_params():
    ctx = MockContext(user_id="__webhook__")
    resp = await auth.oauth_callback(ctx, headers={}, body="", query_params={})
    assert resp["status"] == 400


@pytest.mark.asyncio
async def test_oauth_callback_rejects_unknown_state():
    ctx = MockContext(user_id="__webhook__")
    resp = await auth.oauth_callback(
        ctx, headers={}, body="",
        query_params={"state": "forged-state", "code": "some-code"},
    )
    assert resp["status"] == 400
    assert "Invalid or expired" in resp["body"]


@pytest.mark.asyncio
async def test_oauth_callback_reports_user_cancelled_authorization():
    ctx = MockContext(user_id="__webhook__")
    resp = await auth.oauth_callback(
        ctx, headers={}, body="",
        query_params={"error": "access_denied"},
    )
    assert resp["status"] == 200
    assert "not completed" in resp["body"]


@pytest.mark.asyncio
async def test_full_connect_round_trip_saves_connection_and_emits_event():
    # Step 1: user starts the connect flow from their own authenticated ctx.
    user_ctx = MockContext(user_id="user-42")
    start_result = await auth.connect_github(user_ctx, auth._NoParams())
    state = start_result.data["authorize_url"].split("state=")[1].split("&")[0]

    # Step 2: GitHub calls back on an unauthenticated webhook ctx, sharing
    # the SAME underlying MockStore/MockHTTP/MockSecretStore instances (as
    # real deployments share the same backing services across contexts).
    webhook_ctx = MockContext(user_id="__webhook__")
    webhook_ctx.store = user_ctx.store
    webhook_ctx.secrets = user_ctx.secrets
    webhook_ctx.http = user_ctx.http

    webhook_ctx.http.mock_post(
        "login/oauth/access_token",
        {"access_token": "gho_faketoken", "scope": "repo,read:org,workflow,admin:repo_hook", "token_type": "bearer"},
    )
    webhook_ctx.http.mock_get("/user", {"login": "dimasickky"})

    resp = await auth.oauth_callback(
        webhook_ctx, headers={}, body="",
        query_params={"state": state, "code": "fake-code"},
    )
    assert resp["status"] == 200
    assert "dimasickky" in resp["body"]

    connection = await storage.get_connection(user_ctx)
    assert connection is not None
    assert connection["account_login"] == "dimasickky"

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
async def test_oauth_callback_emits_event_scoped_to_real_user_not_webhook(monkeypatch):
    """Regression test for the sidebar-doesn't-auto-refresh bug: the emit must
    go out through a client rescoped to the REAL imperal_id, resolved from the
    same oauth-state lookup oauth_callback already does — not the webhook's
    own "__webhook__" pseudo-identity, which the real user's panel session
    never sees.
    """
    user_ctx = MockContext(user_id="user-77")
    start_result = await auth.connect_github(user_ctx, auth._NoParams())
    state = start_result.data["authorize_url"].split("state=")[1].split("&")[0]

    webhook_ctx = MockContext(user_id="__webhook__")
    webhook_ctx.store = user_ctx.store
    webhook_ctx.secrets = user_ctx.secrets
    webhook_ctx.http = user_ctx.http

    webhook_ctx.http.mock_post("login/oauth/access_token", {
        "access_token": "gho_faketoken", "scope": "repo", "token_type": "bearer",
    })
    webhook_ctx.http.mock_get("/user", {"login": "dimasickky"})

    seen_user_ids = []
    fake_client = _FakeProdExtensionsClient()

    def _fake_extensions_for(ctx, user_id):
        seen_user_ids.append(user_id)
        return fake_client

    monkeypatch.setattr(storage, "_extensions_for", _fake_extensions_for)

    resp = await auth.oauth_callback(
        webhook_ctx, headers={}, body="",
        query_params={"state": state, "code": "fake-code"},
    )
    assert resp["status"] == 200
    assert seen_user_ids == ["user-77"]  # real user, never "__webhook__"
    assert len(fake_client.emitted) == 1
    assert fake_client.emitted[0]["event_type"] == "github-connector.install_connected"
    assert fake_client.emitted[0]["data"]["imperal_id"] == "user-77"


@pytest.mark.asyncio
async def test_disconnect_github_no_connection_errors():
    ctx = MockContext(user_id="user-1")
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=True))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_disconnect_github_preview_does_not_delete():
    ctx = MockContext(user_id="user-1")
    await storage.save_connection(ctx, {"account_login": "octocat"})
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=False))
    assert result.status == "success"
    assert result.data.needs_confirmation is True
    # not actually deleted yet
    assert await storage.get_connection(ctx) is not None


@pytest.mark.asyncio
async def test_disconnect_github_confirmed_deletes_and_refreshes_sidebar():
    ctx = MockContext(user_id="user-1")
    await storage.save_connection(ctx, {"account_login": "octocat"})
    result = await auth.disconnect_github(ctx, auth._ConfirmParams(confirm=True))
    assert result.status == "success"
    assert result.data.needs_confirmation is False
    assert result.refresh_panels == ["sidebar"]
    assert await storage.get_connection(ctx) is None

    # sidebar must fall back to the "not connected" state afterwards
    tree = await panels.sidebar(ctx)
    assert tree.to_dict()["type"] == "Stack"
    assert tree.to_dict()["props"]["children"][0]["type"] == "Empty"


@pytest.mark.asyncio
async def test_sidebar_connected_shows_disconnect_button():
    ctx = MockContext(user_id="user-1")
    await storage.save_connection(ctx, {"account_login": "octocat"})
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", [])
    tree = await panels.sidebar(ctx)
    payload = tree.to_dict()
    footer = payload["props"]["children"][-1]
    assert footer["props"]["label"] == "Disconnect"
    assert footer["props"]["on_click"]["action"] == "call"
    assert footer["props"]["on_click"]["function"] == "disconnect_github"
    assert footer["props"]["on_click"]["params"]["confirm"] is True
