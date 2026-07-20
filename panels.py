"""github-connector · sidebar panel — connection status + repository list.

P1 scope only (per extensions/github-connector.md phase plan): shows whether
GitHub is connected and lists the repositories this installation covers.
The file-browser center panel (P2) and PR/issue panels (P3+) are separate,
later additions — not built in this pass.
"""
from imperal_sdk import ui

from app import ext
import auth
import storage


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
    installation = await storage.get_installation(ctx)

    if not installation:
        install_url = await auth.create_install_url(ctx)
        children = [ui.Empty(message="No GitHub account connected yet.")]
        if install_url:
            children.append(ui.Button(
                "Connect GitHub", icon="Github", variant="primary",
                on_click=ui.Open(install_url),
            ))
        else:
            children.append(ui.Text(
                "GitHub App configuration is incomplete. Contact the extension developer."
            ))
        return ui.Stack(gap=3, children=children)

    repos = installation.get("repositories", [])
    account = installation.get("account_login", "")

    header = ui.Stack(direction="h", gap=2, children=[
        ui.Badge(color="green"),
        ui.Text(f"Connected as {account}" if account else "Connected"),
    ])

    if not repos:
        repo_list = ui.Empty(message="No repositories in this installation.")
    else:
        items = [
            ui.ListItem(id=name, title=name,
                        on_click=ui.Call("__panel__center", view="", repo=name))
            for name in repos
        ]
        repo_list = ui.List(items=items)

    # Both actions reuse the existing storage/flow logic. Switch account
    # re-runs the install flow directly from the panel — same as Connect
    # GitHub above, a panel button needs a concrete ui.Open(url) at render
    # time (a panel ui.Call only shows the toast summary, it does not
    # execute a nested ui= action). GitHub's own install page lets the user
    # pick a different account/org; install_callback replaces the stored
    # record via save_installation_for_user's update-or-create.
    # Disconnect is destructive so it goes through disconnect_github's own
    # confirm=true two-step (KAV confirmation card fires for the panel click
    # too, since action_type="destructive" on that chat.function).
    switch_url = await auth.create_install_url(ctx)
    footer_children = []
    if switch_url:
        footer_children.append(ui.Button(
            "Switch account", icon="RefreshCw", variant="ghost", size="sm",
            on_click=ui.Open(switch_url),
        ))
    footer_children.append(ui.Button(
        "Disconnect", icon="Unlink", variant="ghost", size="sm",
        on_click=ui.Call("disconnect_github", confirm=True),
    ))
    footer = ui.Stack(direction="h", gap=2, children=footer_children)

    return ui.Stack(gap=3, children=[header, ui.Divider(), repo_list, ui.Divider(), footer])
