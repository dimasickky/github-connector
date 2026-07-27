"""OAuth state tokens must expire, and abandoned ones must not pile up.

Before this, `gh_oauth_states` rows had no expiry of any kind: the docstring
claimed "unknown/expired", but nothing ever checked a timestamp, and a row was
deleted only on a SUCCESSFUL callback. Every abandoned connect attempt — closed
tab, "Cancel" on GitHub's authorize screen, lost connectivity — left a row
behind permanently, in a partition shared by every user. Two separate problems:
the collection grew without bound, and a one-shot auth handoff token stayed
valid forever.

What is pinned here:

1. A fresh state still resolves (the TTL must not break the normal path).
2. An expired state is rejected...
3. ...and is consumed anyway, so a second attempt with it also fails. An
   expired token that survives to be retried is worse than no expiry at all.
4. Rows written before TTL existed (no `created_ts`) are still accepted —
   deploying this must not kill a sign-in that is already in flight.
5. The sweep removes expired rows, including OTHER users' — that is the whole
   point of it, and narrowing it to one user would quietly reintroduce the
   unbounded growth.
6. The sweep leaves fresh rows and legacy rows alone: it must never delete a
   connect attempt that could still legitimately complete.
"""
import time

import pytest
from imperal_sdk.testing import MockContext

import storage


@pytest.mark.asyncio
async def test_fresh_state_still_resolves():
    """The happy path must survive the TTL check."""
    ctx = MockContext(user_id="__webhook__")
    await storage.save_oauth_state(ctx, "fresh-state", "imp_u_owner")

    owner = await storage.find_and_consume_oauth_state(ctx, "fresh-state")
    assert owner == "imp_u_owner"


@pytest.mark.asyncio
async def test_expired_state_is_rejected():
    """Past the TTL the state must not authorise anyone."""
    ctx = MockContext(user_id="__webhook__")
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {
            "state": "stale-state",
            "imperal_id": "imp_u_owner",
            # 16 minutes old against a 15-minute TTL
            "created_ts": time.time() - (storage._STATE_TTL_SECONDS + 60),
        },
    )

    owner = await storage.find_and_consume_oauth_state(ctx, "stale-state")
    assert owner is None, "an expired state must not resolve to a user"


@pytest.mark.asyncio
async def test_expired_state_is_consumed_so_a_retry_also_fails():
    """One-shot applies to expired tokens too, not just valid ones."""
    ctx = MockContext(user_id="__webhook__")
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {
            "state": "stale-state",
            "imperal_id": "imp_u_owner",
            "created_ts": time.time() - (storage._STATE_TTL_SECONDS + 60),
        },
    )

    assert await storage.find_and_consume_oauth_state(ctx, "stale-state") is None
    # Second attempt: the row must be gone, not merely rejected once.
    assert await storage.find_and_consume_oauth_state(ctx, "stale-state") is None
    page = await ctx.store.query(
        storage.OAUTH_STATES_COLLECTION, where={"state": "stale-state"}, limit=1,
    )
    assert not page.data, "an expired state must be deleted, not left to be retried"


@pytest.mark.asyncio
async def test_legacy_row_without_timestamp_is_still_accepted():
    """Upgrading must not invalidate a sign-in that is already in flight."""
    ctx = MockContext(user_id="__webhook__")
    # Exactly what the pre-TTL code wrote: no created_ts at all.
    await ctx.store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "legacy-state", "imperal_id": "imp_u_owner"},
    )

    owner = await storage.find_and_consume_oauth_state(ctx, "legacy-state")
    assert owner == "imp_u_owner", (
        "a row written before TTL existed must not be treated as expired"
    )


@pytest.mark.asyncio
async def test_sweep_removes_expired_rows_including_other_users():
    """The sweep exists to clear the shared partition, not just my own rows."""
    ctx = MockContext(user_id="__webhook__")
    store = storage._store_for(ctx, "__webhook__")

    for i in range(5):
        await store.create(
            storage.OAUTH_STATES_COLLECTION,
            {
                "state": f"abandoned-{i}",
                "imperal_id": f"imp_other_{i}",
                "created_ts": time.time() - (storage._STATE_TTL_SECONDS + 600),
            },
        )

    removed = await storage._sweep_expired_states(store)
    assert removed == 5, "expired rows from other users must be swept too"

    page = await store.query(storage.OAUTH_STATES_COLLECTION, limit=100)
    assert len(page.data) == 0


@pytest.mark.asyncio
async def test_sweep_spares_fresh_and_legacy_rows():
    """A sweep that eats live attempts would be worse than the leak."""
    ctx = MockContext(user_id="__webhook__")
    store = storage._store_for(ctx, "__webhook__")

    await store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "fresh", "imperal_id": "imp_a", "created_ts": time.time()},
    )
    await store.create(
        storage.OAUTH_STATES_COLLECTION,
        {"state": "legacy", "imperal_id": "imp_b"},  # no created_ts
    )
    await store.create(
        storage.OAUTH_STATES_COLLECTION,
        {
            "state": "old",
            "imperal_id": "imp_c",
            "created_ts": time.time() - (storage._STATE_TTL_SECONDS + 60),
        },
    )

    removed = await storage._sweep_expired_states(store)
    assert removed == 1, "only the expired row should go"

    page = await store.query(storage.OAUTH_STATES_COLLECTION, limit=100)
    remaining = {doc.data["state"] for doc in page.data}
    assert remaining == {"fresh", "legacy"}


@pytest.mark.asyncio
async def test_new_attempt_sweeps_junk_left_by_earlier_ones():
    """The leak fix end-to-end: creating a state clears expired leftovers."""
    ctx = MockContext(user_id="__webhook__")
    store = storage._store_for(ctx, "__webhook__")

    for i in range(20):
        await store.create(
            storage.OAUTH_STATES_COLLECTION,
            {
                "state": f"junk-{i}",
                "imperal_id": f"imp_other_{i}",
                "created_ts": time.time() - (storage._STATE_TTL_SECONDS + 3600),
            },
        )

    await storage.save_oauth_state(ctx, "my-state", "imp_u_me")

    page = await store.query(storage.OAUTH_STATES_COLLECTION, limit=100)
    states = {doc.data["state"] for doc in page.data}
    assert states == {"my-state"}, (
        "abandoned attempts must not accumulate forever in the shared partition"
    )
    # And the new attempt itself must still work.
    assert await storage.find_and_consume_oauth_state(ctx, "my-state") == "imp_u_me"
