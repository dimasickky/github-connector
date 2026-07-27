"""Store reads must not confuse a page size with a quantity.

These tests pin the three things that were wrong before:

1. `find_and_consume_oauth_state` was a linear scan of the first 200 rows of a
   partition SHARED by every user signing in at once. Past 200 parked rows a
   valid `state` stopped being found and the login failed for no visible
   reason. It is now a `where=` point lookup, so it must find a state that sits
   far outside any page window.
2. The skeleton reported `len(<capped page>)` as "repos watched" — a page size
   masquerading as a total. It now uses a real server-side COUNT.
3. The disconnect sweep needs EVERY webhook row (a row it misses becomes an
   orphaned hook on GitHub). Cursor paging is impossible on SDK 5.9.12 —
   `StoreClient.query()` takes no `cursor` — so the list exposes a truncation
   flag instead of pretending to be complete.
"""
import pytest
from imperal_sdk.testing import MockContext

import storage


@pytest.mark.asyncio
async def test_oauth_state_found_far_beyond_the_old_scan_window():
    """A valid state must resolve even with far more than 200 rows parked."""
    ctx = MockContext(user_id="u1")

    # 250 unrelated states from other in-flight sign-ins, then ours LAST —
    # deliberately outside the old limit=200 window.
    for i in range(250):
        await ctx.store.create(
            storage.OAUTH_STATES_COLLECTION,
            {"state": f"other-state-{i}", "imperal_id": f"imp_other_{i}"},
        )
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "the-real-state", "imperal_id": "imp_u_target"},
    )

    owner = await storage.find_and_consume_oauth_state(ctx, "the-real-state")
    assert owner == "imp_u_target", (
        "state past the 200-row mark must still resolve — this is the login "
        "that used to fail silently"
    )


@pytest.mark.asyncio
async def test_oauth_state_is_one_shot():
    """Consuming a state deletes it: a replay must not resolve twice."""
    ctx = MockContext(user_id="u1")
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "single-use", "imperal_id": "imp_u_1"},
    )

    assert await storage.find_and_consume_oauth_state(ctx, "single-use") == "imp_u_1"
    assert await storage.find_and_consume_oauth_state(ctx, "single-use") is None


@pytest.mark.asyncio
async def test_unknown_oauth_state_returns_none():
    ctx = MockContext(user_id="u1")
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "some-other", "imperal_id": "imp_u_1"},
    )
    assert await storage.find_and_consume_oauth_state(ctx, "not-in-store") is None


@pytest.mark.asyncio
async def test_count_repo_webhooks_is_not_a_page_length():
    """The count must exceed the list page cap, not saturate at it."""
    ctx = MockContext(user_id="u1")
    for i in range(_OVER_CAP := storage._WEBHOOK_PAGE_LIMIT + 25):
        await storage.save_repo_webhook(ctx, f"octocat/repo-{i}", 1000 + i)

    count = await storage.count_repo_webhooks(ctx)
    assert count == _OVER_CAP, (
        f"count must be the real total ({_OVER_CAP}), not the page cap "
        f"({storage._WEBHOOK_PAGE_LIMIT})"
    )
    assert count > storage._WEBHOOK_PAGE_LIMIT


@pytest.mark.asyncio
async def test_list_repo_webhooks_page_flags_truncation():
    """Over the cap: rows come back capped AND the caller is told so."""
    ctx = MockContext(user_id="u1")
    for i in range(storage._WEBHOOK_PAGE_LIMIT + 10):
        await storage.save_repo_webhook(ctx, f"octocat/repo-{i}", 2000 + i)

    records, truncated = await storage.list_repo_webhooks_page(ctx)
    assert len(records) == storage._WEBHOOK_PAGE_LIMIT
    assert truncated is True, (
        "the disconnect sweep must be able to learn it did not see everything"
    )


@pytest.mark.asyncio
async def test_list_repo_webhooks_page_not_truncated_when_small():
    """Under the cap: no false truncation alarm."""
    ctx = MockContext(user_id="u1")
    for i in range(3):
        await storage.save_repo_webhook(ctx, f"octocat/repo-{i}", 3000 + i)

    records, truncated = await storage.list_repo_webhooks_page(ctx)
    assert len(records) == 3
    assert truncated is False
