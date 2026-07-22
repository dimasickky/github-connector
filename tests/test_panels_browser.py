"""Tests for the P2 center panel (panels_browser.py): file/dir browsing,
and specifically the explicit 'Back' button fixed alongside the breadcrumb
(users look for a dedicated back control, not just clickable breadcrumb
segments — same pattern as sql-db/panels_editor.py and notes/panels.py).
"""
import pytest

from imperal_sdk.testing import MockContext

import panels_browser
from tests.conftest import seed_user_token


async def _seeded_ctx(user_id="user-1"):
    ctx = MockContext(user_id=user_id)
    await seed_user_token(ctx)
    return ctx


@pytest.mark.asyncio
async def test_center_panel_no_repo_shows_empty():
    ctx = await _seeded_ctx()
    result = await panels_browser.center(ctx)
    assert result.to_dict()["type"] == "Empty"


@pytest.mark.asyncio
async def test_center_panel_root_has_no_back_button():
    ctx = await _seeded_ctx()
    ctx.http.mock_get(
        "/repos/octocat/hello-world/contents/",
        [{"name": "src", "path": "src", "type": "dir"}],
    )
    result = await panels_browser.center(ctx, repo="octocat/hello-world", path="")
    payload = result.to_dict()
    assert payload["type"] == "Page"
    # No back control at the repo root — nowhere higher to go.
    button_titles = [
        c.get("props", {}).get("label") for c in payload["props"]["children"]
        if c.get("type") == "Button"
    ]
    assert "Back" not in button_titles


@pytest.mark.asyncio
async def test_center_panel_subdir_has_back_button_to_parent():
    ctx = await _seeded_ctx()
    ctx.http.mock_get(
        "/repos/octocat/hello-world/contents/src/app",
        [{"name": "main.py", "path": "src/app/main.py", "type": "file"}],
    )
    result = await panels_browser.center(ctx, repo="octocat/hello-world", path="src/app")
    payload = result.to_dict()
    back_buttons = [
        c for c in payload["props"]["children"]
        if c.get("type") == "Button" and c.get("props", {}).get("label") == "Back"
    ]
    assert len(back_buttons) == 1
    action = back_buttons[0]["props"]["on_click"]
    assert action["action"] == "call"
    assert action["params"]["path"] == "src"


@pytest.mark.asyncio
async def test_center_panel_file_view_has_back_button_and_code_block():
    ctx = await _seeded_ctx()
    import base64
    ctx.http.mock_get(
        "/repos/octocat/hello-world/contents/app.py",
        {"name": "app.py", "path": "app.py", "type": "file",
         "encoding": "base64", "content": base64.b64encode(b"print('hi')").decode()},
    )
    result = await panels_browser.center(ctx, repo="octocat/hello-world", path="app.py")
    payload = result.to_dict()
    types = [c.get("type") for c in payload["props"]["children"]]
    assert "Code" in types
    back_buttons = [
        c for c in payload["props"]["children"]
        if c.get("type") == "Button" and c.get("props", {}).get("label") == "Back"
    ]
    assert len(back_buttons) == 1
    assert back_buttons[0]["props"]["on_click"]["params"]["path"] == ""


@pytest.mark.asyncio
async def test_center_panel_readme_renders_as_markdown_not_code():
    ctx = await _seeded_ctx()
    import base64
    ctx.http.mock_get(
        "/repos/octocat/hello-world/contents/README.md",
        {"name": "README.md", "path": "README.md", "type": "file",
         "encoding": "base64", "content": base64.b64encode(b"# Hello\n\nSome **bold** text.").decode()},
    )
    result = await panels_browser.center(ctx, repo="octocat/hello-world", path="README.md")
    payload = result.to_dict()
    types = [c.get("type") for c in payload["props"]["children"]]
    assert "Markdown" in types
    assert "Code" not in types
    md_node = next(c for c in payload["props"]["children"] if c.get("type") == "Markdown")
    assert "Some **bold** text." in md_node["props"]["content"]
