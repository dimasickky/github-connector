"""Tests for handlers_webhook_events.py — signed GitHub event delivery to
notifications. Uses conftest's TEST_WEBHOOK_SECRET seeded into ctx.secrets.
"""
import hashlib
import hmac
import json

import pytest

from imperal_sdk.testing import MockContext

import handlers_webhook_events as hwe
import storage
from tests.conftest import TEST_WEBHOOK_SECRET


def _sign(body: str) -> str:
    digest = hmac.new(TEST_WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(event: str, body: str) -> dict:
    return {"x-github-event": event, "x-hub-signature-256": _sign(body)}


@pytest.mark.asyncio
async def test_bad_signature_rejected():
    ctx = MockContext(user_id="__webhook__")
    body = json.dumps({"installation": {"id": 1}})
    result = await hwe.webhook_events(ctx, {"x-github-event": "issues", "x-hub-signature-256": "sha256=deadbeef"}, body, {})
    assert result["status"] == 401


@pytest.mark.asyncio
async def test_missing_secret_rejected():
    ctx = MockContext(user_id="__webhook__")
    ctx.secrets._store.pop("github_webhook_secret", None)
    body = json.dumps({"installation": {"id": 1}})
    result = await hwe.webhook_events(ctx, {"x-github-event": "issues", "x-hub-signature-256": "sha256=x"}, body, {})
    assert result["status"] == 501


@pytest.mark.asyncio
async def test_unknown_installation_ignored():
    ctx = MockContext(user_id="__webhook__")
    body = json.dumps({"installation": {"id": 999}, "action": "opened",
                        "issue": {"title": "x", "number": 1}, "repository": {"full_name": "a/b"}})
    result = await hwe.webhook_events(ctx, _headers("issues", body), body, {})
    assert result["status"] == 200
    assert result["body"] == "ignored"


@pytest.mark.asyncio
async def test_issue_opened_notifies_resolved_user():
    ctx = MockContext(user_id="__webhook__")
    await storage._index_installation(ctx, "42", "user-1")

    body = json.dumps({
        "installation": {"id": 42}, "action": "opened",
        "issue": {"title": "Something broke", "number": 7},
        "repository": {"full_name": "acme/widgets"},
    })
    result = await hwe.webhook_events(ctx, _headers("issues", body), body, {})
    assert result["status"] == 200
    assert result["body"] == "ok"
    assert len(ctx.notify.sent) == 1
    assert "Something broke" in ctx.notify.sent[0]["message"]
    assert "acme/widgets" in ctx.notify.sent[0]["message"]


@pytest.mark.asyncio
async def test_workflow_run_failure_is_high_priority():
    ctx = MockContext(user_id="__webhook__")
    await storage._index_installation(ctx, "42", "user-1")

    body = json.dumps({
        "installation": {"id": 42}, "action": "completed",
        "workflow_run": {"name": "CI", "conclusion": "failure"},
        "repository": {"full_name": "acme/widgets"},
    })
    result = await hwe.webhook_events(ctx, _headers("workflow_run", body), body, {})
    assert result["status"] == 200
    assert ctx.notify.sent[0]["priority"] == "high"
    assert "CI failed" in ctx.notify.sent[0]["message"]


@pytest.mark.asyncio
async def test_pull_request_labeled_is_silently_ignored():
    """v1 only surfaces opened/review_requested/merged — 'labeled' is noise."""
    ctx = MockContext(user_id="__webhook__")
    await storage._index_installation(ctx, "42", "user-1")

    body = json.dumps({
        "installation": {"id": 42}, "action": "labeled",
        "pull_request": {"number": 3, "title": "x", "merged": False},
        "repository": {"full_name": "acme/widgets"},
    })
    result = await hwe.webhook_events(ctx, _headers("pull_request", body), body, {})
    assert result["status"] == 200
    assert result["body"] == "ignored"
    assert ctx.notify.sent == []


@pytest.mark.asyncio
async def test_push_to_default_branch_notifies_low_priority():
    ctx = MockContext(user_id="__webhook__")
    await storage._index_installation(ctx, "42", "user-1")

    body = json.dumps({
        "installation": {"id": 42},
        "ref": "refs/heads/main",
        "pusher": {"name": "octocat"},
        "commits": [{"id": "abc"}, {"id": "def"}],
        "repository": {"full_name": "acme/widgets", "default_branch": "main"},
    })
    result = await hwe.webhook_events(ctx, _headers("push", body), body, {})
    assert result["status"] == 200
    assert ctx.notify.sent[0]["priority"] == "low"
    assert "2 new commit(s)" in ctx.notify.sent[0]["message"]


@pytest.mark.asyncio
async def test_resolve_imperal_id_for_installation_roundtrip():
    ctx = MockContext(user_id="__webhook__")
    assert await storage.resolve_imperal_id_for_installation(ctx, "42") is None
    await storage._index_installation(ctx, "42", "user-7")
    assert await storage.resolve_imperal_id_for_installation(ctx, "42") == "user-7"

    # re-installing (new installation_id resolved for the same user via
    # save_installation_for_user) updates the index rather than duplicating it
    await storage.save_installation_for_user(ctx, "user-7", {
        "installation_id": "43", "account_login": "octocat", "repositories": [],
    })
    assert await storage.resolve_imperal_id_for_installation(ctx, "43") == "user-7"
