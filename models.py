"""github-connector · pydantic models for chat.function params and sdl.Entity
result types (P1 install-flow + P2 read-only repo browsing)."""
from pydantic import BaseModel, Field
from imperal_sdk import sdl


class _NoParams(BaseModel):
    pass


# ── P2: read-only repo browsing ──────────────────────────────────────────── #

class RepoParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo' — from list_repositories")


class FileContentsParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    path: str = Field(description="File or directory path within the repo, e.g. 'src/app.py'. Empty string = repo root.")
    ref: str = Field(default="", description="Branch, tag, or commit SHA. Empty = the repo's default branch.")


class ListCommitsParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    path: str = Field(default="", description="Optional: only commits touching this file/path")
    limit: int = Field(default=20, ge=1, le=100, description="Max commits to return, 1-100")


class SearchCodeParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    query: str = Field(description="Search terms — GitHub's own code search syntax works, e.g. 'TODO language:python', 'def parse_args path:src'")
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return, 1-100")


class ListReleasesParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    limit: int = Field(default=20, ge=1, le=100, description="Max releases to return, 1-100")


class CreateRepositoryParams(BaseModel):
    name: str = Field(description="New repository name, e.g. 'my-new-project'")
    org: str = Field(default="", description="Create it inside this organization instead of the user's personal account. Empty = personal account.")
    private: bool = Field(default=True, description="Private repository (default) or public")
    description: str = Field(default="", description="Optional short repository description")
    auto_init: bool = Field(default=True, description="Initialize with a README so the repo isn't empty (default true)")


class Repository(sdl.Entity):
    full_name: str = ""
    private: bool = False
    default_branch: str = "main"
    stars: int = 0
    language: str = ""


class FileEntry(sdl.Entity):
    path: str = ""
    entry_type: str = "file"  # "file" | "dir"
    size: int = 0


class FileContent(sdl.Entity):
    path: str = ""
    ref: str = ""
    encoding: str = "utf-8"
    content: str = ""
    size: int = 0
    sha: str = ""


class Commit(sdl.Entity):
    sha: str = ""
    message: str = ""
    author: str = ""
    date: str = ""
    url: str = ""


class Contributor(sdl.Entity):
    login: str = ""
    contributions: int = 0
    avatar_url: str = ""


class CodeSearchResult(sdl.Entity):
    path: str = ""
    repository: str = ""
    score: float = 0.0


class Release(sdl.Entity):
    tag_name: str = ""
    name: str = ""
    draft: bool = False
    prerelease: bool = False
    published_at: str = ""
    body: str = ""


# ── P3: read-only PR / issues / actions ─────────────────────────────────── #

class ListPullsParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    state: str = Field(default="open", description="'open', 'closed', or 'all'")
    limit: int = Field(default=20, ge=1, le=100, description="Max pull requests to return, 1-100")


class GetPullRequestParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Pull request number")


class ListIssuesParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    state: str = Field(default="open", description="'open', 'closed', or 'all'")
    limit: int = Field(default=20, ge=1, le=100, description="Max issues to return, 1-100")


class GetIssueParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Issue number")


class WorkflowRunsParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    limit: int = Field(default=20, ge=1, le=100, description="Max workflow runs to return, 1-100")


class PullRequest(sdl.Entity):
    number: int = 0
    state: str = "open"
    author: str = ""
    base: str = ""
    head: str = ""
    draft: bool = False
    created_at: str = ""
    body: str = ""
    mergeable_state: str = ""
    labels: list[str] = []
    assignees: list[str] = []


class Issue(sdl.Entity):
    number: int = 0
    state: str = "open"
    author: str = ""
    comments: int = 0
    created_at: str = ""
    body: str = ""
    labels: list[str] = []
    assignees: list[str] = []


class WorkflowRun(sdl.Entity):
    run_number: int = 0
    workflow_name: str = ""
    conclusion: str = ""
    branch: str = ""
    event: str = ""
    created_at: str = ""


# ── P4: write tools (non-destructive: create, never merge/close/delete) ──── #

class CreateBranchParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    name: str = Field(description="New branch name, e.g. 'feature/my-change'")
    from_ref: str = Field(default="", description="Branch, tag, or commit SHA to branch from. Empty = the repo's default branch.")


class CreateOrUpdateFileParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    path: str = Field(description="File path within the repo, e.g. 'src/app.py'")
    content: str = Field(description="New full file content (plain text — encoded to base64 internally)")
    message: str = Field(description="Commit message")
    branch: str = Field(description="Branch to commit to, e.g. 'main' or a feature branch")


class CreatePullRequestParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    title: str = Field(description="Pull request title")
    head: str = Field(description="Branch containing the changes")
    base: str = Field(description="Branch to merge into, e.g. 'main'")
    body: str = Field(default="", description="Pull request description")
    draft: bool = Field(default=False, description="Open as a draft pull request")
    labels: list[str] = Field(default_factory=list, description="Label names to apply (must already exist on the repo)")
    assignees: list[str] = Field(default_factory=list, description="GitHub usernames to assign to the pull request")


class CreateIssueParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    title: str = Field(description="Issue title")
    body: str = Field(default="", description="Issue description")
    labels: list[str] = Field(default_factory=list, description="Label names to apply (must already exist on the repo)")
    assignees: list[str] = Field(default_factory=list, description="GitHub usernames to assign to the issue")


class CommentParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Issue or pull request number")
    body: str = Field(description="Comment text (markdown supported)")


class ReviewPullRequestParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Pull request number")
    event: str = Field(description="Review verdict: 'APPROVE', 'REQUEST_CHANGES', or 'COMMENT'")
    body: str = Field(default="", description="Overall review comment (required by GitHub for REQUEST_CHANGES/COMMENT, optional for APPROVE)")


class Review(sdl.Entity):
    author: str = ""
    state: str = ""
    body: str = ""
    submitted_at: str = ""


class Branch(sdl.Entity):
    ref: str = ""
    sha: str = ""


class CommitResult(sdl.Entity):
    sha: str = ""
    branch: str = ""


class Comment(sdl.Entity):
    author: str = ""
    body: str = ""
    created_at: str = ""


# ── P5: destructive tools (merge/close/delete) — own two-step confirm flow ── #

class MergePullRequestParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Pull request number")
    method: str = Field(default="merge", description="'merge', 'squash', or 'rebase'")
    confirm: bool = Field(default=False, description="Set true on a second call to actually merge. First call (default) only previews.")


class CloseParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Issue or pull request number")
    confirm: bool = Field(default=False, description="Set true on a second call to actually close. First call (default) only previews.")


class DeleteBranchParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    branch: str = Field(description="Branch name to delete")
    confirm: bool = Field(default=False, description="Set true on a second call to actually delete. First call (default) only previews.")


class DestructiveActionResult(sdl.Entity):
    action: str = ""
    needs_confirmation: bool = False
    output: str = ""


# ── P6: trigger existing CI/CD (workflow_dispatch) ──────────────────────── #

class TriggerWorkflowParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    workflow: str = Field(description="Workflow file name (e.g. 'deploy.yml') or numeric workflow ID — from get_workflow_runs")
    ref: str = Field(default="main", description="Branch or tag to run the workflow on")
    inputs: dict = Field(default_factory=dict, description="Workflow input parameters, matching the workflow_dispatch inputs it declares")


class WorkflowDispatchResult(sdl.Entity):
    workflow: str = ""
    ref: str = ""


# ── Per-repo webhook notification toggle (§12.2) ────────────────────────── #

class RepoNotificationResult(sdl.Entity):
    repo: str = ""
    enabled: bool = False
