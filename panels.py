"""github-connector · sidebar panel — connection status + live repo list.

Per extensions/github-connector.md §12.2 (2026-07-23 second pivot: GitHub
App -> classic OAuth App): there is no more cached "repositories this
installation covers" list (that only existed because a GitHub App installation
carries a fixed repository_selection). A classic OAuth token can reach
anything the account can reach, decided live by GitHub on every call — so the
sidebar now fetches the same `GET /user/repos` list handlers_repos.py's
`list_repositories` tool uses, instead of reading a cached `repositories[]`
field off the old installation record.
"""
from imperal_sdk import ui

from app import ext
import auth
import storage
import github_client
import handlers_repos


@ext.panel(
    "sidebar",
    slot="left",
    title="GitHub",
    default_width=280,
    min_width=200,
    max_width=400,
    refresh="on_event:github-connector.install_connected,github-connector.install_disconnected",
)
async def sidebar(ctx, **kwargs):
    connection = await storage.get_connection(ctx)

    if not connection:
        authorize_url = await auth.create_authorize_url(ctx)
        children = [ui.Empty(message="No GitHub account connected yet.")]
        if authorize_url:
            children.append(ui.Button(
                "Connect GitHub", icon="Github", variant="primary",
                on_click=ui.Open(authorize_url),
            ))
        else:
            children.append(ui.Text(
                "GitHub OAuth App configuration is incomplete. Contact the extension developer."
            ))
        return ui.Stack(gap=3, children=children)

    account = connection.get("account_login", "")

    header = ui.Stack(direction="h", gap=2, children=[
        ui.Badge(color="green"),
        ui.Text(f"Connected as {account}" if account else "Connected"),
    ])

    token, err = await handlers_repos._get_token(ctx)
    if err:
        return ui.Stack(gap=3, children=[
            header,
            ui.Alert(message=err.error or "Could not reach GitHub.", type="error"),
        ])

    resp = await github_client.gh_get(ctx, token, "/user/repos", {
        "per_page": 30, "affiliation": "owner,collaborator,organization_member", "sort": "updated",
    })
    if resp.status_code != 200:
        repo_list = ui.Alert(message=github_client.gh_error_message(resp.status_code), type="error")
    else:
        repos = resp.json()
        if not repos:
            repo_list = ui.Empty(message="No repositories found for this account.")
        else:
            items = [
                ui.ListItem(id=r["full_name"], title=r["full_name"],
                            on_click=ui.Call("__panel__center", view="", repo=r["full_name"]))
                for r in repos
            ]
            repo_list = ui.List(items=items)

    # Disconnect is destructive so it goes through disconnect_github's own
    # confirm=true two-step (KAV confirmation card fires for the panel click
    # too, since action_type="destructive" on that chat.function) — same
    # pattern the pre-pivot panel used.
    disconnect_btn = ui.Button(
        "Disconnect", icon="Unlink", variant="ghost", size="sm",
        on_click=ui.Call("disconnect_github", confirm=True),
    )

    return ui.Stack(gap=3, children=[header, ui.Divider(), repo_list, ui.Divider(), disconnect_btn])
