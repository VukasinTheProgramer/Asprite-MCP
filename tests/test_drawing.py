import pytest
from mcp.client import Client

from aseprite_mcp.server import mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch, aseprite_exe):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


async def test_draw_grid_writes_exact_pixels(workspace, read_pixels):
    grid = "..111..\n.11211.\n1122211\n.11211.\n..111.."
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "face", "width": 7, "height": 5})
        out = await c.call_tool("draw_grid", {"grid": grid})
        assert not out.is_error

    rows = read_pixels(str(workspace / "face.aseprite"), 7, 5)
    assert rows == ["0011100", "0112110", "1122211", "0112110", "0011100"]


async def test_draw_grid_rejects_uneven_rows_without_touching_canvas(workspace, read_pixels):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 5, "height": 5})
        out = await c.call_tool("draw_grid", {"grid": "..1\n.1"})
        assert out.is_error
        assert "grid_rows_uneven" in out.content[0].text

    rows = read_pixels(str(workspace / "s.aseprite"), 5, 5)
    assert rows == ["00000"] * 5  # untouched


async def test_draw_grid_rejects_unknown_char(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 5, "height": 5})
        out = await c.call_tool("draw_grid", {"grid": "Z"})
        assert out.is_error
        assert "unknown_grid_char" in out.content[0].text


async def test_draw_grid_rejects_overhang(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 5, "height": 5})
        out = await c.call_tool("draw_grid", {"grid": "1" * 10})
        assert out.is_error
        assert "out_of_bounds" in out.content[0].text


async def test_draw_shape_line_and_filled_rect(workspace, read_pixels):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "shapes", "width": 10, "height": 10})
        out = await c.call_tool(
            "draw_shape", {"shape": "line", "points": [[0, 0], [4, 4]], "color": 3}
        )
        assert not out.is_error
        out = await c.call_tool(
            "draw_shape",
            {"shape": "rect", "points": [[1, 6], [3, 8]], "color": 5, "filled": True},
        )
        assert not out.is_error

    rows = read_pixels(str(workspace / "shapes.aseprite"), 10, 10)
    assert rows[0][0] == "3" and rows[4][4] == "3"  # diagonal endpoints
    assert rows[6][1:4] == "555" and rows[8][1:4] == "555"  # filled rect interior


async def test_draw_shape_mirror_reflects_across_canvas(workspace, read_pixels):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "m", "width": 10, "height": 10})
        out = await c.call_tool(
            "draw_shape",
            {"shape": "point", "points": [[9, 9]], "color": 7, "mirror": "horizontal"},
        )
        assert not out.is_error

    rows = read_pixels(str(workspace / "m.aseprite"), 10, 10)
    assert rows[9][9] == "7"  # original
    assert rows[9][0] == "7"  # mirrored: x' = width-1-9 = 0


async def test_draw_shape_wrong_point_count_rejected(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "s", "width": 5, "height": 5})
        out = await c.call_tool("draw_shape", {"shape": "line", "points": [[0, 0]], "color": 1})
        assert out.is_error
        assert "wrong_point_count" in out.content[0].text


async def test_fill_floods_from_seed_point(workspace, read_pixels):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "f", "width": 5, "height": 5})
        out = await c.call_tool("fill", {"x": 0, "y": 0, "color": 4})
        assert not out.is_error

    rows = read_pixels(str(workspace / "f.aseprite"), 5, 5)
    assert rows == ["44444"] * 5  # nothing blocks the flood on a blank canvas


async def test_get_sprite_info_reports_real_state(workspace):
    async with Client(mcp) as c:
        await c.call_tool("create_sprite", {"name": "i", "width": 16, "height": 16})
        out = await c.call_tool("get_sprite_info", {})
        assert not out.is_error
        info = out.structured_content
        assert info["width"] == 16
        assert info["height"] == 16
        assert info["color_mode"] == "indexed"
        assert len(info["layers"]) == 1
        assert len(info["frames"]) == 1
