import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


GRID = "..111..\n.11211.\n1122211\n.11211.\n..111.."


async def test_draw_grid_get_region_round_trip_exact(workspace):
    """The build flow's own canary test: exercises the bridge, transactions,
    palette mapping, and coordinates in one assertion."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "face", "width": 7, "height": 5})
        await c.call_tool("draw_grid", {"grid": GRID})
        out = await c.call_tool("get_region_as_grid", {})
        assert not out.is_error
        assert out.structured_content["grid"] == GRID


async def test_get_region_as_grid_partial_region(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "face", "width": 7, "height": 5})
        await c.call_tool("draw_grid", {"grid": GRID})
        out = await c.call_tool(
            "get_region_as_grid", {"x": 1, "y": 1, "width": 5, "height": 3}
        )
        assert out.structured_content["grid"] == "11211\n12221\n11211"


async def test_get_region_as_grid_rejects_oob(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 7, "height": 5})
        out = await c.call_tool(
            "get_region_as_grid", {"x": 0, "y": 0, "width": 100, "height": 100}
        )
        assert out.is_error
        assert "out_of_bounds" in out.content[0].text


async def test_mutating_tool_returns_preview_image_by_default(workspace):
    async with Client(mcp) as c:
        out = await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        assert not out.is_error
        types = [b.type for b in out.content]
        assert "image" in types
        # structured_content must be None once an image is in the result —
        # confirms structured_output=False actually took effect (CLAUDE.md #26)
        assert out.structured_content is None


async def test_preview_false_suppresses_image_block(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 8, "height": 8})
        out = await c.call_tool(
            "draw_grid", {"grid": "11\n11", "preview": False}
        )
        assert not out.is_error
        assert "image" not in [b.type for b in out.content]


async def test_index_zero_round_trips_as_transparent_not_legend_char(workspace):
    """Regression: the default legend maps '0' -> index 0, which collides
    with the transparent sentinel. '.' must always win (M3 finding)."""
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 3, "height": 1})
        out = await c.call_tool("get_region_as_grid", {})
        assert out.structured_content["grid"] == "..."
