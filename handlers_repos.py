"""github-connector · P2 read-only repository browsing tools.

list_repositories, get_file_contents, list_recent_commits, list_contributors —
all thin wrappers: resolve this user's OAuth token (refreshing if needed) ->
call the matching GitHub REST endpoint via github_client.gh_get -> reshape
into an sdl.Entity/EntityList result.

Every tool starts the same way (token resolution), so that shared prelude
lives in `_get_token(ctx)` to avoid repeating it in every handler.
"""
import base64

from imperal_sdk import ActionResult, sdl, ui
from app import chat
from error_codes import GH_NOT_CONNECTED, GH_REPO_NOT_ACCESSIBLE, GH_FILE_NOT_FOUND
from imperal_sdk.chat.error_codes import INTERNAL
from models import (
    _NoParams, RepoParams, FileContentsParams, ListCommitsParams,
    SearchCodeParams, ListReleasesParams, CreateRepositoryParams,
    Repository, FileEntry, FileContent, Commit, Contributor,
    CodeSearchResult, Release,
)
import github_client


async def _get_token(ctx):
    """Resolve this user's user-to-server OAuth token (refreshing it first if
    it's close to expiry). Returns (token, error_result) — exactly one of the
    two is not-None, mirroring the (data, error) tuple shape github_client.py
    already uses."""
    token, err = await github_client.get_user_token(ctx)
    if err:
        return None, ActionResult.error(err, retryable=False, code=GH_NOT_CONNECTED)
    return token, None


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    return owner, name


@chat.function(
    "list_repositories",
    description="List the GitHub repositories this account's App installation has access to.",
    action_type="read",
    data_model=sdl.EntityList[Repository],
)
async def list_repositories(ctx, params: _NoParams) -> ActionResult:
    """List repositories this user's token can see (intersection of what they
    picked at GitHub App install time and what they personally have access
    to — GitHub's own scoping rule for user-to-server tokens)."""
    token, err = await _get_token(ctx)
    if err:
        return err
    resp = await github_client.gh_get(ctx, token, "/user/installations", {"per_page": 100})
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    installations = resp.json().get("installations", [])
    repos: list[dict] = []
    for inst in installations:
        inst_id = inst.get("id")
        repo_resp = await github_client.gh_get(
            ctx, token, f"/user/installations/{inst_id}/repositories", {"per_page": 100},
        )
        if repo_resp.status_code == 200:
            repos.extend(repo_resp.json().get("repositories", []))
    items = [
        Repository(
            id=str(r["id"]), title=r.get("name", ""), kind="gh_repo",
            full_name=r.get("full_name", ""), private=r.get("private", False),
            default_branch=r.get("default_branch", "main"),
            stars=r.get("stargazers_count", 0), language=r.get("language") or "",
            url=r.get("html_url", ""),
        )
        for r in repos
    ]
    return ActionResult.success(sdl.EntityList[Repository](items=items), summary=f"{len(items)} repositor{'y' if len(items)==1 else 'ies'}")


@chat.function(
    "create_repository",
    description=(
        "Create a new GitHub repository — in your personal account, or inside "
        "an organization you belong to (pass org=). Only possible now that "
        "we act as your real GitHub user (§12.1) — a plain App-bot identity "
        "cannot create personal-account repositories, GitHub blocks that."
    ),
    action_type="write",
    data_model=Repository,
    effects=["github.create_repository"],
    event="github-connector-extension.create_repository",
)
async def create_repository(ctx, params: CreateRepositoryParams) -> ActionResult:
    """POST /user/repos (personal account) or POST /orgs/{org}/repos (org) —
    the one operation that genuinely requires a user-to-server token; an
    installation token gets a 403 on /user/repos no matter its permissions."""
    token, err = await _get_token(ctx)
    if err:
        return err

    body = {
        "name": params.name,
        "private": params.private,
        "auto_init": params.auto_init,
    }
    if params.description:
        body["description"] = params.description

    path = f"/orgs/{params.org}/repos" if params.org else "/user/repos"
    resp = await github_client.gh_post(ctx, token, path, json_body=body)
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)

    r = resp.json()
    repo = Repository(
        id=str(r["id"]), title=r.get("name", ""), kind="gh_repo",
        full_name=r.get("full_name", ""), private=r.get("private", False),
        default_branch=r.get("default_branch", "main"),
        stars=r.get("stargazers_count", 0), language=r.get("language") or "",
        url=r.get("html_url", ""),
    )
    return ActionResult.success(repo, summary=f"Created repository {repo.full_name}")


@chat.function(
    "get_file_contents",
    description=(
        "Read a file's content, or list a directory's entries, from a GitHub "
        "repository at a given ref (branch/tag/commit). Pass path='' for the repo root."
    ),
    action_type="read",
    data_model=FileContent,
)
async def get_file_contents(ctx, params: FileContentsParams) -> ActionResult:
    """Fetch /repos/{owner}/{repo}/contents/{path} — a dict for a file (base64
    content decoded here), a list for a directory (returned as a FileEntry list)."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    if not owner or not name:
        return ActionResult.error("repo must be 'owner/repo'.", retryable=False, code=GH_REPO_NOT_ACCESSIBLE)
    q = {"ref": params.ref} if params.ref else {}
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/contents/{params.path}", q)
    if resp.status_code == 404:
        return ActionResult.error(github_client.gh_error_message(404), retryable=False, code=GH_FILE_NOT_FOUND)
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    data = resp.json()

    if isinstance(data, list):
        # Directory listing — return as an EntityList-shaped-into-one-Entity is
        # awkward for a single data_model; represent the directory itself as a
        # FileContent with content="" and let the caller re-query per entry.
        entries = ", ".join(e.get("name", "") for e in data[:50])
        result = FileContent(
            id=params.path or "/", title=params.path or "(root)", kind="gh_dir",
            path=params.path, ref=params.ref, content="", size=0,
            description=f"Directory with {len(data)} entr{'y' if len(data)==1 else 'ies'}: {entries}",
        )
        sorted_entries = sorted(data, key=lambda e: (e.get("type") != "dir", e.get("name", "")))
        listing_ui = ui.List(items=[
            ui.ListItem(
                id=e.get("path", e.get("name", "")),
                title=e.get("name", "") + ("/" if e.get("type") == "dir" else ""),
                icon="Folder" if e.get("type") == "dir" else "File",
            )
            for e in sorted_entries[:50]
        ])
        return ActionResult.success(
            result, summary=f"{len(data)} entries in {params.path or '/'}", ui=listing_ui,
        )

    if data.get("type") != "file":
        return ActionResult.error(
            f"{params.path} is not a regular file (type={data.get('type')}).",
            retryable=False, code=GH_FILE_NOT_FOUND,
        )
    raw = data.get("content", "")
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="replace") if data.get("encoding") == "base64" else raw
    except Exception:
        decoded = "(binary file — cannot display as text)"
    result = FileContent(
        id=data.get("path", params.path), title=data.get("name", params.path),
        kind="gh_file", path=data.get("path", params.path), ref=params.ref,
        content=decoded, size=data.get("size", 0), sha=data.get("sha", ""),
        url=data.get("html_url", ""),
    )
    code_ui = ui.Code(
        content=decoded,
        language=github_client.guess_language(data.get("name", params.path)),
        line_numbers=True,
    )
    return ActionResult.success(
        result, summary=f"{data.get('path', params.path)} ({data.get('size', 0)} bytes)", ui=code_ui,
    )


@chat.function(
    "list_recent_commits",
    description="List recent commits on a GitHub repository, optionally filtered to those touching a specific path.",
    action_type="read",
    data_model=sdl.EntityList[Commit],
)
async def list_recent_commits(ctx, params: ListCommitsParams) -> ActionResult:
    """GET /repos/{owner}/{repo}/commits."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    if not owner or not name:
        return ActionResult.error("repo must be 'owner/repo'.", retryable=False, code=GH_REPO_NOT_ACCESSIBLE)
    q = {"per_page": params.limit}
    if params.path:
        q["path"] = params.path
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/commits", q)
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    commits = resp.json()
    items = [
        Commit(
            id=c["sha"], title=(c.get("commit", {}).get("message", "") or "").splitlines()[0][:120],
            kind="gh_commit", sha=c["sha"],
            message=c.get("commit", {}).get("message", ""),
            author=(c.get("commit", {}).get("author", {}) or {}).get("name", "")
                   or (c.get("author") or {}).get("login", ""),
            date=(c.get("commit", {}).get("author", {}) or {}).get("date", ""),
            url=c.get("html_url", ""),
        )
        for c in commits
    ]
    return ActionResult.success(sdl.EntityList[Commit](items=items), summary=f"{len(items)} commit(s)")


@chat.function(
    "list_contributors",
    description="List contributors to a GitHub repository, ranked by number of commits.",
    action_type="read",
    data_model=sdl.EntityList[Contributor],
)
async def list_contributors(ctx, params: RepoParams) -> ActionResult:
    """GET /repos/{owner}/{repo}/contributors."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    if not owner or not name:
        return ActionResult.error("repo must be 'owner/repo'.", retryable=False, code=GH_REPO_NOT_ACCESSIBLE)
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/contributors", {"per_page": 100})
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    contributors = resp.json()
    items = [
        Contributor(
            id=str(c["id"]), title=c.get("login", ""), kind="gh_contributor",
            login=c.get("login", ""), contributions=c.get("contributions", 0),
            avatar_url=c.get("avatar_url", ""), url=c.get("html_url", ""),
        )
        for c in contributors
    ]
    return ActionResult.success(sdl.EntityList[Contributor](items=items), summary=f"{len(items)} contributor(s)")


@chat.function(
    "search_code",
    description=(
        "Search for code inside a GitHub repository. Accepts GitHub's own code-search "
        "syntax (e.g. 'TODO language:python', 'def parse_args path:src') — the repo "
        "qualifier is added automatically from the repo param."
    ),
    action_type="read",
    data_model=sdl.EntityList[CodeSearchResult],
)
async def search_code(ctx, params: SearchCodeParams) -> ActionResult:
    """GET /search/code — installation tokens can use GitHub's code search
    scoped to repos the installation itself can see, no extra permission
    beyond Contents:read needed."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    if not owner or not name:
        return ActionResult.error("repo must be 'owner/repo'.", retryable=False, code=GH_REPO_NOT_ACCESSIBLE)
    q = f"{params.query} repo:{owner}/{name}"
    resp = await github_client.gh_get(ctx, token, "/search/code", {"q": q, "per_page": params.limit})
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    results = resp.json().get("items", [])
    items = [
        CodeSearchResult(
            id=r.get("sha", r.get("path", "")), title=r.get("name", r.get("path", "")),
            kind="gh_code_result", path=r.get("path", ""),
            repository=(r.get("repository") or {}).get("full_name", params.repo),
            score=r.get("score", 0.0), url=r.get("html_url", ""),
        )
        for r in results
    ]
    return ActionResult.success(sdl.EntityList[CodeSearchResult](items=items), summary=f"{len(items)} match(es) for '{params.query}'")


@chat.function(
    "list_releases",
    description="List releases (tags) published on a GitHub repository, newest first.",
    action_type="read",
    data_model=sdl.EntityList[Release],
)
async def list_releases(ctx, params: ListReleasesParams) -> ActionResult:
    """GET /repos/{owner}/{repo}/releases."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    if not owner or not name:
        return ActionResult.error("repo must be 'owner/repo'.", retryable=False, code=GH_REPO_NOT_ACCESSIBLE)
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/releases", {"per_page": params.limit})
    if resp.status_code != 200:
        return ActionResult.error(github_client.gh_error_message(resp.status_code),
                                  retryable=resp.status_code >= 500, code=GH_REPO_NOT_ACCESSIBLE)
    releases = resp.json()
    items = [
        Release(
            id=str(r["id"]), title=r.get("name") or r.get("tag_name", ""), kind="gh_release",
            tag_name=r.get("tag_name", ""), name=r.get("name") or "",
            draft=r.get("draft", False), prerelease=r.get("prerelease", False),
            published_at=r.get("published_at") or r.get("created_at", ""),
            body=r.get("body") or "", url=r.get("html_url", ""),
        )
        for r in releases
    ]
    return ActionResult.success(sdl.EntityList[Release](items=items), summary=f"{len(items)} release(s)")
