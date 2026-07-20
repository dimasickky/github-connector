"""github-connector · P3 read-only pull request tools.

Only `list_pull_requests` here (P3, read-only). Write/destructive PR actions
(create_pull_request, merge_pull_request, close_pull_request) are P4/P5 —
not built in this pass (per extensions/github-connector.md §12).
"""
from imperal_sdk import ActionResult, sdl, ui
from app import chat
from models import (
    ListPullsParams, GetPullRequestParams, CreatePullRequestParams,
    MergePullRequestParams, ReviewPullRequestParams,
    PullRequest, Review, DestructiveActionResult,
)
from handlers_repos import _get_token, _split_repo
from error_codes import GH_INVALID_REVIEW_EVENT
import github_client

_VALID_REVIEW_EVENTS = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}


def _pr_from_json(pr: dict) -> PullRequest:
    """Shared PR-JSON -> PullRequest reshape (list/get/create all return the
    same shape from GitHub, aside from list which strips a few detail-only
    fields GitHub itself omits from the list endpoint)."""
    return PullRequest(
        id=pr["number"], title=pr["title"], kind="pull_request",
        number=pr["number"], state=pr["state"], author=(pr.get("user") or {}).get("login", ""),
        base=(pr.get("base") or {}).get("ref", ""), head=(pr.get("head") or {}).get("ref", ""),
        draft=pr.get("draft", False), created_at=pr.get("created_at", ""),
        body=pr.get("body") or "", mergeable_state=pr.get("mergeable_state") or "",
        labels=[l.get("name", "") for l in (pr.get("labels") or [])],
        assignees=[a.get("login", "") for a in (pr.get("assignees") or [])],
        url=pr.get("html_url", ""),
    )


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

    pulls = [_pr_from_json(pr) for pr in resp.json()]
    return ActionResult.success(
        sdl.EntityList[PullRequest](items=pulls),
        summary=f"{len(pulls)} pull request(s) ({params.state}) in {params.repo}",
    )


@chat.function(
    "get_pull_request",
    description="Get full details of a single pull request by number — title, body, state, branches, draft status, labels, assignees, and mergeable_state.",
    action_type="read",
    data_model=PullRequest,
)
async def get_pull_request(ctx, params: GetPullRequestParams) -> ActionResult:
    """GET /repos/{owner}/{repo}/pulls/{number} — the single-PR endpoint,
    unlike the list endpoint, also returns mergeable_state (GitHub computes
    it asynchronously; may be 'unknown' briefly after a push)."""
    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/pulls/{params.number}")
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=resp.status_code >= 500)
    pr = resp.json()
    return ActionResult.success(_pr_from_json(pr), summary=f"PR #{pr['number']} in {params.repo}: {pr['title']} ({pr['state']})")


@chat.function(
    "create_pull_request",
    description="Open a new pull request in a connected GitHub repository (head branch into base branch).",
    action_type="write",
    data_model=PullRequest,
    effects=["github.create_pull_request"],
    event="github-connector-extension.create_pull_request",
)
async def create_pull_request(ctx, params: CreatePullRequestParams) -> ActionResult:
    """Open a pull request from an existing head branch into an existing base branch.

    labels/assignees are applied as a follow-up PATCH to the Issues API (the
    same endpoint create_pull_request's underlying issue uses) — GitHub's
    POST /pulls endpoint itself doesn't accept labels/assignees directly."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/pulls",
        json_body={"title": params.title, "head": params.head, "base": params.base,
                   "body": params.body, "draft": params.draft},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    pr = resp.json()
    if params.labels or params.assignees:
        patch_body = {}
        if params.labels:
            patch_body["labels"] = params.labels
        if params.assignees:
            patch_body["assignees"] = params.assignees
        label_resp = await github_client.gh_patch(
            ctx, token, f"/repos/{owner}/{name}/issues/{pr['number']}", json_body=patch_body,
        )
        if label_resp.status_code < 400:
            pr["labels"] = label_resp.json().get("labels", pr.get("labels", []))
            pr["assignees"] = label_resp.json().get("assignees", pr.get("assignees", []))
        else:
            await ctx.log(
                f"create_pull_request: PR #{pr['number']} opened but labels/assignees PATCH failed "
                f"({label_resp.status_code}) — set them manually.", level="warning",
            )
    result = _pr_from_json(pr)
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
        pr_resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/pulls/{params.number}")
        mergeable_state = ""
        warning = ""
        if pr_resp.status_code < 400:
            try:
                pr_data = pr_resp.json()
            except Exception:
                pr_data = {}
            mergeable_state = pr_data.get("mergeable_state") or ""
            if mergeable_state in ("dirty", "conflicting"):
                warning = f" ⚠️ mergeable_state='{mergeable_state}' — this PR likely has merge conflicts."
            elif mergeable_state == "blocked":
                warning = " ⚠️ mergeable_state='blocked' — a required check/review is not satisfied yet."
            elif mergeable_state == "unstable":
                warning = " ⚠️ mergeable_state='unstable' — non-required checks are failing."
            elif mergeable_state == "unknown":
                warning = " (mergeable_state='unknown' — GitHub is still computing it; try again in a moment if this persists.)"
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
                + warning
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


@chat.function(
    "review_pull_request",
    description=(
        "Submit a formal review on a pull request — APPROVE, REQUEST_CHANGES, or a plain COMMENT. "
        "This is a real GitHub review (POST /pulls/{number}/reviews), distinct from comment_on_issue_or_pr's "
        "plain issue-style comment: it shows up as an actual review verdict with the green/red badge on the PR."
    ),
    action_type="write",
    data_model=Review,
    effects=["github.review_pull_request"],
    event="github-connector-extension.review_pull_request",
)
async def review_pull_request(ctx, params: ReviewPullRequestParams) -> ActionResult:
    """POST /repos/{owner}/{repo}/pulls/{number}/reviews with event=APPROVE|REQUEST_CHANGES|COMMENT.

    GitHub requires a non-empty `body` for REQUEST_CHANGES and COMMENT (APPROVE
    may be silent) — validated here so the caller gets an actionable message
    instead of GitHub's own 422."""
    event = params.event.strip().upper()
    if event not in _VALID_REVIEW_EVENTS:
        return ActionResult.error(
            f"event must be one of {sorted(_VALID_REVIEW_EVENTS)}, got '{params.event}'.",
            retryable=False, code=GH_INVALID_REVIEW_EVENT,
        )
    if event in ("REQUEST_CHANGES", "COMMENT") and not params.body.strip():
        return ActionResult.error(
            f"{event} requires a non-empty body explaining the review.",
            retryable=False, code=GH_INVALID_REVIEW_EVENT,
        )

    token, err = await _get_token(ctx)
    if err:
        return err
    owner, name = _split_repo(params.repo)

    json_body: dict = {"event": event}
    if params.body.strip():
        json_body["body"] = params.body
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/pulls/{params.number}/reviews", json_body=json_body,
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=resp.status_code >= 500)

    review = resp.json()
    verdict = {"APPROVE": "Approved", "REQUEST_CHANGES": "Requested changes on", "COMMENT": "Commented on"}[event]
    result = Review(
        id=str(review.get("id", "")), title=f"Review on PR #{params.number}", kind="pr_review",
        author=(review.get("user") or {}).get("login", ""), state=review.get("state", event),
        body=review.get("body") or "", submitted_at=review.get("submitted_at") or "",
        url=review.get("html_url", ""),
    )
    return ActionResult.success(result, summary=f"{verdict} PR #{params.number} in {params.repo}.")
