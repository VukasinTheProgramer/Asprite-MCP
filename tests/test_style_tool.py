"""A3 wiring, end to end: an active project supplies palette and canvas size,
so the model stops choosing them per sprite."""

import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path / "art"))
    monkeypatch.setenv("ASEPRITE_MCP_STYLES", str(tmp_path / "styles"))
    return tmp_path


def _text(out) -> str:
    return "\n".join(b.text for b in out.content if getattr(b, "type", None) == "text")


async def test_create_then_get_returns_bible_and_a_swatch_image(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        assert not out.is_error, _text(out)

        out = await c.call_tool("style", {"action": "get"})
        assert not out.is_error, _text(out)
        assert "project: dungeon" in _text(out)
        assert "ramps (dark -> light)" in _text(out)
        assert any(getattr(b, "type", None) == "image" for b in out.content)


async def test_create_sprite_takes_canvas_and_palette_from_active_project(workspace):
    async with Client(mcp) as c:
        await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        await c.call_tool(
            "style",
            {"action": "update", "canvas_defaults": {"character": [48, 48], "item": [16, 16]}},
        )

        out = await c.call_tool(
            "create_sprite", {"name": "knight", "asset_type": "character", "preview": False}
        )
        assert not out.is_error, _text(out)
        assert "48x48" in _text(out)
        assert "dungeon" in _text(out)

        info = await c.call_tool("get_sprite_info", {})
        assert info.structured_content["width"] == 48
        assert info.structured_content["palette_size"] == 16


async def test_create_sprite_without_size_or_project_says_what_to_do(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("create_sprite", {"name": "orphan", "preview": False})
        assert out.is_error
        msg = _text(out)
        assert "sprite_size_unresolved" in msg
        assert "asset_type" in msg  # the message names the way out


async def test_explicit_size_still_works_with_no_project(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool(
            "create_sprite", {"name": "plain", "width": 32, "height": 32, "preview": False}
        )
        assert not out.is_error, _text(out)
        assert "32x32" in _text(out)


async def test_set_palette_warns_when_it_diverges_from_the_project(workspace):
    async with Client(mcp) as c:
        await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        await c.call_tool("create_sprite", {"name": "k", "asset_type": "character", "preview": False})

        out = await c.call_tool(
            "set_palette", {"palette": ["#000000", "#ff00ff"], "preview": False}
        )
        assert not out.is_error, _text(out)          # warns, never blocks
        assert "WARNING" in _text(out)
        assert "dungeon" in _text(out)


async def test_on_palette_set_produces_no_warning(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("style", {"action": "create", "project": "d", "palette": "pico8"})
        await c.call_tool("create_sprite", {"name": "k", "asset_type": "character", "preview": False})

        from aseprite_mcp.palettes import load_preset

        out = await c.call_tool(
            "set_palette", {"palette": load_preset("pico8")["colors"], "preview": False}
        )
        assert not out.is_error, _text(out)
        assert "WARNING" not in _text(out)


async def test_creating_a_duplicate_project_is_refused(workspace):
    async with Client(mcp) as c:
        await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        out = await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        assert out.is_error
        assert "style_project_exists" in _text(out)


async def test_set_active_switches_which_project_tools_default_to(workspace):
    async with Client(mcp) as c:
        await c.call_tool("style", {"action": "create", "project": "a", "palette": "db16"})
        await c.call_tool("style", {"action": "create", "project": "b", "palette": "pico8"})
        # create leaves the newest active
        assert "project: b" in _text(await c.call_tool("style", {"action": "get"}))

        await c.call_tool("style", {"action": "set_active", "project": "a"})
        assert "project: a" in _text(await c.call_tool("style", {"action": "get"}))


async def test_set_active_on_an_unknown_project_lists_the_real_ones(workspace):
    async with Client(mcp) as c:
        await c.call_tool("style", {"action": "create", "project": "dungeon", "palette": "db16"})
        out = await c.call_tool("style", {"action": "set_active", "project": "forest"})
        assert out.is_error
        assert "style_project_not_found" in _text(out) and "dungeon" in _text(out)
