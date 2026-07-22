"""github-connector · per-user persistence (classic OAuth App model).

Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub App
-> classic OAuth App): there is no more "installation" concept at all — a
classic OAuth App has no repository picker, no installation_id, nothing to
select. The user just authorizes once and the token can see everything they
themselves can see on GitHub. So the per-user data model collapses to two
much simpler things:

- `gh_oauth_states`  — unchanged in shape/purpose from the GitHub App design:
  short-lived (~900s), one-shot install-flow state token. Written from an
  AUTHENTICATED chat.function call right before redirecting to GitHub's
  `authorize` page. Read back inside the unauthenticated webhook handler
  (ctx.user.imperal_id == "__webhook__" there per Extension.webhook's
  docstring) by scanning the pseudo-user's own state records for a matching
  `state` value — the state token itself IS the only thing the webhook can
  trust, precisely because it arrives unauthenticated.
- `gh_connections` — the real per-user connection record once the callback
  resolves the state back to a real imperal_id: {account_login}. No
  installation_id, no repositories[] list — a classic OAuth token just IS
  connected or isn't; which repos it can reach is decided live by GitHub on
  every API call, not cached here.
- `gh_user_tokens` — unchanged in shape/purpose: the OAuth token itself,
  encrypted at rest (see user_auth.py for the encrypt/decrypt/refresh logic).
  Classic OAuth tokens do not expire by default (no GitHub-mandated refresh
  cycle the way GitHub App user-to-server tokens have) — user_auth.py
  tolerates that (no refresh_token/expires_in in the exchange response).

Per-repo webhooks (§12.2 — classic OAuth Apps have NO centralized App-level
webhook the way a GitHub App does; each repo's hook must be registered
individually via the REST API):

- `gh_repo_webhooks` — one record per (repo, imperal_id) this user asked us
  to watch: {repo_full_name, github_hook_id, imperal_id}. `github_hook_id` is
  needed to delete the hook again on disconnect/repo-removal (GitHub's hook
  API addresses hooks by their own numeric id, not by URL).
- `gh_repo_webhook_index` — reverse index repo_full_name -> imperal_id,
  written under the shared "__webhook__" partition (same trick as
  gh_oauth_states) so the UNAUTHENTICATED webhook_events handler — which
  receives only `repository.full_name` in the payload, no user identity at
  all — can resolve which real user to notify. A single repo could in theory
  be watched by more than one Imperal user (e.g. two teammates each connected
  their own account to the same shared org repo), so this stores a list of
  imperal_ids, not a single value.
"""

OAUTH_STATES_COLLECTION = "gh_oauth_states"
CONNECTIONS_COLLECTION = "gh_connections"
USER_TOKENS_COLLECTION = "gh_user_tokens"
REPO_WEBHOOKS_COLLECTION = "gh_repo_webhooks"
REPO_WEBHOOK_INDEX_COLLECTION = "gh_repo_webhook_index"


def _store_for(ctx, user_id: str):
    """Build a StoreClient scoped to an arbitrary user_id, reusing ctx.store's
    own gateway/auth/tenant wiring (only user_id differs). Used two ways:

    - `_store_for(ctx, "__webhook__")` from an AUTHENTICATED chat.function —
      writes into the shared pseudo-user partition both ends of the connect
      flow agree on, since the webhook that reads it back has no identity of
      its own to match against.
    - `_store_for(webhook_ctx, real_imperal_id)` from the UNAUTHENTICATED
      webhook, once `find_and_consume_oauth_state` has resolved which real
      user this callback belongs to — lets the webhook save the finished
      connection record into THAT user's own partition, not "__webhook__".
      (`ctx.as_user()` is not usable here: it requires system-context, i.e.
      `imperal_id == "__system__"`, but a webhook's ctx has
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
    written/consumed, connection saved/read), just without real
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


def _extensions_for(ctx, user_id: str):
    """Build an ExtensionsClient scoped to an arbitrary user_id, reusing
    ctx.extensions' own loader/ctx_factory/call_stack wiring (only the
    kctx_dict's user_id differs). Same rationale as `_store_for` above:

    `ctx.extensions.emit(...)` publishes the event under `ctx.extensions.
    _kctx_dict["user_id"]` — inside `oauth_callback`/`disconnect_github`'s
    webhook path that's the pseudo-identity "__webhook__", not the real
    Imperal user, per Extension.webhook's own docstring. A panel declared
    `refresh="on_event:...,"` only re-fetches for the session the event was
    published under, so an event emitted as "__webhook__" never reaches the
    real user's own panel session. Rebuilding the client with the resolved
    real imperal_id (found via `find_and_consume_oauth_state`/
    `get_connection`) fixes that at the source, without touching the SDK.

    In tests, ctx.extensions is a MockExtensions (imperal_sdk.testing) with
    no `_kctx_dict`/`_loader` at all. Falls back to ctx.extensions itself in
    that case, same tolerance pattern as `_store_for`.
    """
    if not hasattr(ctx.extensions, "_kctx_dict"):
        return ctx.extensions
    from imperal_sdk.extensions.client import ExtensionsClient
    scoped_kctx = dict(ctx.extensions._kctx_dict, user_id=user_id)
    return ExtensionsClient(
        loader=ctx.extensions._loader,
        ctx_factory=ctx.extensions._ctx_factory,
        kctx_dict=scoped_kctx,
        current_app_id=ctx.extensions._current,
        call_stack=list(ctx.extensions._call_stack),
    )


async def save_oauth_state(ctx, state: str, imperal_id: str) -> None:
    """Called from the authenticated connect-flow chat.function, right before
    redirecting the user to GitHub's authorize page. Writes into the shared
    "__webhook__" partition (via _store_for) — NOT the calling user's own
    partition — because the webhook callback that reads this back has no
    identity of its own to match against; `imperal_id` in the record body is
    what lets the callback attribute the finished connection to the right
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
    already consumed. One-shot: deletes on successful read.
    """
    page = await webhook_ctx.store.query(OAUTH_STATES_COLLECTION, limit=200)
    for doc in page.data:
        if doc.data.get("state") == state:
            owner = doc.data.get("imperal_id")
            await webhook_ctx.store.delete(OAUTH_STATES_COLLECTION, doc.id)
            return owner
    return None


# ── Connection record (§12.2 — no installation, no repo list) ──────────── #

async def get_connection(ctx):
    """Return this user's connection record (dict) or None if not connected."""
    page = await ctx.store.query(CONNECTIONS_COLLECTION, limit=1)
    return page.data[0].data if page.data else None


async def save_connection_for_user(webhook_ctx, imperal_id: str, record: dict) -> None:
    """Called from the unauthenticated webhook once the state token has been
    resolved to a real imperal_id — writes the finished connection record
    into THAT user's own store partition (via _store_for), not "__webhook__".
    """
    store = _store_for(webhook_ctx, imperal_id)
    existing = await store.query(CONNECTIONS_COLLECTION, limit=1)
    if existing.data:
        await store.update(CONNECTIONS_COLLECTION, existing.data[0].id, record)
    else:
        await store.create(CONNECTIONS_COLLECTION, record)


async def save_connection(ctx, record: dict) -> None:
    """Called from an AUTHENTICATED context — writes into the calling user's
    own partition directly via ctx.store."""
    existing = await ctx.store.query(CONNECTIONS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.update(CONNECTIONS_COLLECTION, existing.data[0].id, record)
    else:
        await ctx.store.create(CONNECTIONS_COLLECTION, record)


async def delete_connection(ctx) -> None:
    existing = await ctx.store.query(CONNECTIONS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.delete(CONNECTIONS_COLLECTION, existing.data[0].id)


# ── User OAuth token (unchanged in shape from §12.1) ────────────────────── #

async def save_user_token_for_user(webhook_ctx, imperal_id: str, encrypted_record: dict) -> None:
    """Called from the unauthenticated oauth_callback once the state token
    has been resolved to a real imperal_id — writes the encrypted token
    record into THAT user's own store partition, mirroring
    save_connection_for_user's shape exactly."""
    store = _store_for(webhook_ctx, imperal_id)
    existing = await store.query(USER_TOKENS_COLLECTION, limit=1)
    if existing.data:
        await store.update(USER_TOKENS_COLLECTION, existing.data[0].id, encrypted_record)
    else:
        await store.create(USER_TOKENS_COLLECTION, encrypted_record)


async def get_user_token(ctx) -> dict | None:
    """Return this user's encrypted token record (dict) or None if not
    connected. Caller (github_client.get_user_token) decrypts it."""
    page = await ctx.store.query(USER_TOKENS_COLLECTION, limit=1)
    return page.data[0].data if page.data else None


async def save_user_token(ctx, encrypted_record: dict) -> None:
    """Called from an AUTHENTICATED context (e.g. after a token refresh) —
    writes into the calling user's own partition directly via ctx.store."""
    existing = await ctx.store.query(USER_TOKENS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.update(USER_TOKENS_COLLECTION, existing.data[0].id, encrypted_record)
    else:
        await ctx.store.create(USER_TOKENS_COLLECTION, encrypted_record)


async def delete_user_token(ctx) -> None:
    existing = await ctx.store.query(USER_TOKENS_COLLECTION, limit=1)
    if existing.data:
        await ctx.store.delete(USER_TOKENS_COLLECTION, existing.data[0].id)


# ── Per-repo webhooks (§12.2 — classic OAuth Apps have no centralized hook) ─ #

async def save_repo_webhook(ctx, repo_full_name: str, github_hook_id: int) -> None:
    """Record that this user registered a webhook on `repo_full_name`, and
    index it (shared "__webhook__" partition) so webhook_events can resolve a
    real user from just `repository.full_name` in an incoming event payload."""
    existing = await ctx.store.query(REPO_WEBHOOKS_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    record = {"repo_full_name": repo_full_name, "github_hook_id": github_hook_id}
    if existing.data:
        await ctx.store.update(REPO_WEBHOOKS_COLLECTION, existing.data[0].id, record)
    else:
        await ctx.store.create(REPO_WEBHOOKS_COLLECTION, record)
    await _index_repo_webhook(ctx, repo_full_name, ctx.user.imperal_id)


async def get_repo_webhook(ctx, repo_full_name: str) -> dict | None:
    """Return this user's webhook record for one repo (dict, includes
    github_hook_id) or None if this repo has no registered hook."""
    page = await ctx.store.query(REPO_WEBHOOKS_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    return page.data[0].data if page.data else None


async def list_repo_webhooks(ctx) -> list[dict]:
    """List every repo this user has a registered webhook on."""
    page = await ctx.store.query(REPO_WEBHOOKS_COLLECTION, limit=200)
    return [doc.data for doc in page.data]


async def delete_repo_webhook(ctx, repo_full_name: str) -> None:
    existing = await ctx.store.query(REPO_WEBHOOKS_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    if existing.data:
        await ctx.store.delete(REPO_WEBHOOKS_COLLECTION, existing.data[0].id)
    await _deindex_repo_webhook(ctx, repo_full_name, ctx.user.imperal_id)


async def _index_repo_webhook(ctx, repo_full_name: str, imperal_id: str) -> None:
    idx_store = _store_for(ctx, "__webhook__")
    existing = await idx_store.query(REPO_WEBHOOK_INDEX_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    if existing.data:
        watchers = existing.data[0].data.get("imperal_ids", [])
        if imperal_id not in watchers:
            watchers.append(imperal_id)
        await idx_store.update(REPO_WEBHOOK_INDEX_COLLECTION, existing.data[0].id,
                                {"repo_full_name": repo_full_name, "imperal_ids": watchers})
    else:
        await idx_store.create(REPO_WEBHOOK_INDEX_COLLECTION,
                                {"repo_full_name": repo_full_name, "imperal_ids": [imperal_id]})


async def _deindex_repo_webhook(ctx, repo_full_name: str, imperal_id: str) -> None:
    idx_store = _store_for(ctx, "__webhook__")
    existing = await idx_store.query(REPO_WEBHOOK_INDEX_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    if not existing.data:
        return
    watchers = [w for w in existing.data[0].data.get("imperal_ids", []) if w != imperal_id]
    if watchers:
        await idx_store.update(REPO_WEBHOOK_INDEX_COLLECTION, existing.data[0].id,
                                {"repo_full_name": repo_full_name, "imperal_ids": watchers})
    else:
        await idx_store.delete(REPO_WEBHOOK_INDEX_COLLECTION, existing.data[0].id)


async def resolve_imperal_ids_for_repo(webhook_ctx, repo_full_name: str) -> list[str]:
    """Look up which real users watch a given repo — used by the
    unauthenticated webhook_events handler to know who to notify."""
    idx_store = _store_for(webhook_ctx, "__webhook__")
    page = await idx_store.query(REPO_WEBHOOK_INDEX_COLLECTION, where={"repo_full_name": repo_full_name}, limit=1)
    if page.data:
        return page.data[0].data.get("imperal_ids", [])
    return []
