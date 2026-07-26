"""github-connector · Skeleton tools — ambient repo context.

WHY THIS SECTION EXISTS
-----------------------
Until now this extension shipped no skeleton at all: the assistant had zero
ambient signal about GitHub, so even "what am I working on?" required an
explicit tool call, and a question like "any open PRs?" could not be answered
from context. Every other connector of ours surfaces its state; this one was
blind.

WHAT GOES IN, AND WHAT THE KERNEL DOES WITH IT
----------------------------------------------
The section is refreshed on a timer for EVERY user, whether or not the
conversation is about GitHub, and the result is projected into the classifier
envelope by imperal_kernel/hub/classifier/skeleton_summary.py. That projection
imposes hard, silent limits, and the shape below is built around them:

* a list of DICTS expands to its item fields; a list of SCALARS collapses to
  ``list[N]`` — so names in a plain string list never reach the model;
* inside a list item, nested dict/list values are SKIPPED outright — a list
  nested in a repo entry does not get truncated, it VANISHES;
* an item renders a label key (``title``/``name``) plus an id key (``id``) as
  its header, then at most 6 more scalar fields, and is cut at ~110 chars —
  and a long FIELD NAME spends that budget exactly like a value does;
* the whole list value is capped at ~700 chars, a bare string at 60.

Hence: short field names, scalars only inside items, and a small sample.

NETWORK POLICY
--------------
One bounded GET, guarded by asyncio.wait_for and a broad except, mirroring how
the platform's own mail skeleton calls its providers (per-call 5-10s timeouts
inside the kernel's 120s activity budget). A repo list is a single cheap,
cacheable request, and without it there is nothing to show — the store only
holds the connection record and webhook rows, not repos.

The guards matter more than the call: this runs on a hot loop for every user,
so a slow or failing GitHub must degrade to "connected, no data" rather than
poison the ambient snapshot or stall the tick.
"""
from __future__ import annotations

import asyncio
import logging

import github_client
import storage
from app import ext

log = logging.getLogger("github-connector")

# Sample sizes are chosen to ARRIVE INTACT rather than to look generous:
# 6 repos x ~100 rendered chars stays inside the ~700-char list budget.
_SKELETON_REPOS = 6
_NAME_CHARS = 40

# How many rows to ASK GitHub for, which is a different number from how many we
# SHOW. These were the same value at first, and that quietly made repo_count a
# lie: with per_page=6 the API can only ever return 6, so a user with 40 repos
# still saw repo_count=6. One page of 100 is the same single request (it is what
# list_repositories already asks for), so the true count costs nothing extra.
_REPO_FETCH = 100

# Per-call network budget. Deliberately tighter than the kernel's 120s activity
# timeout so a hanging GitHub costs one stale section, never the whole tick.
_HTTP_TIMEOUT = 8.0


def _short(text: str, cap: int = _NAME_CHARS) -> str:
    """Collapse to a single capped line, marking a cut when one happens.

    The classifier envelope is one line per section, so an embedded newline
    would split a value across lines; whitespace is collapsed before the cap
    so the budget is not spent on invisible padding.

    The ellipsis is not decoration. A silent hard cut turns several long names
    into the SAME string — "dimasickky/some-fairly-long-extension-na" six times
    over — which reads as duplicate entries rather than truncated ones. One
    character tells the model the tail is missing.
    """
    flat = " ".join((text or "").split())
    return flat if len(flat) <= cap else flat[:cap - 1] + "…"


def _repo_label(row: dict, login: str) -> str:
    """Name a repo the way a human would in conversation.

    `full_name` is owner/repo, but the owner is the connected user for most of
    their repos — and `login` is already a field on this section, so repeating
    it on every item spends the 110-char item budget on a constant. Worse, with
    a long owner prefix the cap lands inside the shared prefix and every repo
    renders identically.

    So: bare repo name for the user's own repos, owner/name only when the owner
    actually differs (an org or a collaboration), which is exactly when that
    prefix carries information.
    """
    full = row.get("full_name") or ""
    owner = (row.get("owner") or {}).get("login") or (full.split("/")[0] if "/" in full else "")
    name = row.get("name") or (full.split("/")[-1] if full else "")
    if owner and login and owner.lower() != login.lower():
        return _short(f"{owner}/{name}")
    return _short(name or full)


@ext.skeleton(
    "repos_overview",
    alert=True,
    # 120s. The repo list moves on GitHub's side, not ours — a push or a new PR
    # is not something our own tools caused — so there is no write for a tick
    # to catch up with, and the SDK's SkeletonClient is read-only anyway (no
    # ctx.skeleton.invalidate() for handlers). Since this section is the only
    # one here that costs a network call, it refreshes on the slower cadence:
    # the platform derives its tick from the MINIMUM ttl across a user's
    # sections (floor 15s), so a smaller value here would add GitHub traffic to
    # every other extension's refresh too. Live events already arrive out of
    # band via the opt-in repo webhooks.
    ttl=120,
    description=(
        "Connected GitHub account — login, and the most recently updated repositories "
        "(name, private flag, language, stars, default branch) plus how many repos have "
        "live notifications enabled. Ambient context so the assistant knows what the "
        "user is working on without an explicit lookup."
    ),
)
async def repos_overview(ctx) -> dict:
    """Ambient context: connected account + most recently updated repos.

    Degrades in layers, never raises: not connected -> flags only; GitHub slow
    or erroring -> connected plus whatever the store knows. A skeleton refresh
    that throws would drop this section from the snapshot entirely, so every
    failure path returns a usable payload instead.
    """
    try:
        conn = await storage.get_connection(ctx)
        if not conn:
            return {"response": {"connected": False, "repo_count": 0, "repos": []}}

        # The connect flow stores this as `account_login` (auth.py, after the
        # /user lookup). Checked against the writer rather than guessed: a
        # wrong key here fails silently as an empty string, which is exactly
        # the kind of quiet blindness this section is meant to remove.
        login = conn.get("account_login") or ""

        # Cheap local read — how many repos this user wired up for live events.
        try:
            watched = len(await storage.list_repo_webhooks(ctx))
        except Exception:
            watched = 0

        token, _err = await github_client.get_user_token(ctx)
        if not token:
            # Connected record exists but the token is gone or unreadable:
            # say so plainly rather than implying zero repos.
            return {"response": {
                "connected": True, "login": _short(login),
                "repo_count": 0, "repos": [], "watched_repos": watched,
                "note": "token unavailable — reconnect may be needed",
            }}

        try:
            resp = await asyncio.wait_for(
                github_client.gh_get(ctx, token, "/user/repos", {
                    "per_page": _REPO_FETCH,
                    "affiliation": "owner,collaborator,organization_member",
                    "sort": "updated",
                }),
                timeout=_HTTP_TIMEOUT,
            )
        except Exception as exc:
            # Broad on purpose, and sufficient: since 3.11 asyncio.TimeoutError
            # IS the builtin TimeoutError, so a timeout is already caught here —
            # no need for the (asyncio.TimeoutError, Exception) pair. Nothing
            # from this call may propagate: a raising refresh drops the whole
            # section from the ambient snapshot.
            log.warning("skeleton repo fetch failed: %s", exc)
            return {"response": {
                "connected": True, "login": _short(login),
                "repo_count": 0, "repos": [], "watched_repos": watched,
            }}

        if getattr(resp, "status_code", 500) != 200:
            return {"response": {
                "connected": True, "login": _short(login),
                "repo_count": 0, "repos": [], "watched_repos": watched,
            }}

        rows = resp.json() if callable(getattr(resp, "json", None)) else []
        if not isinstance(rows, list):
            rows = []
        rows = [r for r in rows if isinstance(r, dict)]

        repos = []
        for r in rows[:_SKELETON_REPOS]:
            # SCALARS ONLY, SHORT NAMES, cheapest fields first. `title` and `id`
            # are consumed as the item header by the renderer and are therefore
            # free; everything after them competes for the 6-field window.
            repos.append({
                "title": _repo_label(r, login),
                "private": bool(r.get("private", False)),
                "lang": _short(r.get("language") or "", 16),
                "stars": int(r.get("stargazers_count", 0) or 0),
                "branch": _short(r.get("default_branch") or "", 16),
            })

        return {"response": {
            "connected": True,
            "login": _short(login),
            # The TOTAL, not len(repos). `repos` is a 6-item sample of the most
            # recently updated; reporting its length as the count would tell the
            # model a user with 40 repos has 6, and it would sound authoritative
            # doing it. Capped at one page, hence the "+" marker when it fills.
            "repo_count": len(rows),
            "watched_repos": watched,
            "repos": repos,
            # Emitted ONLY when true. A `repo_count_capped: false` riding along
            # on every tick pays bytes on a per-user hot loop to say nothing.
            # The flag exists to stop the model stating "you have exactly 100
            # repos" when 100 really means "one page, possibly more".
            **({"repo_count_capped": True} if len(rows) >= _REPO_FETCH else {}),
        }}
    except Exception as exc:
        # Last-resort guard: a raising refresh drops the section from the
        # snapshot, so failure still returns a shape the classifier can read.
        log.error("skeleton refresh failed: %s", exc)
        return {"response": {"connected": False, "repo_count": 0, "repos": []}}


@ext.tool(
    "skeleton_alert_repos_overview",
    description="Alert on GitHub connected or disconnected.",
)
async def skeleton_alert_repos_overview(
    ctx,
    old: dict | None = None,
    new: dict | None = None,
) -> dict:
    """Called by platform when repos_overview snapshot changes between ticks.

    Deliberately narrow. This is evaluated on every tick, so anything chatty
    becomes ambient noise: a star count ticking up or a repo changing position
    in the "recently updated" sample is not news. Only the connection
    transition is, because it is the one thing that silently breaks every
    GitHub tool until the user acts.
    """
    if not old or not new:
        return {"response": ""}

    was, now = bool(old.get("connected")), bool(new.get("connected"))
    if was and not now:
        return {"response": "GitHub disconnected — reconnect to restore repo access"}
    if now and not was:
        login = new.get("login") or ""
        return {"response": f"GitHub connected{f' as {login}' if login else ''}"}
    return {"response": ""}
