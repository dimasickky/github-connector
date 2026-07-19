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
