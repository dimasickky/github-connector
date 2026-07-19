"""github-connector · P4 write tools — branches and file commits.

create_branch and create_or_update_file are the two non-destructive write
operations that don't touch anything that already exists (create_branch
only ever adds a new ref; create_or_update_file only ever adds a new commit
on a branch the caller names). Nothing here can merge, close, or delete —
those stay in P5 with their own confirm flow (per extensions/github-connector.md §11).
"""
import base64

from imperal_sdk import ActionResult, sdl
from app import chat
from models import CreateBranchParams, CreateOrUpdateFileParams, Branch, CommitResult
from handlers_repos import _get_token, _split_repo
import github_client


@chat.function(
    "create_branch",
    description="Create a new branch in a connected GitHub repository, from an existing branch/tag/commit (defaults to the repo's default branch).",
    action_type="write",
    data_model=Branch,
    effects=["github.create_branch"],
    event="github-connector-extension.create_branch",
)
async def create_branch(ctx, params: CreateBranchParams) -> ActionResult:
    """Create a new git ref (branch) pointing at from_ref's current commit."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    from_ref = params.from_ref
    if not from_ref:
        repo_resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}")
        if repo_resp.status_code >= 400:
            return ActionResult.error(github_client.gh_error_message(repo_resp.status_code), retryable=True)
        from_ref = repo_resp.json().get("default_branch", "main")

    ref_resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/git/ref/heads/{from_ref}")
    if ref_resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(ref_resp.status_code), retryable=True)
    base_sha = ref_resp.json()["object"]["sha"]

    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/git/refs",
        json_body={"ref": f"refs/heads/{params.name}", "sha": base_sha},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    data = resp.json()
    branch = Branch(id=params.name, title=params.name, kind="branch",
                     ref=data.get("ref", ""), sha=data.get("object", {}).get("sha", ""))
    return ActionResult.success(branch, summary=f"Created branch '{params.name}' in {params.repo}")


@chat.function(
    "create_or_update_file",
    description="Create or update a single file in a connected GitHub repository — this IS a commit (one file per call).",
    action_type="write",
    data_model=CommitResult,
    effects=["github.commit_file"],
    event="github-connector-extension.create_or_update_file",
)
async def create_or_update_file(ctx, params: CreateOrUpdateFileParams) -> ActionResult:
    """Create or update one file on a branch, i.e. make a single-file commit."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)

    existing = await github_client.gh_get(
        ctx, token, f"/repos/{owner}/{name}/contents/{params.path}",
        params={"ref": params.branch},
    )
    body = {
        "message": params.message,
        "content": base64.b64encode(params.content.encode()).decode(),
        "branch": params.branch,
    }
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]

    resp = await github_client.gh_put(
        ctx, token, f"/repos/{owner}/{name}/contents/{params.path}", json_body=body,
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    data = resp.json()
    commit_sha = data.get("commit", {}).get("sha", "")
    result = CommitResult(id=commit_sha, title=params.message, kind="commit",
                          sha=commit_sha, branch=params.branch)
    return ActionResult.success(result, summary=f"Committed '{params.message}' to {params.branch} in {params.repo}")
