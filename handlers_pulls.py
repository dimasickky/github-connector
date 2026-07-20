"""github-connector · P3 read-only pull request tools.

Only `list_pull_requests` here (P3, read-only). Write/destructive PR actions
(create_pull_request, merge_pull_request, close_pull_request) are P4/P5 —
not built in this pass (per extensions/github-connector.md §12).
"""
from imperal_sdk import ActionResult, sdl, ui
from app import chat
from models import ListPullsParams, CreatePullRequestParams, MergePullRequestParams, PullRequest, DestructiveActionResult
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


@chat.function(
    "create_pull_request",
    description="Open a new pull request in a connected GitHub repository (head branch into base branch).",
    action_type="write",
    data_model=PullRequest,
    effects=["github.create_pull_request"],
    event="github-connector-extension.create_pull_request",
)
async def create_pull_request(ctx, params: CreatePullRequestParams) -> ActionResult:
    """Open a pull request from an existing head branch into an existing base branch."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/pulls",
        json_body={"title": params.title, "head": params.head, "base": params.base, "body": params.body},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    pr = resp.json()
    result = PullRequest(
        id=pr["number"], title=pr["title"], kind="pull_request",
        number=pr["number"], state=pr["state"], author=(pr.get("user") or {}).get("login", ""),
        base=(pr.get("base") or {}).get("ref", ""), head=(pr.get("head") or {}).get("ref", ""),
        draft=pr.get("draft", False), created_at=pr.get("created_at", ""), url=pr.get("html_url", ""),
    )
    return ActionResult.success(result, summary=f"Opened PR #{pr['number']} in {params.repo}: {params.head} -> {params.base}")


@chat.function(
    "merge_pull_request",
    description=(
        "Merge an open pull request in a connected GitHub repository. This is irreversible on GitHub's side, "
        "so it requires an explicit confirm=true on a second call — the first call only previews the merge."
    ),
    action_type="destructive",
    data_model=DestructiveActionResult,
    effects=["github.merge_pull_request"],
    event="github-connector-extension.merge_pull_request",
)
async def merge_pull_request(ctx, params: MergePullRequestParams) -> ActionResult:
    """Merge a pull request via merge/squash/rebase.

    Own explicit two-step confirm-flow (same pattern as wp-site-connector's
    manage_plugin): the first call (confirm=false, the default) returns a
    preview and performs no change; the caller must re-call with
    confirm=true to actually merge. This does NOT rely on the platform's
    confirmation gate (account-level, off by default, not controllable by
    an extension — see extensions/github-connector.md §11).
    """
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)

    if not params.confirm:
        await ctx.log(
            f"merge_pull_request: preview only (awaiting confirm) — #{params.number} in {params.repo}",
            level="info",
        )
        diff_text, diff_status = await github_client.gh_get_diff(
            ctx, token, f"/repos/{owner}/{name}/pulls/{params.number}",
        )
        preview_ui = None
        if diff_text:
            MAX_DIFF_CHARS = 6000
            shown = diff_text[:MAX_DIFF_CHARS]
            if len(diff_text) > MAX_DIFF_CHARS:
                shown += f"\n… diff truncated ({len(diff_text)} chars total) — review the full diff on GitHub before confirming."
            preview_ui = ui.Code(shown, language="diff")
        return ActionResult.success(
            DestructiveActionResult(
                id=str(params.number), title=f"PR #{params.number}", kind="pull_request",
                action="merge", needs_confirmation=True,
            ),
            summary=(
                f"This will merge PR #{params.number} in {params.repo} ({params.method}) — irreversible. "
                "Call again with confirm=true to actually merge it."
                + ("" if diff_text else " (Diff preview unavailable — GitHub did not return one.)")
            ),
            ui=preview_ui,
        )

    resp = await github_client.gh_put(
        ctx, token, f"/repos/{owner}/{name}/pulls/{params.number}/merge",
        json_body={"merge_method": params.method},
    )
    if resp.status_code >= 400:
        await ctx.log(f"merge_pull_request: GitHub rejected merge of #{params.number} in {params.repo}", level="error")
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    await ctx.log(f"merge_pull_request: merged #{params.number} in {params.repo}", level="info")
    data = resp.json()
    return ActionResult.success(
        DestructiveActionResult(
            id=str(params.number), title=f"PR #{params.number}", kind="pull_request",
            action="merge", needs_confirmation=False, output=data.get("sha", ""),
        ),
        summary=f"Merged PR #{params.number} in {params.repo}.",
    )
