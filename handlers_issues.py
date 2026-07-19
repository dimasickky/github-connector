"""github-connector · P3 read-only issue tools.

Only `list_issues` here (P3, read-only). Write actions (create_issue,
comment_on_issue_or_pr, close_issue) are P4/P5 — not built in this pass.

Note: GitHub's `/issues` endpoint also returns pull requests (a PR is an
issue under the hood) — filtered out here via the documented `pull_request`
key so list_issues never shows PRs (list_pull_requests is the dedicated tool
for those).
"""
from imperal_sdk import ActionResult, sdl
from app import chat
from models import ListIssuesParams, CreateIssueParams, CommentParams, Issue, Comment
from handlers_repos import _get_token, _split_repo
import github_client


@chat.function(
    "list_issues",
    description="List issues on a connected GitHub repository (open by default). Pull requests are excluded — use list_pull_requests for those.",
    action_type="read",
    data_model=sdl.EntityList[Issue],
)
async def list_issues(ctx, params: ListIssuesParams) -> ActionResult:
    """List issues on a repo, filtered by state, excluding pull requests."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_get(
        ctx, token, f"/repos/{owner}/{name}/issues",
        params={"state": params.state, "per_page": params.limit},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    issues = [
        Issue(
            id=i["number"], title=i["title"], kind="issue",
            number=i["number"], state=i["state"], author=(i.get("user") or {}).get("login", ""),
            comments=i.get("comments", 0), created_at=i.get("created_at", ""),
            url=i.get("html_url", ""),
        )
        for i in resp.json()
        if "pull_request" not in i
    ]
    return ActionResult.success(
        sdl.EntityList[Issue](items=issues),
        summary=f"{len(issues)} issue(s) ({params.state}) in {params.repo}",
    )


@chat.function(
    "create_issue",
    description="Open a new issue in a connected GitHub repository.",
    action_type="write",
    data_model=Issue,
    effects=["github.create_issue"],
    event="github-connector-extension.create_issue",
)
async def create_issue(ctx, params: CreateIssueParams) -> ActionResult:
    """Open a new issue with a title and optional body."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/issues",
        json_body={"title": params.title, "body": params.body},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    i = resp.json()
    result = Issue(id=i["number"], title=i["title"], kind="issue",
                   number=i["number"], state=i["state"], author=(i.get("user") or {}).get("login", ""),
                   comments=i.get("comments", 0), created_at=i.get("created_at", ""), url=i.get("html_url", ""))
    return ActionResult.success(result, summary=f"Opened issue #{i['number']} in {params.repo}: {params.title}")


@chat.function(
    "comment_on_issue_or_pr",
    description="Add a comment to an existing issue or pull request (GitHub treats PR comments the same way as issue comments).",
    action_type="write",
    data_model=Comment,
    effects=["github.create_comment"],
    event="github-connector-extension.comment_on_issue_or_pr",
)
async def comment_on_issue_or_pr(ctx, params: CommentParams) -> ActionResult:
    """Post a new comment on an issue or pull request by number."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/issues/{params.number}/comments",
        json_body={"body": params.body},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    c = resp.json()
    result = Comment(id=c["id"], title=f"Comment on #{params.number}", kind="comment",
                      author=(c.get("user") or {}).get("login", ""), body=c.get("body", ""),
                      created_at=c.get("created_at", ""), url=c.get("html_url", ""))
    return ActionResult.success(result, summary=f"Commented on #{params.number} in {params.repo}")
