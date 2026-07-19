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


# ── P3: read-only PR / issues / actions ─────────────────────────────────── #

class ListPullsParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    state: str = Field(default="open", description="'open', 'closed', or 'all'")
    limit: int = Field(default=20, ge=1, le=100, description="Max pull requests to return, 1-100")


class ListIssuesParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    state: str = Field(default="open", description="'open', 'closed', or 'all'")
    limit: int = Field(default=20, ge=1, le=100, description="Max issues to return, 1-100")


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


class Issue(sdl.Entity):
    number: int = 0
    state: str = "open"
    author: str = ""
    comments: int = 0
    created_at: str = ""


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


class CreateIssueParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    title: str = Field(description="Issue title")
    body: str = Field(default="", description="Issue description")


class CommentParams(BaseModel):
    repo: str = Field(description="Repository full name, e.g. 'owner/repo'")
    number: int = Field(description="Issue or pull request number")
    body: str = Field(description="Comment text (markdown supported)")


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
