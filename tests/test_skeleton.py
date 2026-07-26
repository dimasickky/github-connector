"""Tests for the repos_overview skeleton section.

Two things are asserted here, and the second matters as much as the first.

1. BEHAVIOUR: the section degrades in layers and never raises. A skeleton
   refresh that throws is dropped from the ambient snapshot entirely, so
   "not connected", "token gone" and "GitHub erroring" must each still
   return a usable payload.

2. RENDER CONTRACT: the kernel projects this payload into the classifier
   envelope through hard caps (imperal_kernel/hub/classifier/skeleton_summary
   .py) — nested dict/list values inside a list item are SKIPPED, only the
   first 6 scalar fields of an item render, each item is cut at ~110 chars,
   and a list of plain scalars collapses to `list[N]`. Violating those is not
   a loud failure: the data silently never reaches the model. That is exactly
   the bug this extension's sibling had, so the shape is asserted, not trusted.
"""
import pytest
from imperal_sdk.testing import MockContext

import skeleton
from tests.conftest import seed_connection, seed_user_token

_REPO_ROWS = [
    {"id": 1, "full_name": "dimasickky/telegram-publisher-extension", "private": False,
     "language": "Python", "stargazers_count": 4, "default_branch": "main"},
    {"id": 2, "full_name": "dimasickky/imperal-notes", "private": True,
     "language": "Python", "stargazers_count": 0, "default_branch": "main"},
]


@pytest.mark.asyncio
async def test_not_connected_returns_flags_only():
    ctx = MockContext()
    snap = (await skeleton.repos_overview(ctx))["response"]
    assert snap == {"connected": False, "repo_count": 0, "repos": []}


@pytest.mark.asyncio
async def test_connected_surfaces_recent_repos():
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", _REPO_ROWS)

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["connected"] is True
    assert snap["login"] == "octocat"
    assert snap["repo_count"] == 2
    assert snap["repos"][0]["title"] == "dimasickky/telegram-publisher-extension"
    assert snap["repos"][1]["private"] is True
    assert snap["repos"][0]["lang"] == "Python"


@pytest.mark.asyncio
async def test_github_error_degrades_to_connected_without_repos():
    """A 500 from GitHub must not blank the connection state."""
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", {"message": "boom"}, status=500)

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["connected"] is True
    assert snap["login"] == "octocat"
    assert snap["repos"] == []


@pytest.mark.asyncio
async def test_connected_without_token_says_so():
    """Connection record present but no token — reconnect hint, not "0 repos"."""
    ctx = MockContext()
    await seed_connection(ctx)

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["connected"] is True
    assert snap["repos"] == []
    assert "note" in snap


@pytest.mark.asyncio
async def test_shape_survives_classifier_budgets():
    """Guard the renderer rules this payload is shaped around."""
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", _REPO_ROWS)

    snap = (await skeleton.repos_overview(ctx))["response"]
    entry = snap["repos"][0]

    # 1. Every value inside a list item is a scalar — a nested dict/list would
    #    be skipped by the renderer and never reach the model.
    for key, value in entry.items():
        assert not isinstance(value, (dict, list)), f"{key} would be dropped"

    # 2. The item stays inside the render window. `title` is consumed as the
    #    item label, so the 6-field budget applies to what remains.
    payload_fields = [k for k in entry if k != "title"]
    assert len(payload_fields) <= 6, f"fields past the 6th never render: {payload_fields}"

    # 3. Rendered length per item stays under the ~110-char item cap.
    rendered = " ".join(f"{k}={v}" for k, v in entry.items())
    assert len(rendered) <= 110, f"item would be truncated: {len(rendered)} chars"

    # 4. The sample is bounded, so the whole list stays inside ~700 chars.
    assert len(snap["repos"]) <= skeleton._SKELETON_REPOS


@pytest.mark.asyncio
async def test_alert_fires_only_on_connection_transitions():
    ctx = MockContext()

    connected = {"connected": True, "login": "octocat"}
    disconnected = {"connected": False}

    gained = await skeleton.skeleton_alert_repos_overview(ctx, disconnected, connected)
    assert "connected" in gained["response"].lower()
    assert "octocat" in gained["response"]

    lost = await skeleton.skeleton_alert_repos_overview(ctx, connected, disconnected)
    assert "disconnected" in lost["response"].lower()

    # A star count moving is not news.
    quiet = await skeleton.skeleton_alert_repos_overview(
        ctx,
        {"connected": True, "login": "octocat", "repos": [{"title": "a", "stars": 1}]},
        {"connected": True, "login": "octocat", "repos": [{"title": "a", "stars": 2}]},
    )
    assert quiet["response"] == ""

    # Missing either side is a no-op, not a crash.
    assert (await skeleton.skeleton_alert_repos_overview(ctx, None, connected))["response"] == ""


# ── Regressions found by rendering a REAL payload, not by these mocks ──────── #
#
# The first version of this section passed every test above while still being
# wrong in two ways, because the fixtures used two short repo names. Feeding it
# ten long ones through the kernel's own renderer exposed both. Hence these.

def _many_repos(n: int, owner: str = "octocat", prefix: str = "some-fairly-long-extension-name"):
    return [
        {"id": i, "name": f"{prefix}-{i}", "full_name": f"{owner}/{prefix}-{i}",
         "owner": {"login": owner}, "private": i % 2 == 0, "language": "Python",
         "stargazers_count": i, "default_branch": "main"}
        for i in range(1, n + 1)
    ]


@pytest.mark.asyncio
async def test_repo_count_is_the_total_not_the_sample_size():
    """repo_count must describe the ACCOUNT, not the slice we chose to show.

    Originally `per_page` was set to the sample size and the count was
    `len(repos)`, so a user with 10 repos was told they had 6 — stated with
    full confidence. The sample is a sample; the count is a fact.
    """
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", _many_repos(10))

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["repo_count"] == 10
    assert len(snap["repos"]) == skeleton._SKELETON_REPOS
    # A short page is NOT capped, so the flag stays absent rather than false.
    assert "repo_count_capped" not in snap


@pytest.mark.asyncio
async def test_capped_flag_appears_only_when_the_page_fills():
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", _many_repos(skeleton._REPO_FETCH))

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["repo_count"] == skeleton._REPO_FETCH
    assert snap["repo_count_capped"] is True


@pytest.mark.asyncio
async def test_long_names_stay_distinguishable():
    """Truncation must not collapse different repos into one string.

    With `owner/name` labels and a 40-char cap, six long names all cut to the
    identical "octocat/some-fairly-long-extension-na" — which reads as six
    duplicate rows, not six truncated ones.
    """
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", _many_repos(6))

    snap = (await skeleton.repos_overview(ctx))["response"]
    titles = [r["title"] for r in snap["repos"]]

    assert len(set(titles)) == len(titles), f"indistinguishable after truncation: {titles}"
    # The connected user's own login is redundant on every row — `login` is
    # already a top-level field — so it is not repeated inside items.
    assert not any(t.startswith("octocat/") for t in titles)


@pytest.mark.asyncio
async def test_foreign_owner_keeps_its_prefix():
    """The owner prefix is dropped only when it is the connected user's own."""
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", [
        {"id": 1, "name": "infra", "full_name": "acme-corp/infra",
         "owner": {"login": "acme-corp"}, "private": True, "language": "Go",
         "stargazers_count": 7, "default_branch": "trunk"},
        {"id": 2, "name": "notes", "full_name": "octocat/notes",
         "owner": {"login": "octocat"}, "private": False, "language": "Python",
         "stargazers_count": 1, "default_branch": "main"},
    ])

    titles = [r["title"] for r in (await skeleton.repos_overview(ctx))["response"]["repos"]]

    assert "acme-corp/infra" in titles   # org repo: prefix carries information
    assert "notes" in titles             # own repo: prefix is noise


@pytest.mark.asyncio
async def test_truncation_is_marked_not_silent():
    """A cut label ends in an ellipsis so the model knows the tail is missing."""
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", [
        {"id": 1, "name": "x" * 120, "full_name": f"octocat/{'x' * 120}",
         "owner": {"login": "octocat"}, "private": False, "language": "Python",
         "stargazers_count": 0, "default_branch": "main"},
    ])

    title = (await skeleton.repos_overview(ctx))["response"]["repos"][0]["title"]

    assert len(title) <= skeleton._NAME_CHARS
    assert title.endswith("…")


@pytest.mark.asyncio
async def test_malformed_rows_are_skipped_not_fatal():
    """GitHub returning junk in the list must not drop the whole section."""
    ctx = MockContext()
    await seed_connection(ctx)
    await seed_user_token(ctx)
    ctx.http.mock_get("/user/repos", ["not-a-dict", None, 42,
                                      {"id": 9, "name": "real", "full_name": "octocat/real",
                                       "owner": {"login": "octocat"}}])

    snap = (await skeleton.repos_overview(ctx))["response"]

    assert snap["connected"] is True
    assert [r["title"] for r in snap["repos"]] == ["real"]
    # Junk rows are not counted as repos either.
    assert snap["repo_count"] == 1
