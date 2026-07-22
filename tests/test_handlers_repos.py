"""Tests for P2 read-only repo browsing tools (handlers_repos.py).

Each test seeds a valid user-to-server OAuth token via conftest's
seed_user_token (§12.1 pivot — no installation/JWT mint mock needed any
more, the token IS the credential), mocks the GitHub REST GET the tool
under test makes, then asserts the reshaped ActionResult.
"""
import pytest

from imperal_sdk.testing import MockContext

import handlers_repos
from tests.conftest import seed_user_token
from models import _NoParams, RepoParams, FileContentsParams, ListCommitsParams, SearchCodeParams, ListReleasesParams, CreateRepositoryParams


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await seed_user_token(ctx)
    return ctx


@pytest.mark.asyncio
async def test_list_repositories_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_repos.list_repositories(ctx, _NoParams())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_list_repositories_success():
    ctx = await _seeded_ctx()
    # More specific pattern registered FIRST — MockHTTP._find matches by
    # substring in registration order, and "/user/installations" is itself
    # a substring of the repositories URL below, so order matters here.
    ctx.http.mock_get("/user/installations/12345/repositories", {
        "repositories": [
            {"id": 1, "name": "hello-world", "full_name": "octocat/hello-world",
             "private": False, "default_branch": "main", "stargazers_count": 42,
             "language": "Python", "html_url": "https://github.com/octocat/hello-world"},
        ],
    })
    ctx.http.mock_get("/user/installations", {
        "installations": [{"id": 12345}],
    })
    result = await handlers_repos.list_repositories(ctx, _NoParams())
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].full_name == "octocat/hello-world"
    assert result.data.items[0].stars == 42


@pytest.mark.asyncio
async def test_get_file_contents_file():
    import base64
    ctx = await _seeded_ctx()
    content_b64 = base64.b64encode(b"print('hi')").decode()
    ctx.http.mock_get("/contents/", {
        "type": "file", "path": "app.py", "name": "app.py",
        "content": content_b64, "encoding": "base64", "size": 11,
        "sha": "abc123", "html_url": "https://github.com/octocat/hello-world/blob/main/app.py",
    })
    result = await handlers_repos.get_file_contents(
        ctx, FileContentsParams(repo="octocat/hello-world", path="app.py"))
    assert result.status == "success"
    assert result.data.content == "print('hi')"
    assert result.data.sha == "abc123"
    # A file must render as a proper syntax-highlighted code block in chat,
    # not the raw FileContent dict.
    assert result.ui is not None
    assert result.ui.to_dict()["type"] == "Code"
    assert result.ui.to_dict()["props"]["language"] == "python"


@pytest.mark.asyncio
async def test_get_file_contents_directory():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/contents/", [
        {"name": "app.py"}, {"name": "README.md"},
    ])
    result = await handlers_repos.get_file_contents(
        ctx, FileContentsParams(repo="octocat/hello-world", path=""))
    assert result.status == "success"
    assert result.data.kind == "gh_dir"
    # A directory must render as a browsable list in chat, not raw JSON.
    assert result.ui is not None
    assert result.ui.to_dict()["type"] == "List"


@pytest.mark.asyncio
async def test_get_file_contents_bad_repo_format():
    ctx = await _seeded_ctx()
    result = await handlers_repos.get_file_contents(
        ctx, FileContentsParams(repo="not-a-valid-repo", path=""))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_get_file_contents_not_found():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/contents/", {"error": "not found"}, status=404)
    result = await handlers_repos.get_file_contents(
        ctx, FileContentsParams(repo="octocat/hello-world", path="missing.py"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_list_recent_commits_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/commits", [
        {"sha": "deadbeef", "commit": {"message": "Initial commit\n\nBody here",
                                       "author": {"name": "The Octocat", "date": "2026-01-01T00:00:00Z"}},
         "author": {"login": "octocat"}, "html_url": "https://github.com/x/y/commit/deadbeef"},
    ])
    result = await handlers_repos.list_recent_commits(
        ctx, ListCommitsParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].sha == "deadbeef"
    assert result.data.items[0].title == "Initial commit"


@pytest.mark.asyncio
async def test_list_contributors_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/contributors", [
        {"id": 1, "login": "octocat", "contributions": 100,
         "avatar_url": "https://x/y.png", "html_url": "https://github.com/octocat"},
    ])
    result = await handlers_repos.list_contributors(
        ctx, RepoParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert result.data.items[0].login == "octocat"
    assert result.data.items[0].contributions == 100


@pytest.mark.asyncio
async def test_search_code_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/search/code", {
        "items": [
            {"sha": "abc123", "name": "app.py", "path": "src/app.py", "score": 1.0,
             "repository": {"full_name": "octocat/hello-world"},
             "html_url": "https://github.com/octocat/hello-world/blob/main/src/app.py"},
        ],
    })
    result = await handlers_repos.search_code(
        ctx, SearchCodeParams(repo="octocat/hello-world", query="TODO"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].path == "src/app.py"
    assert result.data.items[0].repository == "octocat/hello-world"


@pytest.mark.asyncio
async def test_search_code_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_repos.search_code(
        ctx, SearchCodeParams(repo="octocat/hello-world", query="TODO"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_list_releases_success():
    ctx = await _seeded_ctx()
    ctx.http.mock_get("/releases", [
        {"id": 1, "tag_name": "v1.0.0", "name": "First release", "draft": False,
         "prerelease": False, "published_at": "2026-01-01T00:00:00Z",
         "body": "Initial release notes", "html_url": "https://github.com/octocat/hello-world/releases/tag/v1.0.0"},
    ])
    result = await handlers_repos.list_releases(
        ctx, ListReleasesParams(repo="octocat/hello-world"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].tag_name == "v1.0.0"
    assert result.data.items[0].title == "First release"


@pytest.mark.asyncio
async def test_list_releases_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_repos.list_releases(
        ctx, ListReleasesParams(repo="octocat/hello-world"))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_create_repository_personal_account():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/user/repos", {
        "id": 999, "name": "my-new-project", "full_name": "octocat/my-new-project",
        "private": True, "default_branch": "main", "stargazers_count": 0,
        "language": None, "html_url": "https://github.com/octocat/my-new-project",
    })
    result = await handlers_repos.create_repository(
        ctx, CreateRepositoryParams(name="my-new-project"))
    assert result.status == "success"
    assert result.data.full_name == "octocat/my-new-project"


@pytest.mark.asyncio
async def test_create_repository_org():
    ctx = await _seeded_ctx()
    ctx.http.mock_post("/orgs/acme/repos", {
        "id": 1000, "name": "team-repo", "full_name": "acme/team-repo",
        "private": False, "default_branch": "main", "stargazers_count": 0,
        "language": None, "html_url": "https://github.com/acme/team-repo",
    })
    result = await handlers_repos.create_repository(
        ctx, CreateRepositoryParams(name="team-repo", org="acme", private=False))
    assert result.status == "success"
    assert result.data.full_name == "acme/team-repo"


@pytest.mark.asyncio
async def test_create_repository_not_connected_errors():
    ctx = MockContext(user_id="user-2")
    result = await handlers_repos.create_repository(
        ctx, CreateRepositoryParams(name="x"))
    assert result.status == "error"
