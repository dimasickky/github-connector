"""github-connector · center panel — repo file browser + code viewer.

P2 scope: browsing one repo's file tree at a given path/ref, and viewing a
single file's content. Reuses handlers_repos.py's tools directly (no
duplicate GitHub-calling logic) — the panel is a thin renderer over the same
_get_token/gh_get plumbing.
"""
from imperal_sdk import ui

from app import ext
import github_client
import handlers_repos


@ext.panel("center", slot="center", center_overlay=True, title="GitHub Connector")
async def center(ctx, repo="", path="", ref="", **kwargs):
    if not repo:
        return ui.Empty(message="Select a repository from the list to browse its files.")

    token, err = await handlers_repos._get_token(ctx)
    if err:
        return ui.Alert(message=err.error or "Could not reach GitHub.", type="error")

    owner, name = handlers_repos._split_repo(repo)
    q = {"ref": ref} if ref else None
    resp = await github_client.gh_get(ctx, token, f"/repos/{owner}/{name}/contents/{path}", params=q)

    if resp.status_code == 404:
        return ui.Alert(message=f"Path not found: {path or '/'}", type="error")
    if resp.status_code >= 400:
        return ui.Alert(message=github_client.gh_error_message(resp.status_code), type="error")

    data = resp.json()

    breadcrumb = _breadcrumb(repo, path, ref)

    if isinstance(data, list):
        # Directory listing. Tree nodes aren't independently clickable via
        # on_click in the base Tree component — rendered as a List instead
        # so each row can carry its own navigation action.
        items = [
            ui.ListItem(
                id=entry["path"], title=entry["name"] + ("/" if entry["type"] == "dir" else ""),
                icon="Folder" if entry["type"] == "dir" else "File",
                on_click=ui.Call("__panel__center", repo=repo, path=entry["path"], ref=ref),
            )
            for entry in sorted(data, key=lambda e: (e["type"] != "dir", e["name"]))
        ]
        body = ui.List(items=items) if items else ui.Empty(message="This directory is empty.")
        return ui.Page(title=repo, subtitle=path or "/", children=[breadcrumb, body])

    # Single file.
    import base64
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            content = "(binary file — cannot display)"
    language = _guess_language(data.get("name", ""))
    return ui.Page(title=data.get("name", path), subtitle=repo, children=[
        breadcrumb,
        ui.Code(content=content, language=language, line_numbers=True),
    ])


def _breadcrumb(repo: str, path: str, ref: str):
    parts = [p for p in path.split("/") if p]
    crumbs = [ui.Button(repo, variant="ghost", size="sm",
                        on_click=ui.Call("__panel__center", repo=repo, path="", ref=ref))]
    accum = ""
    for part in parts:
        accum = f"{accum}/{part}" if accum else part
        crumbs.append(ui.Text("/", variant="caption"))
        crumbs.append(ui.Button(part, variant="ghost", size="sm",
                                on_click=ui.Call("__panel__center", repo=repo, path=accum, ref=ref)))
    return ui.Stack(direction="h", gap=1, children=crumbs)


_EXT_LANG = {
    "py": "python", "js": "javascript", "ts": "typescript", "tsx": "typescript",
    "jsx": "javascript", "json": "json", "md": "markdown", "yml": "yaml",
    "yaml": "yaml", "html": "html", "css": "css", "sh": "bash", "rb": "ruby",
    "go": "go", "rs": "rust", "java": "java", "php": "php", "sql": "sql",
}


def _guess_language(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _EXT_LANG.get(ext, "")
