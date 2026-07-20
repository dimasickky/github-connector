"""Tests for P5 destructive tools (merge/close/delete) — own two-step confirm flow.

Each tool has two states to verify: the preview call (confirm=False, the
default) must NOT perform the destructive HTTP call and must return
needs_confirmation=True; the confirmed call (confirm=True) must actually
call GitHub and return needs_confirmation=False.
"""
import pytest

from imperal_sdk.testing import MockContext

import handlers_pulls
import handlers_issues
import handlers_content
import storage
from models import MergePullRequestParams, CloseParams, DeleteBranchParams


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await storage.save_installation(ctx, {
        "installation_id": "12345", "account_login": "octocat",
        "repositories": ["octocat/hello-world"],
    })
    ctx.http.mock_post("access_tokens", {"token": "ghs_test_token"})
    return ctx


@pytest.mark.asyncio
async def test_merge_pull_request_preview_does_not_merge():
    ctx = await _seeded_ctx()
    # No merge mock registered — if the tool called it anyway, _find would
    # return a 404 "No mock registered" and status_code >= 400 would flip
    # the result to error, catching an accidental live call in preview mode.
    result = await handlers_pulls.merge_pull_request(
        ctx, MergePullRequestParams(repo="octocat/hello-world", number=5))
    assert result.status == "success"
    assert result.data.needs_confirmation is True


@pytest.mark.asyncio
async def test_merge_pull_request_preview_shows_diff():
    ctx = await _seeded_ctx()
    diff_body = "diff --git a/foo.py b/foo.py\n+added line\n"
    ctx.http.mock_get("/pulls/5", {"raw": diff_body})
    # MockHTTP.mock_get always JSON-encodes; patch response body directly to
    # simulate the raw diff text GitHub returns for the diff Accept header.
    ctx.http._mocks[-1] = ("GET", "/pulls/5", diff_body, 200, {})
    result = await handlers_pulls.merge_pull_request(
        ctx, MergePullRequestParams(repo="octocat/hello-world", number=5))
    assert result.status == "success"
    assert result.data.needs_confirmation is True
    assert result.ui is not None
    assert result.ui.to_dict()["type"] == "Code"
    assert "added line" in result.ui.to_dict()["props"]["content"]


@pytest.mark.asyncio
async def test_merge_pull_request_preview_warns_on_conflicting_state():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/pulls/5", {"number": 5, "mergeable_state": "dirty"})
    result = await handlers_pulls.merge_pull_request(
        ctx, MergePullRequestParams(repo="octocat/hello-world", number=5))
    assert result.status == "success"
    assert "conflict" in result.summary.lower()


@pytest.mark.asyncio
async def test_merge_pull_request_confirmed_merges():
    ctx = await _seeded_ctx()
    ctx.http._mocks.append(("PUT", "/pulls/5/merge", {"merged": True, "sha": "abc123"}, 200, {}))
    result = await handlers_pulls.merge_pull_request(
        ctx, MergePullRequestParams(repo="octocat/hello-world", number=5, confirm=True))
    assert result.status == "success"
    assert result.data.needs_confirmation is False


@pytest.mark.asyncio
async def test_close_pull_request_or_issue_preview_does_not_close():
    ctx = await _seeded_ctx()
    result = await handlers_issues.close_pull_request_or_issue(
        ctx, CloseParams(repo="octocat/hello-world", number=3))
    assert result.status == "success"
    assert result.data.needs_confirmation is True


@pytest.mark.asyncio
async def test_close_pull_request_or_issue_confirmed_closes():
    ctx = await _seeded_ctx()
    ctx.http._mocks.append(("PATCH", "/issues/3", {"number": 3, "state": "closed"}, 200, {}))
    result = await handlers_issues.close_pull_request_or_issue(
        ctx, CloseParams(repo="octocat/hello-world", number=3, confirm=True))
    assert result.status == "success"
    assert result.data.needs_confirmation is False


@pytest.mark.asyncio
async def test_delete_branch_preview_does_not_delete():
    ctx = await _seeded_ctx()
    result = await handlers_content.delete_branch(
        ctx, DeleteBranchParams(repo="octocat/hello-world", branch="stale-feature"))
    assert result.status == "success"
    assert result.data.needs_confirmation is True


@pytest.mark.asyncio
async def test_delete_branch_confirmed_deletes():
    ctx = await _seeded_ctx()
    ctx.http._mocks.append(("DELETE", "/git/refs/heads/stale-feature", {}, 204, {}))
    result = await handlers_content.delete_branch(
        ctx, DeleteBranchParams(repo="octocat/hello-world", branch="stale-feature", confirm=True))
    assert result.status == "success"
    assert result.data.needs_confirmation is False
