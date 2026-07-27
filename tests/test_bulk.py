"""Tests for the shared bulk pattern (bulk.py) and its two users.

The point of these is not that "delete works" — the single-item tools already
cover that. It is that the three guarantees bulk.py exists to make actually
hold: nothing is touched before confirm, an oversized batch is refused before
any network call, and one item failing neither hides nor discards the rest.
"""
import pytest

from imperal_sdk.testing import MockContext

import bulk
import handlers_content
import handlers_issues
from tests.conftest import seed_user_token
from models import DeleteBranchesParams, CloseIssuesParams


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await seed_user_token(ctx)
    return ctx


# ── the ceiling and the empty case ──────────────────────────────────────── #

def test_validate_batch_rejects_empty():
    assert bulk.validate_batch([], "branches") is not None


def test_validate_batch_rejects_oversized():
    too_many = [f"b{i}" for i in range(bulk.MAX_BULK_ITEMS + 1)]
    assert bulk.validate_batch(too_many, "branches") is not None


def test_validate_batch_accepts_exactly_the_ceiling():
    at_limit = [f"b{i}" for i in range(bulk.MAX_BULK_ITEMS)]
    assert bulk.validate_batch(at_limit, "branches") is None


@pytest.mark.asyncio
async def test_oversized_batch_makes_no_network_call():
    """The ceiling has to be enforced BEFORE the fan-out, or it protects
    nothing — that is the whole reason it exists."""
    ctx = await _seeded_ctx()
    result = await handlers_content.delete_branches(
        ctx, DeleteBranchesParams(
            repo="octocat/hello-world",
            branches=[f"feature/{i}" for i in range(bulk.MAX_BULK_ITEMS + 1)],
            confirm=True,
        ))
    assert result.status == "error"


# ── the preview gate ────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_delete_branches_preview_deletes_nothing():
    ctx = await _seeded_ctx()
    result = await handlers_content.delete_branches(
        ctx, DeleteBranchesParams(
            repo="octocat/hello-world", branches=["feature/a", "feature/b"]))

    assert result.status == "success"
    assert result.data.needs_confirmation is True
    assert result.data.succeeded == 0
    # every target is named in the preview — the realistic accident is not
    # noticing WHICH branch was in the list
    assert "feature/a" in result.summary and "feature/b" in result.summary


@pytest.mark.asyncio
async def test_close_issues_preview_closes_nothing():
    ctx = await _seeded_ctx()
    result = await handlers_issues.close_issues(
        ctx, CloseIssuesParams(repo="octocat/hello-world", numbers=[1, 2, 3]))

    assert result.status == "success"
    assert result.data.needs_confirmation is True
    assert result.data.total == 3


# ── the happy path ──────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_delete_branches_confirmed_deletes_all():
    ctx = await _seeded_ctx()
    for b in ("feature/a", "feature/b"):
        ctx.http._mocks.append(("DELETE", f"/git/refs/heads/{b}", {}, 204, {}))

    result = await handlers_content.delete_branches(
        ctx, DeleteBranchesParams(
            repo="octocat/hello-world",
            branches=["feature/a", "feature/b"], confirm=True))

    assert result.status == "success"
    assert result.data.succeeded == 2
    assert result.data.failed == 0
    assert result.data.needs_confirmation is False


@pytest.mark.asyncio
async def test_close_issues_confirmed_closes_all():
    ctx = await _seeded_ctx()
    for n in (12, 15):
        ctx.http._mocks.append(("PATCH", f"/issues/{n}", {"number": n, "state": "closed"}, 200, {}))

    result = await handlers_issues.close_issues(
        ctx, CloseIssuesParams(repo="octocat/hello-world", numbers=[12, 15], confirm=True))

    assert result.status == "success"
    assert result.data.succeeded == 2
    assert result.data.failed == 0


# ── partial failure: the case the whole design is for ───────────────────── #

@pytest.mark.asyncio
async def test_one_bad_branch_does_not_sink_the_batch():
    """Six of eight going through is a useful answer; an all-or-nothing
    refusal on a cleanup run is not."""
    ctx = await _seeded_ctx()
    ctx.http._mocks.append(("DELETE", "/git/refs/heads/feature/ok", {}, 204, {}))
    ctx.http._mocks.append(("DELETE", "/git/refs/heads/feature/gone", {}, 404, {}))

    result = await handlers_content.delete_branches(
        ctx, DeleteBranchesParams(
            repo="octocat/hello-world",
            branches=["feature/ok", "feature/gone"], confirm=True))

    assert result.status == "success"
    assert result.data.succeeded == 1
    assert result.data.failed == 1

    by_name = {i.title: i for i in result.data.items}
    assert by_name["feature/ok"].ok is True
    assert by_name["feature/gone"].ok is False
    assert by_name["feature/gone"].error  # carries a reason, not just False
    # and the failure is visible in the summary, not buried in the payload
    assert "feature/gone" in result.summary


@pytest.mark.asyncio
async def test_results_keep_caller_ordering():
    """A report you have to re-sort against what you asked for is one you
    cannot check at a glance."""
    ctx = await _seeded_ctx()
    asked = ["feature/c", "feature/a", "feature/b"]
    for b in asked:
        ctx.http._mocks.append(("DELETE", f"/git/refs/heads/{b}", {}, 204, {}))

    result = await handlers_content.delete_branches(
        ctx, DeleteBranchesParams(
            repo="octocat/hello-world", branches=asked, confirm=True))

    assert [i.title for i in result.data.items] == asked


@pytest.mark.asyncio
async def test_item_crash_is_attributed_not_fatal():
    """An unexpected exception must cost its own item, not the results the
    rest of the batch already earned."""
    async def _boom(target):
        if target == "bad":
            raise RuntimeError("kaboom")
        return None

    result = await bulk.run_bulk("delete", ["good", "bad"], "branch", _boom)

    assert result.status == "success"
    assert result.data.succeeded == 1
    assert result.data.failed == 1
    by_name = {i.title: i for i in result.data.items}
    assert "RuntimeError" in by_name["bad"].error


# ── summary wording ─────────────────────────────────────────────────────── #

def test_plural_handles_the_es_case():
    """'3 branchs' in a destructive summary reads like a broken tool."""
    assert bulk._plural(1, "branch") == "1 branch"
    assert bulk._plural(3, "branch") == "3 branches"
    assert bulk._plural(2, "issue") == "2 issues"
