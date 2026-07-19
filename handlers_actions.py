"""github-connector · P3 read-only Actions tools.

Only `get_workflow_runs` here (P3, read-only status view). The write action
`trigger_workflow_dispatch` is P6 — not built in this pass (per
extensions/github-connector.md §12, the actual "деплой гита" trigger comes
later, once read-only visibility is proven end-to-end).
"""
from imperal_sdk import ActionResult, sdl
from app import chat
from models import WorkflowRunsParams, WorkflowRun
from handlers_repos import _get_token, _split_repo
import github_client


@chat.function(
    "get_workflow_runs",
    description="List recent GitHub Actions workflow runs for a connected repository — status, conclusion, and branch of each.",
    action_type="read",
    data_model=sdl.EntityList[WorkflowRun],
)
async def get_workflow_runs(ctx, params: WorkflowRunsParams) -> ActionResult:
    """List recent GitHub Actions workflow runs for a repo."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_get(
        ctx, token, f"/repos/{owner}/{name}/actions/runs",
        params={"per_page": params.limit},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    runs = [
        WorkflowRun(
            id=r["id"], title=r.get("display_title", r.get("name", "")), kind="workflow_run",
            run_number=r.get("run_number", 0), workflow_name=r.get("name", ""),
            conclusion=r.get("conclusion") or r.get("status", ""),
            branch=r.get("head_branch", ""), event=r.get("event", ""),
            created_at=r.get("created_at", ""), url=r.get("html_url", ""),
        )
        for r in resp.json().get("workflow_runs", [])
    ]
    return ActionResult.success(
        sdl.EntityList[WorkflowRun](items=runs),
        summary=f"{len(runs)} workflow run(s) in {params.repo}",
    )
