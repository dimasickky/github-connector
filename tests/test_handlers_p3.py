"""Tests for P3 read-only PR / issues / actions tools."""
import pytest

from imperal_sdk.testing import MockContext

import handlers_pulls
import handlers_issues
import handlers_actions
import storage
from models import ListPullsParams, ListIssuesParams, WorkflowRunsParams


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await storage.save_installation(ctx, {
        "installation_id": "12345", "account_login": "octocat",
        "repositories": ["octocat/hello-world"],
    })
    ctx.http.mock_post("access_tokens", {"token": "ghs_test_token"})
    return ctx


@pytest.mark.asyncio
async def test_list_pull_requests_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/pulls", [
        {"number": 7, "title": "Fix bug", "state": "open", "draft": False,
         "user": {"login": "octocat"}, "base": {"ref": "main"}, "head": {"ref": "fix-bug"},
         "created_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/octocat/hello-world/pull/7"},
    ])
    result = await handlers_pulls.list_pull_requests(ctx, ListPullsParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].number == 7
    assert result.data.items[0].author == "octocat"


@pytest.mark.asyncio
async def test_list_issues_excludes_pull_requests():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/issues", [
        {"number": 1, "title": "Real issue", "state": "open",
         "user": {"login": "octocat"}, "comments": 2, "created_at": "2026-01-01T00:00:00Z",
         "html_url": "https://github.com/octocat/hello-world/issues/1"},
        {"number": 2, "title": "Actually a PR", "state": "open", "pull_request": {},
         "user": {"login": "octocat"}, "comments": 0, "created_at": "2026-01-01T00:00:00Z",
         "html_url": "https://github.com/octocat/hello-world/pull/2"},
    ])
    result = await handlers_issues.list_issues(ctx, ListIssuesParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].number == 1


@pytest.mark.asyncio
async def test_get_workflow_runs_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/actions/runs", {
        "workflow_runs": [
            {"id": 99, "name": "CI", "run_number": 5, "conclusion": "success",
             "head_branch": "main", "event": "push", "created_at": "2026-01-01T00:00:00Z",
             "html_url": "https://github.com/octocat/hello-world/actions/runs/99"},
        ],
    })
    result = await handlers_actions.get_workflow_runs(ctx, WorkflowRunsParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].conclusion == "success"


@pytest.mark.asyncio
async def test_list_pull_requests_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_pulls.list_pull_requests(ctx, ListPullsParams(repo="octocat/hello-world"))
    assert result.status == "error"
