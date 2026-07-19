"""github-connector · P3 read-only pull request tools.

Only `list_pull_requests` here (P3, read-only). Write/destructive PR actions
(create_pull_request, merge_pull_request, close_pull_request) are P4/P5 —
not built in this pass (per extensions/github-connector.md §12).
"""
from imperal_sdk import ActionResult, sdl
from app import chat
from models import ListPullsParams, PullRequest
from handlers_repos import _get_token, _split_repo
import github_client


@chat.function(
    "list_pull_requests",
    description="List pull requests on a connected GitHub repository (open by default).",
    action_type="read",
    data_model=sdl.EntityList[PullRequest],
)
async def list_pull_requests(ctx, params: ListPullsParams) -> ActionResult:
    """List pull requests on a repo, filtered by state."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_get(
        ctx, token, f"/repos/{owner}/{name}/pulls",
        params={"state": params.state, "per_page": params.limit},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    pulls = [
        PullRequest(
            id=pr["number"], title=pr["title"], kind="pull_request",
            number=pr["number"], state=pr["state"], author=(pr.get("user") or {}).get("login", ""),
            base=(pr.get("base") or {}).get("ref", ""), head=(pr.get("head") or {}).get("ref", ""),
            draft=pr.get("draft", False), created_at=pr.get("created_at", ""),
            url=pr.get("html_url", ""),
        )
        for pr in resp.json()
    ]
    return ActionResult.success(
        sdl.EntityList[PullRequest](items=pulls),
        summary=f"{len(pulls)} pull request(s) ({params.state}) in {params.repo}",
    )
