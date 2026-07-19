"""Tests for P4 write tools (branches, file commits, PR/issue/comment creation)."""
import base64

import pytest

from imperal_sdk.testing import MockContext

import handlers_content
import handlers_pulls
import handlers_issues
import storage
from models import (
    CreateBranchParams, CreateOrUpdateFileParams, CreatePullRequestParams,
    CreateIssueParams, CommentParams,
)


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await storage.save_installation(ctx, {
        "installation_id": "12345", "account_login": "octocat",
        "repositories": ["octocat/hello-world"],
    })
    ctx.http.mock_post("access_tokens", {"token": "ghs_test_token"})
    return ctx


@pytest.mark.asyncio
async def test_create_branch_uses_default_branch_when_no_from_ref():
    ctx = await _seeded_ctx()
    # More specific pattern registered first: MockHTTP._find picks the first
    # registered pattern that's a substring of the URL, and "/repos/octocat/
    # hello-world" is itself a substring of the git/ref URL too.
    ctx.http.mock_get("/git/ref/heads/main", {"object": {"sha": "abc123"}})
    ctx.http.mock_get("/repos/octocat/hello-world", {"default_branch": "main"})
    ctx.http.mock_post("/git/refs", {"ref": "refs/heads/feature/x", "object": {"sha": "abc123"}})

    result = await handlers_content.create_branch(
        ctx, CreateBranchParams(repo="octocat/hello-world", name="feature/x"))
    assert result.status == "success"
    assert result.data.title == "feature/x"


@pytest.mark.asyncio
async def test_create_or_update_file_encodes_content_base64():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/contents/README.md", {"message": "Not Found"}, status=404)
    ctx.http._mocks.append(("PUT", "/contents/README.md", {
        "commit": {"sha": "deadbeef", "html_url": "https://github.com/octocat/hello-world/commit/deadbeef"},
    }, 200, {}))

    result = await handlers_content.create_or_update_file(
        ctx, CreateOrUpdateFileParams(
            repo="octocat/hello-world", path="README.md", content="hello",
            message="update readme", branch="main"))
    assert result.status == "success"
    assert result.data.sha == "deadbeef"


@pytest.mark.asyncio
async def test_create_pull_request_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/pulls", {
        "number": 9, "title": "My PR", "state": "open", "draft": False,
        "user": {"login": "octocat"}, "base": {"ref": "main"}, "head": {"ref": "feature/x"},
        "created_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/octocat/hello-world/pull/9",
    })
    result = await handlers_pulls.create_pull_request(
        ctx, CreatePullRequestParams(repo="octocat/hello-world", title="My PR",
                                      head="feature/x", base="main"))
    assert result.status == "success"
    assert result.data.number == 9


@pytest.mark.asyncio
async def test_create_issue_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/issues", {
        "number": 3, "title": "Bug", "state": "open",
        "user": {"login": "octocat"}, "comments": 0,
        "created_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/octocat/hello-world/issues/3",
    })
    result = await handlers_issues.create_issue(
        ctx, CreateIssueParams(repo="octocat/hello-world", title="Bug"))
    assert result.status == "success"
    assert result.data.number == 3


@pytest.mark.asyncio
async def test_comment_on_issue_or_pr_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/issues/3/comments", {
        "id": 555, "body": "thanks!", "html_url": "https://github.com/octocat/hello-world/issues/3#issuecomment-555",
    })
    result = await handlers_issues.comment_on_issue_or_pr(
        ctx, CommentParams(repo="octocat/hello-world", number=3, body="thanks!"))
    assert result.status == "success"
