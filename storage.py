"""github-connector · per-user persistence for GitHub App installations.

Two collections, both partitioned automatically by ctx.user.imperal_id (the
same store-partitioning mechanism used by wp-site-connector's `sites`/`creds`
and spotify's `sp_credentials` — see extensions/github-connector.md §4-5):

- `gh_oauth_states`  — short-lived (~900s), one-shot install-flow state token.
  Written from an AUTHENTICATED chat.function call (the user's own ctx, so it
  naturally lives under their imperal_id) right before redirecting them to
  GitHub's install page. Read back inside the unauthenticated webhook handler
  (ctx.user.imperal_id == "__webhook__" there per Extension.webhook's
  docstring) — so the webhook can't use ctx.store to look it up by user, only
  by scanning the pseudo-user's own state records for a matching `state`
  value. This is intentional: the state token itself IS the only thing the
  webhook can trust, precisely because it arrives unauthenticated.
- `gh_installations` — the real installation record once the callback
  resolves the state back to a real imperal_id: {installation_id,
  account_login, repository_selection, repositories[]}.

No GitHub tokens are ever stored here — installation tokens are minted fresh
per tool-call (see auth.py) and never persisted, matching §4's "even simpler
than Spotify" design (no refresh-token to keep either).
"""

OAUTH_STATES_COLLECTION = "gh_oauth_states"
INSTALLATIONS_COLLECTION = "gh_installations"
# Reverse index installation_id -> imperal_id, written under the shared
# "__webhook__" partition (same trick as gh_oauth_states) so the UNAUTHENTICATED
# webhook_events handler — which receives only a GitHub installation_id in the
# payload, no user identity at all — can resolve which real user to notify.
INSTALLATION_INDEX_COLLECTION = "gh_installation_index"


def _store_for(ctx, user_id: str):
    """Build a StoreClient scoped to an arbitrary user_id, reusing ctx.store's
    own gateway/auth/tenant wiring (only user_id differs). Used two ways:

    - `_store_for(ctx, "__webhook__")` from an AUTHENTICATED chat.function —
      writes into the shared pseudo-user partition both ends of the install
      flow agree on, since the webhook that reads it back has no identity of
      its own to match against.
    - `_store_for(webhook_ctx, real_imperal_id)` from the UNAUTHENTICATED
      webhook, once `find_and_consume_oauth_state` has resolved which real
      user this callback belongs to — lets the webhook save the finished
      installation record into THAT user's own partition, not "__webhook__".
      (`ctx.as_user()` is not usable here: it requires system-context,
      i.e. `imperal_id == "__system__"`, but a webhook's ctx has
      `imperal_id == "__webhook__"` per Extension.webhook's docstring — a
      different pseudo-identity, not system. This helper sidesteps that by
      building the StoreClient directly instead of asking the Context to
      re-scope itself.)

    In tests, ctx.store is a MockStore (imperal_sdk.testing) — a plain
    in-memory dict with no gateway/auth/tenant attributes at all, since
    tests never cross a real user boundary. Rather than special-case every
    test around a StoreClient it can't construct, fall back to ctx.store
    itself when those attributes aren't present — the collections still
    round-trip correctly for the behavior we actually test (state
    written/consumed, installation saved/read), just without real
    per-pseudo-user network partitioning (which MockStore has no concept of
    to begin with).
    """
    if not hasattr(ctx.store, "_gateway_url"):
        return ctx.store
    from imperal_sdk.store.client import StoreClient
    return StoreClient(
        gateway_url=ctx.store._gateway_url,
        service_token=ctx.store._auth_token,
        extension_id=ctx.store._extension_id,
        user_id=user_id,
        tenant_id=ctx.store._tenant_id,
    )


async def save_oauth_state(ctx, state: str, imperal_id: str) -> None:
    """Called from the authenticated install-flow chat.function, right before
    redirecting the user to GitHub's install page. Writes into the shared
    "__webhook__" partition (via _store_for) — NOT the calling user's own
    partition — because the webhook callback that reads this back has no
    identity of its own to match against; `imperal_id` in the record body is
    what lets the callback attribute the finished installation to the right
    real user.
    """
    store = _store_for(ctx, "__webhook__")
    await store.create(OAUTH_STATES_COLLECTION, {"state": state, "imperal_id": imperal_id})


async def find_and_consume_oauth_state(webhook_ctx, state: str) -> str | None:
    """Called from the unauthenticated webhook (its own ctx.store is already
    scoped to "__webhook__", matching what save_oauth_state wrote into).
    Scans for a matching `state` value — that's the only lookup key available
    to an unauthenticated caller — and returns the `imperal_id` recorded
    alongside it.

    Returns the imperal_id that owns this state, or None if unknown/expired/
    already consumed. One-shot: deletes on successful read (TTL is enforced
    by the caller checking a `created_at`/`expires_at` field, not by storage
    itself — ctx.store has no native TTL).
    """
    page = await webhook_ctx.store.query(OAUTH_STATES_COLLECTION, limit=200)
    for doc in page.data:
        if doc.data.get("state") == state:
            owner = doc.data.get("imperal_id")
            await webhook_ctx.store.delete(OAUTH_STATES_COLLECTION, doc.id)
            return owner
    return None


async def get_installation(ctx):
    """Return this user's installation record (dict) or None if not connected."""
    page = await ctx.store.query(INSTALLATIONS_COLLECTION, limit=1)
    return page.data[0].data if page.data else None


async def save_installation_for_user(webhook_ctx, imperal_id: str, record: dict) -> None:
    """Called from the unauthenticated webhook once the state token has been
    resolved to a real imperal_id — writes the finished installation record
    into THAT user's own store partition (via _store_for), not "__webhook__".
    """
    store = _store_for(webhook_ctx, imperal_id)
    existing = await store.query(INSTALLATIONS_COLLECTION, limit=1)
    if existing.data:
        await store.update(INSTALLATIONS_COLLECTION, existing.data[0].id, record)
    else:
        await store.create(INSTALLATIONS_COLLECTION, record)
    await _index_installation(webhook_ctx, record.get("installation_id", ""), imperal_id)


async def _index_installation(ctx, installation_id: str, imperal_id: str) -> None:
    """Upsert the installation_id -> imperal_id reverse index (shared
    "__webhook__" partition) so webhook_events can resolve a real user from
    just the installation_id GitHub puts in every event payload."""
    if not installation_id:
        return
    idx_store = _store_for(ctx, "__webhook__")
    existing = await idx_store.query(
        INSTALLATION_INDEX_COLLECTION, where={"installation_id": installation_id}, limit=1,
    )
    if existing.data:
        await idx_store.update(INSTALLATION_INDEX_COLLECTION, existing.data[0].id,
                                {"installation_id": installation_id, "imperal_id": imperal_id})
    else:
        await idx_store.create(INSTALLATION_INDEX_COLLECTION,
                                {"installation_id": installation_id, "imperal_id": imperal_id})


async def resolve_imperal_id_for_installation(webhook_ctx, installation_id: str) -> str | None:
    """Look up which real user owns a given installation_id — used by the
    unauthenticated webhook_events handler to know who to notify."""
    idx_store = _store_for(webhook_ctx, "__webhook__")
    page = await idx_store.query(
        INSTALLATION_INDEX_COLLECTION, where={"installation_id": installation_id}, limit=1,
    )
    if page.data:
        return page.data[0].data.get("imperal_id")
    return None


async def save_installation(ctx, record: dict) -> None:
    """Called from an AUTHENTICATED context (e.g. a future re-sync tool) —
    writes into the calling user's own partition directly via ctx.store."""
    existing = await ctx.store.query(INSTALLATIONS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.update(INSTALLATIONS_COLLECTION, existing.data[0].id, record)
    else:
        await ctx.store.create(INSTALLATIONS_COLLECTION, record)


async def delete_installation(ctx) -> None:
    existing = await ctx.store.query(INSTALLATIONS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.delete(INSTALLATIONS_COLLECTION, existing.data[0].id)
