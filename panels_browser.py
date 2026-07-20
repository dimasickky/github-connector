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
    back_bar = _back_bar(repo, path, ref)

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
        children = [breadcrumb, body] if not back_bar else [back_bar, breadcrumb, body]
        return ui.Page(title=repo, subtitle=path or "/", children=children)

    # Single file.
    import base64
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            content = "(binary file — cannot display)"
    language = github_client.guess_language(data.get("name", ""))
    code_children = [breadcrumb, ui.Code(content=content, language=language, line_numbers=True)]
    if back_bar:
        code_children = [back_bar] + code_children
    return ui.Page(title=data.get("name", path), subtitle=repo, children=code_children)


def _back_bar(repo: str, path: str, ref: str):
    """Explicit 'Back' button one directory level up — the breadcrumb alone
    isn't a strong enough affordance (users look for a dedicated back
    control), matching the ui.Button("Back", icon="ArrowLeft", ...) pattern
    already used by sql-db/panels_editor.py and notes/panels.py. Hidden at
    the repo root — there is nowhere higher to go."""
    if not path:
        return None
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return ui.Button("Back", icon="ArrowLeft", variant="ghost", size="sm",
                      on_click=ui.Call("__panel__center", repo=repo, path=parent, ref=ref))


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
