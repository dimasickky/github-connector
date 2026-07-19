"""Tests for P6 CI/CD trigger tool (trigger_workflow_dispatch)."""
import pytest

from imperal_sdk.testing import MockContext

import handlers_actions
import storage
from models import TriggerWorkflowParams


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await storage.save_installation(ctx, {
        "installation_id": "12345", "account_login": "octocat",
        "repositories": ["octocat/hello-world"],
    })
    ctx.http.mock_post("access_tokens", {"token": "ghs_test_token"})
    return ctx


@pytest.mark.asyncio
async def test_trigger_workflow_dispatch_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/actions/workflows/deploy.yml/dispatches", {}, status=204)

    result = await handlers_actions.trigger_workflow_dispatch(
        ctx, TriggerWorkflowParams(repo="octocat/hello-world", workflow="deploy.yml", ref="main"))
    assert result.status == "success"
    assert result.data.workflow == "deploy.yml"
    assert result.data.ref == "main"


@pytest.mark.asyncio
async def test_trigger_workflow_dispatch_github_error():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/actions/workflows/nope.yml/dispatches", {"message": "Not Found"}, status=404)

    result = await handlers_actions.trigger_workflow_dispatch(
        ctx, TriggerWorkflowParams(repo="octocat/hello-world", workflow="nope.yml"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_trigger_workflow_dispatch_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_actions.trigger_workflow_dispatch(
        ctx, TriggerWorkflowParams(repo="octocat/hello-world", workflow="deploy.yml"))
    assert result.status == "error"
