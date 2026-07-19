"""github-connector · P3 read-only Actions tools.

Only `get_workflow_runs` here (P3, read-only status view). The write action
`trigger_workflow_dispatch` is P6 — not built in this pass (per
extensions/github-connector.md §12, the actual "деплой гита" trigger comes
later, once read-only visibility is proven end-to-end).
"""
from imperal_sdk import ActionResult, sdl
from app import chat
from models import WorkflowRunsParams, TriggerWorkflowParams, WorkflowRun, WorkflowDispatchResult
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


@chat.function(
    "trigger_workflow_dispatch",
    description=(
        "Trigger an existing GitHub Actions workflow in a connected repository — the workflow must already "
        "declare a workflow_dispatch trigger (this doesn't create or modify workflows, only runs one that's "
        "already there). This is the 'deploy from chat' path: it runs whatever CI/CD the user already set up."
    ),
    action_type="write",
    chain_callable=True,
    data_model=WorkflowDispatchResult,
    effects=["github.trigger_workflow"],
    event="github-connector-extension.trigger_workflow_dispatch",
)
async def trigger_workflow_dispatch(ctx, params: TriggerWorkflowParams) -> ActionResult:
    """Dispatch a workflow_dispatch event on an existing workflow file/ID."""
    token, err = await _get_token(ctx)
    if err:
        return err

    owner, name = _split_repo(params.repo)
    resp = await github_client.gh_post(
        ctx, token, f"/repos/{owner}/{name}/actions/workflows/{params.workflow}/dispatches",
        json_body={"ref": params.ref, "inputs": params.inputs},
    )
    if resp.status_code >= 400:
        return ActionResult.error(github_client.gh_error_message(resp.status_code), retryable=True)

    return ActionResult.success(
        WorkflowDispatchResult(
            id=params.workflow, title=f"{params.workflow} @ {params.ref}", kind="workflow_dispatch",
            workflow=params.workflow, ref=params.ref,
        ),
        summary=f"Triggered workflow '{params.workflow}' on {params.ref} in {params.repo}.",
    )
